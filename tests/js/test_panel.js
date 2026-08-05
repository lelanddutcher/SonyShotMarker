#!/usr/bin/env node
/*
 * test_panel.js — runs the panel end to end without Premiere.
 *
 * This exists because of a real bug: the first cut of the panel put its <script> tags at
 * the end of <body> and hung click handlers on <div>s, so it rendered perfectly and did
 * absolutely nothing. Nothing in the parser suite could have caught that.
 *
 * So this harness loads index.html for real — the same script tags, in the same order —
 * into a minimal DOM, stubs `require("uxp")`, `require("fs")` and `require("premierepro")`
 * with UXP-shaped fakes, and then clicks the buttons. The fs stub is backed by Node and
 * insists on UXP's `file:` URL form, so a path-conversion bug fails here too.
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const PLUGIN = path.resolve(__dirname, "..", "..", "plugin", "premiere-uxp");
const REPO = path.resolve(__dirname, "..", "..");

let passed = 0, failed = 0, skipped = 0;
const failures = [];

async function test(name, fn) {
  try { await fn(); passed++; console.log(`  ok   ${name}`); }
  catch (e) { failed++; failures.push([name, e]); console.log(`  FAIL ${name}\n       ${e.message}`); }
}
function skip(name, why) { skipped++; console.log(`  skip ${name}  (${why})`); }

// ── a DOM just big enough for the panel ─────────────────────────────────────────
function makeElement(tag, id, registry) {
  const node = {
    tagName: String(tag || "div").toUpperCase(),
    id: id || "",
    className: "",
    textContent: "",
    title: "",
    style: {},
    checked: false,
    disabled: false,
    children: [],
    parent: null,
    firstChild: null,
    _listeners: {},
    _registry: registry || null,
    setAttribute(name, value) { this[name] = value; },
    appendChild(child) {
      child.parent = this;
      this.children.push(child);
      this.firstChild = this.children[0];
      // Elements built at runtime (the colour swatches) have to become findable by id,
      // exactly as they would in a real document.
      const reg = this._registry || child._registry;
      if (reg && child.id) reg[child.id] = child;
      return child;
    },
    removeChild(child) {
      this.children = this.children.filter((c) => c !== child);
      this.firstChild = this.children[0] || null;
      return child;
    },
    addEventListener(type, fn) {
      (this._listeners[type] = this._listeners[type] || []).push(fn);
    },
    async emit(type) {
      for (const fn of this._listeners[type] || []) await fn({ type, target: this });
    },
    /** Concatenated text of this node and everything under it. */
    text() {
      return [this.textContent || ""].concat(this.children.map((c) => c.text())).join(" ").trim();
    }
  };
  return node;
}

/** Build the element table from index.html, so a renamed id breaks this test. */
function domFromHtml(html) {
  const byId = {};
  const idRe = /\bid="([^"]+)"/g;
  // Capture the whole opening tag so the element's starting class attribute carries over -
  // otherwise the harness cannot tell what the panel actually looks like before any click.
  const tagRe = /<(\w+)([^>]*\bid="([^"]+)"[^>]*)>/g;
  let m;
  while ((m = tagRe.exec(html)) !== null) {
    const el = makeElement(m[1], m[3], byId);
    const cls = /\bclass="([^"]*)"/.exec(m[2]);
    if (cls) el.className = cls[1];
    byId[m[3]] = el;
  }
  while ((m = idRe.exec(html)) !== null) if (!byId[m[1]]) byId[m[1]] = makeElement("div", m[1], byId);

  const body = makeElement("body", "", byId);
  const document = {
    body,
    readyState: "complete",
    getElementById: (id) => byId[id] || null,
    createElement: (tag) => makeElement(tag, "", byId),
    addEventListener() {},
    querySelector: (sel) => (sel.charAt(0) === "#" ? byId[sel.slice(1)] || null : null),
    theme: {
      getCurrent: () => "darkest",
      onUpdated: { addListener() {} }
    },
    _byId: byId
  };
  return document;
}

function scriptSrcsFrom(html) {
  const out = [];
  const re = /<script[^>]*\bsrc="([^"]+)"/g;
  let m;
  while ((m = re.exec(html)) !== null) out.push(m[1]);
  return out;
}

// ── UXP-shaped stubs ────────────────────────────────────────────────────────────
/**
 * UXP's fs, implemented over Node's. Deliberately strict: it only accepts the `file:`
 * URL form UXP wants, so a Windows-path or escaping bug in paths.js surfaces here.
 */
function makeUxpFs(log) {
  function nativePath(url) {
    if (typeof url !== "string" || url.indexOf("file:") !== 0) {
      throw new Error("UXP fs expects a file: URL, got " + url);
    }
    if (url.indexOf("%") !== -1) throw new Error("UXP fs does not want percent-encoding: " + url);
    let p = url.slice("file:".length);
    if (/^\/[A-Za-z]:/.test(p)) p = p.slice(1);          // "/D:/x" -> "D:/x"
    return p;
  }
  return {
    async open(url, flags) {
      if (flags !== "r") throw new Error("the panel must never open a file for writing (got " + flags + ")");
      log.opens.push({ url, flags });
      return fs.openSync(nativePath(url), "r");
    },
    async read(fd, buffer, offset, length, position) {
      // Strict on purpose: the real UXP rejects a typed array here with exactly this
      // message, and a lenient mock let that bug reach Premiere once already.
      if (!(buffer instanceof ArrayBuffer)) throw new Error("buffer should be ArrayBuffer");
      log.reads.push({ position, length });
      const buf = Buffer.alloc(length);
      const bytesRead = fs.readSync(fd, buf, 0, length, position);
      new Uint8Array(buffer).set(buf.subarray(0, bytesRead), offset);
      return { bytesRead, buffer };
    },
    async close(fd) { fs.closeSync(fd); },
    async lstat(url) { return { size: fs.statSync(nativePath(url)).size }; },
    async readFile(url) { return new Uint8Array(fs.readFileSync(nativePath(url))); }
  };
}

const TICKS_PER_SECOND = 254016000000;

/** A fake Premiere that records everything the panel asks it to do. */
function makePremiereApi(clips, log) {
  const TYPE_CLIP = 1, TYPE_BIN = 2;
  // Premiere ties an action to the transaction open when it is created; building actions
  // first and only adding them later fails at runtime with "Script action failed to
  // execute.". The mock enforces that, because the lenient version shipped that bug.
  const tx = { open: false };

  function makeMarkerStore() {
    const markers = [];
    const store = {
      getMarkers: () => markers.slice(),
      createAddMarkerAction: (name, type, startTime, duration, comments) => {
        if (!tx.open) throw new Error("Script action failed to execute.");
        return { kind: "add", name, type, ticks: startTime.ticks, comments };
      },
      _commit(action) {
        if (action.kind === "add") {
          markers.push({
            getName: () => action.name,
            getStart: () => ({ ticks: action.ticks }),
            getComments: () => action.comments,
            getType: () => action.type,
              createSetColorByIndexAction: (i) => {
              if (!tx.open) throw new Error("Script action failed to execute.");
              return { kind: "color", ticks: action.ticks, index: i };
            }
          });
          log.added.push({ name: action.name, ticks: action.ticks, comments: action.comments });
        } else if (action.kind === "color") {
          log.colored.push({ ticks: action.ticks, index: action.index });
        }
      },
      _markers: markers
    };
    return store;
  }

  const items = clips.map((c) => {
    const markerStore = makeMarkerStore();
    const item = {
      name: c.name,
      type: c.isBin ? TYPE_BIN : TYPE_CLIP,
      getId: () => c.name,
      _markerStore: markerStore,
      _clipApi: {
        isSequence: async () => !!c.isSequence,
        getMediaFilePath: async () => c.path
      }
    };
    return item;
  });

  const project = {
    name: "marker project testing",
    getRootItem: async () => ({ getItems: async () => items }),
    executeTransaction(callback, undoString) {
      const collected = [];
      tx.open = true;
      try {
        callback({ addAction: (a) => { collected.push(a); return true; }, empty: false });
      } finally {
        tx.open = false;
      }
      log.transactions.push({ undoString, count: collected.length });
      for (const a of collected) {
        for (const it of items) it._markerStore._commit(a);
      }
      return true;
    }
  };

  return {
    Project: { getActiveProject: async () => project },
    ProjectUtils: { getSelection: async () => ({ getItems: async () => items }) },
    ProjectItem: { TYPE_CLIP, TYPE_BIN, cast: (x) => x },
    FolderItem: { cast: (x) => x },
    ClipProjectItem: { cast: (item) => item._clipApi },
    Markers: {
      getMarkers: async (clipApi) => {
        const owner = items.find((i) => i._clipApi === clipApi);
        return owner._markerStore;
      }
    },
    Marker: { MARKER_TYPE_COMMENT: "Comment" },
    TickTime: {
      createWithTicks: (t) => ({ ticks: String(t), seconds: Number(t) / TICKS_PER_SECOND }),
      createWithSeconds: (s) => ({ ticks: String(Math.round(s * TICKS_PER_SECOND)), seconds: s }),
      TIME_ZERO: { ticks: "0", seconds: 0 }
    },
    _project: project,
    _items: items
  };
}

/**
 * Load the panel exactly the way index.html does — same scripts, same order — into a
 * sandbox, and hand back the DOM plus the recorders.
 */
function loadPanel(clips) {
  const html = fs.readFileSync(path.join(PLUGIN, "index.html"), "utf8");
  const document = domFromHtml(html);
  const log = { opens: [], reads: [], added: [], colored: [], transactions: [] };
  const ppro = makePremiereApi(clips, log);
  const uxpFs = makeUxpFs(log);

  const sandbox = {
    document,
    window: { addEventListener() {} },
    console: { log() {}, error() {}, warn() {} },
    setTimeout,
    clearTimeout,
    TextDecoder,
    Uint8Array,
    ArrayBuffer,
    BigInt,
    Math,
    JSON,
    Number,
    String,
    Object,
    Array,
    RegExp,
    Error,
    Promise,
    isFinite,
    parseInt,
    parseFloat
  };
  sandbox.globalThis = sandbox;

  const requireShim = (name) => {
    if (name === "premierepro") return ppro;
    if (name === "fs") return uxpFs;
    if (name === "uxp") return { entrypoints: { setup() {} }, storage: {} };
    // Relative requires resolve against the plugin root, the way UXP does for scripts
    // loaded by index.html. Reaching this at all means a <script> tag failed to run.
    log.relativeRequires = (log.relativeRequires || []).concat(name);
    throw new Error("unexpected require(" + name + ")");
  };
  sandbox.require = requireShim;

  const context = vm.createContext(sandbox);
  const srcs = scriptSrcsFrom(html);
  assert.ok(srcs.length, "index.html declares no scripts");
  for (const src of srcs) {
    const file = path.join(PLUGIN, src);
    assert.ok(fs.existsSync(file), `index.html references a missing script: ${src}`);
    vm.runInContext(fs.readFileSync(file, "utf8"), context, { filename: file });
  }
  return { document, log, ppro, sandbox, scriptSrcs: srcs, html };
}

const SAMPLE = process.env.SHOTMARK_SAMPLE ||
  [path.join(REPO, "SIMON7034.MP4"), path.resolve(REPO, "..", "..", "..", "SIMON7034.MP4")]
    .find((p) => fs.existsSync(p));

// ── the suite ───────────────────────────────────────────────────────────────────
async function main() {
  console.log("\nSony Shot Marks — panel suite\n");

  console.log("panel structure");
  await test("index.html loads its scripts from <head>, in dependency order", () => {
    const html = fs.readFileSync(path.join(PLUGIN, "index.html"), "utf8");
    const head = html.slice(html.indexOf("<head"), html.indexOf("</head>"));
    const srcs = scriptSrcsFrom(html);
    // UXP may never execute a script placed at the end of <body>. This is the bug that
    // shipped a panel where every control was dead, so it is pinned here.
    for (const s of srcs) {
      assert.ok(head.indexOf(s) !== -1, `${s} must be loaded from <head>, not <body>`);
    }
    const order = srcs.map((s) => path.basename(s));
    assert.deepStrictEqual(order, [
      "paths.js", "shotmarks.js", "reader-uxp.js", "clipmarks.js", "premiere.js", "main.js"
    ]);
  });

  await test("styled controls are divs, not native buttons", () => {
    // A UXP <button> draws its own label and does not paint or lay out children, so a
    // styled control built on one renders as a bare native widget: the segmented pills
    // stayed grey through three rounds of fixes and the colour dots were invisible
    // entirely (empty buttons collapse to zero size). Divs paint and receive clicks.
    // Comments stripped first: this file explains the button trap at length, and the
    // prose should not count as markup.
    const html = fs.readFileSync(path.join(PLUGIN, "index.html"), "utf8")
      .replace(/<!--[\s\S]*?-->/g, "");
    for (const id of ["scanBtn", "applyBtn", "scopeSelection", "scopeProject", "colors"]) {
      const re = new RegExp('<div[^>]*\\bid="' + id + '"');
      assert.ok(re.test(html), `#${id} must be a <div>: UXP will not paint a styled <button>`);
    }
    assert.ok(!/<button/.test(html), "no native <button> should remain in the panel");
    assert.ok(/<input[^>]*type="checkbox"[^>]*\bid="includeAuto"/.test(html),
      "#includeAuto stays a real checkbox input, which UXP does render");
  });

  await test("every shipped plugin file is printable ASCII only", () => {
    // UXP's parser rejects bytes that Node happily accepts, and the failure is brutal to
    // diagnose: it reports "SyntaxError: Invalid or unexpected token" at a position that
    // looks completely innocent, the module never defines itself, and every control in the
    // panel goes dead with no visible cause. Two separate bytes have done this here:
    //
    //   * a NUL (0x00) that ended up inside a string literal. JavaScript permits NUL in a
    //     string, so Node parsed the file and the whole suite passed while Premiere refused
    //     to load it. This is why the check covers the LOW bytes, not just the high ones.
    //   * an em dash in a comment, since UXP does not decode sources as UTF-8 and the
    //     multi-byte sequence arrives as raw control bytes.
    //
    // So: nothing outside printable ASCII, tab, LF and CR gets shipped.
    const ALLOWED_CONTROL = new Set([0x09, 0x0a, 0x0d]);
    const offenders = [];
    const walk = (dir) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const p = path.join(dir, entry.name);
        if (entry.isDirectory()) { walk(p); continue; }
        if (!/\.(js|html|css|json)$/.test(entry.name)) continue;
        const bytes = fs.readFileSync(p);
        for (let i = 0; i < bytes.length; i++) {
          const b = bytes[i];
          const printable = b >= 0x20 && b <= 0x7e;
          if (printable || ALLOWED_CONTROL.has(b)) continue;
          const before = bytes.subarray(0, i);
          const line = before.toString("latin1").split("\n").length;
          const col = i - (before.lastIndexOf(0x0a) + 1) + 1;
          offenders.push(
            `${path.relative(PLUGIN, p)}:${line}:${col} byte 0x${b.toString(16).padStart(2, "0")}`);
          break;
        }
      }
    };
    walk(PLUGIN);
    assert.deepStrictEqual(offenders, [],
      "non-printable byte in a shipped plugin file:\n  " + offenders.join("\n  "));
  });

  await test("every id main.js reaches for exists in index.html", () => {
    const html = fs.readFileSync(path.join(PLUGIN, "index.html"), "utf8");
    const main = fs.readFileSync(path.join(PLUGIN, "src", "main.js"), "utf8");
    const ids = new Set();
    let m;
    const re = /\$\("([^"]+)"\)/g;
    while ((m = re.exec(main)) !== null) ids.add(m[1]);
    assert.ok(ids.size >= 6, `expected several ids, found ${ids.size}`);
    for (const id of ids) {
      assert.ok(html.indexOf('id="' + id + '"') !== -1, `main.js uses #${id}, which index.html lacks`);
    }
  });

  console.log("\nwiring");
  await test("loading the panel attaches listeners to every control", () => {
    const { document } = loadPanel([]);
    for (const id of ["scanBtn", "applyBtn", "scopeSelection", "scopeProject", "includeAuto"]) {
      const node = document.getElementById(id);
      assert.ok(node, `#${id} missing from the DOM`);
      const types = Object.keys(node._listeners);
      assert.ok(types.length > 0, `#${id} has no event listeners — the panel would look dead`);
    }
  });

  await test("startup does not paint an error banner", () => {
    const { document } = loadPanel([]);
    const banner = document.getElementById("banner");
    assert.strictEqual(banner.className.indexOf("visible"), -1,
      "panel reported a startup failure: " + banner.textContent);
  });

  await test("the scope toggle visibly moves the fill between segments", async () => {
    // The visible state is a class on the inner span, swapped by main.js - UXP honours
    // neither a background on the native button nor a parent-to-child selector.
    const { document } = loadPanel([]);
    const sel = () => document.getElementById("scopeSelection").className;
    const proj = () => document.getElementById("scopeProject").className;

    assert.ok(sel().indexOf("pill-on") !== -1, "selection should start active: " + sel());
    assert.ok(proj().indexOf("pill-off") !== -1, proj());

    await document.getElementById("scopeProject").emit("click");
    assert.ok(proj().indexOf("pill-on") !== -1, "clicking 'Whole project' must fill it: " + proj());
    assert.ok(sel().indexOf("pill-off") !== -1, sel());

    await document.getElementById("scopeSelection").emit("click");
    assert.ok(sel().indexOf("pill-on") !== -1, sel());
    assert.ok(proj().indexOf("pill-off") !== -1, proj());
  });

  await test("the stylesheet avoids selectors UXP does not honour", () => {
    // UXP ignores the child combinator, so a rule written that way silently does nothing
    // and the control renders as an unstyled native widget. Same for text-transform.
    const css = fs.readFileSync(path.join(PLUGIN, "styles.css"), "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "");            // strip comments first
    const selectors = css.split("}").map((b) => b.split("{")[0]).filter((s) => s.trim());
    const withCombinator = selectors.filter((s) => s.indexOf(">") !== -1);
    assert.deepStrictEqual(withCombinator, [],
      "child combinator is not honoured by UXP:\n  " + withCombinator.join("\n  "));
    assert.strictEqual(css.indexOf("text-transform"), -1,
      "UXP ignores text-transform; write the text as you want it displayed");
  });

  console.log("\nscan and apply, against real footage");
  if (!SAMPLE) {
    skip("scan reports the sample clip's marks", "SIMON7034.MP4 not on this machine");
    skip("apply writes four markers at the verified ticks", "SIMON7034.MP4 not on this machine");
    skip("apply is idempotent", "SIMON7034.MP4 not on this machine");
    skip("the panel only ever opens files read-only", "SIMON7034.MP4 not on this machine");
  } else {
    await test("scan reports the sample clip's marks and changes nothing", async () => {
      const { document, log } = loadPanel([{ name: "SIMON7034.MP4", path: SAMPLE }]);
      await document.getElementById("scanBtn").emit("click");
      const text = document.getElementById("results").text();
      assert.ok(/SIMON7034\.MP4/.test(text), text);
      assert.ok(/4 Shot Marks/.test(text), text);
      assert.ok(/embedded \(meta box\)/.test(text), text);
      assert.strictEqual(log.added.length, 0, "scan must not add markers");
      assert.strictEqual(log.transactions.length, 0, "scan must not open a transaction");
      assert.ok(/1 of 1 clip.*have Shot Marks/.test(document.getElementById("summary").textContent),
        document.getElementById("summary").textContent);
    });

    await test("apply adds four markers at the frame-exact ticks", async () => {
      const { document, log } = loadPanel([{ name: "SIMON7034.MP4", path: SAMPLE }]);
      await document.getElementById("applyBtn").emit("click");
      assert.strictEqual(log.added.length, 4, JSON.stringify(log.added, null, 2));
      // frames 93/213/329/416 at 30000/1001 -> 8475667200 ticks per frame
      assert.deepStrictEqual(
        log.added.map((a) => a.ticks),
        [93, 213, 329, 416].map((f) => String(8475667200 * f))
      );
      assert.deepStrictEqual(log.added.map((a) => a.name),
        ["Shot Mark 1", "Shot Mark 1", "Shot Mark 1", "Shot Mark 1"]);
      assert.ok(/_ShotMark1/.test(log.added[0].comments), log.added[0].comments);
      assert.ok(/20:44:59;27/.test(log.added[0].comments), log.added[0].comments);
      assert.strictEqual(log.transactions[0].undoString, "Add Sony Shot Marks",
        "adds must be one undoable step");
      assert.strictEqual(log.transactions[0].count, 4, "all four adds belong to one transaction");
    });

    await test("apply is idempotent — a second run adds nothing", async () => {
      const { document, log } = loadPanel([{ name: "SIMON7034.MP4", path: SAMPLE }]);
      await document.getElementById("applyBtn").emit("click");
      assert.strictEqual(log.added.length, 4);
      await document.getElementById("applyBtn").emit("click");
      assert.strictEqual(log.added.length, 4, "second run duplicated markers");
      assert.ok(/already/.test(document.getElementById("results").text()),
        document.getElementById("results").text());
    });

    await test("the panel only ever opens files read-only", async () => {
      const { document, log } = loadPanel([{ name: "SIMON7034.MP4", path: SAMPLE }]);
      await document.getElementById("applyBtn").emit("click");
      assert.ok(log.opens.length > 0, "nothing was opened");
      for (const o of log.opens) {
        assert.strictEqual(o.flags, "r", "opened " + o.url + " with " + o.flags);
        assert.ok(o.url.indexOf("file:") === 0, "not a UXP file: URL: " + o.url);
      }
      const bytes = log.reads.reduce((a, r) => a + r.length, 0);
      assert.ok(bytes < 1024 * 1024, `read ${bytes} bytes through the UXP path`);
      console.log(`       ${log.opens.length} read-only open(s), ` +
                  `${log.reads.length} positional reads, ${bytes.toLocaleString()} bytes`);
    });

    await test("automatic Rec Start marks appear only when the box is ticked", async () => {
      const { document, log } = loadPanel([{ name: "SIMON7034.MP4", path: SAMPLE }]);
      document.getElementById("includeAuto").checked = true;
      await document.getElementById("includeAuto").emit("click");
      await document.getElementById("applyBtn").emit("click");
      assert.strictEqual(log.added.length, 5, "expected the _RecStart mark as well");
      assert.strictEqual(log.added[0].ticks, "0");
      assert.ok(/Recording Start/.test(log.added[0].name), log.added[0].name);
    });

    await test("markers are green by default, not red", async () => {
      // Premiere's index 1 is Red. A timeline full of red markers reads as an error
      // state rather than as the takes you liked, so the default is index 0, Green.
      const { document, log } = loadPanel([{ name: "SIMON7034.MP4", path: SAMPLE }]);
      await document.getElementById("applyBtn").emit("click");
      assert.ok(log.colored.length >= 4, "markers were not coloured");
      assert.ok(log.colored.every((c) => c.index === 0),
        "expected every marker green (0): " + JSON.stringify(log.colored));
    });

    await test("picking a swatch changes the colour markers are created with", async () => {
      const { document, log } = loadPanel([{ name: "SIMON7034.MP4", path: SAMPLE }]);
      // dot6 is Blue in the panel's palette. Every swatch must actually exist and be
      // clickable - they were invisible when they were empty <button> elements.
      assert.strictEqual(document.getElementById("colors").children.length, 8,
        "expected all eight Premiere marker colours to be offered");
      const blue = document.getElementById("dot6");
      assert.ok(blue, "the colour row was never built");
      assert.ok(blue.className.indexOf("c6") !== -1, "each dot carries its colour class");
      await blue.emit("click");
      assert.strictEqual(document.getElementById("colorName").textContent, "Blue");
      assert.ok(blue.className.indexOf("dot-on") !== -1, "the chosen dot must show as selected");
      assert.ok(document.getElementById("dot0").className.indexOf("dot-off") !== -1,
        "the previous choice must clear");

      await document.getElementById("applyBtn").emit("click");
      assert.ok(log.colored.length >= 4, "markers were not coloured");
      assert.ok(log.colored.every((c) => c.index === 6),
        "expected every marker blue (6): " + JSON.stringify(log.colored));
    });
  }

  console.log("\nfailure handling");
  await test("a clip with no Sony metadata is reported, not thrown", async () => {
    const tmp = fs.mkdtempSync(path.join(require("os").tmpdir(), "shotmark-panel-"));
    const p = path.join(tmp, "GOPRO.MP4");
    fs.writeFileSync(p, Buffer.concat([
      Buffer.from([0, 0, 0, 16]), Buffer.from("ftyp", "ascii"), Buffer.alloc(8),
      Buffer.from([0, 0, 4, 0]), Buffer.from("mdat", "ascii"), Buffer.alloc(1016)
    ]));
    const { document, log } = loadPanel([{ name: "GOPRO.MP4", path: p }]);
    await document.getElementById("scanBtn").emit("click");
    assert.strictEqual(log.added.length, 0);
    assert.strictEqual(document.getElementById("banner").className.indexOf("visible"), -1,
      "an ordinary non-Sony clip must not raise an error banner");
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  await test("a sequence in the project is skipped without touching the disk", async () => {
    const { document, log } = loadPanel([{ name: "My Sequence", path: "", isSequence: true }]);
    await document.getElementById("scanBtn").emit("click");
    assert.strictEqual(log.opens.length, 0);
    assert.strictEqual(log.added.length, 0);
  });

  console.log(`\n${passed} passed, ${failed} failed, ${skipped} skipped\n`);
  if (failed) {
    for (const [n, e] of failures) console.error(`FAILED: ${n}\n${e.stack}\n`);
    process.exit(1);
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
