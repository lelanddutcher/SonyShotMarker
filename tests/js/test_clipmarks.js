#!/usr/bin/env node
/*
 * test_clipmarks.js — the "where do we look, and in what order" layer.
 *
 * This is the policy the panel runs: embedded metadata first for MP4/MOV because it
 * travels with the file, sidecar first for containers we cannot walk cheaply, and each
 * falling back to the other. Exercised here against real Sony files plus a scratch
 * directory built to stage the fallback cases.
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const assert = require("assert");

const Clips = require("../../plugin/premiere-uxp/src/lib/clipmarks.js");
const P = require("../../plugin/premiere-uxp/src/lib/paths.js");
const { createNodeFileReader } = require("./node-reader.js");

let passed = 0, failed = 0, skipped = 0;
const failures = [];

async function test(name, fn) {
  try { await fn(); passed++; console.log(`  ok   ${name}`); }
  catch (e) { failed++; failures.push([name, e]); console.log(`  FAIL ${name}\n       ${e.message}`); }
}
function skip(name, why) { skipped++; console.log(`  skip ${name}  (${why})`); }

// The deps the panel supplies from UXP, supplied here from Node instead.
const DEPS = {
  async openReader(p) {
    const r = createNodeFileReader(p);
    r.dispose = r.close;
    return r;
  },
  async exists(p) {
    try { return fs.statSync(p).isFile(); } catch (e) { return false; }
  }
};

const REPO = path.resolve(__dirname, "..", "..");

function findSample(name) {
  const dirs = [
    process.env.SHOTMARK_SAMPLE && path.dirname(process.env.SHOTMARK_SAMPLE),
    REPO, path.resolve(REPO, "..", "..", "..")
  ].filter(Boolean);
  for (const d of dirs) { const p = path.join(d, name); if (fs.existsSync(p)) return p; }
  return null;
}

const SIDECAR_XML = `<?xml version="1.0" encoding="UTF-8"?>
<NonRealTimeMeta xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20">
\t<Duration value="300"/>
\t<LtcChangeTable tcFps="25" halfStep="false">
\t\t<LtcChange frameCount="0" value="00000000" status="increment"/>
\t</LtcChangeTable>
\t<KlvPacketTable>
\t\t<KlvPacket key="060E2B34010101050301020A02000000" frameCount="50" lengthValue="0A5F53686F744D61726B31" status="spot"/>
\t\t<KlvPacket key="060E2B34010101050301020A02000000" frameCount="120" lengthValue="0A5F53686F744D61726B32" status="spot"/>
\t</KlvPacketTable>
\t<VideoFormat><VideoFrame captureFps="25p" formatFps="25p"/></VideoFormat>
\t<Device manufacturer="Sony" modelName="ILME-FX3"/>
</NonRealTimeMeta>`;

function u32(n) { return Buffer.from([(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255]); }
function box(type, payload) { return Buffer.concat([u32(payload.length + 8), Buffer.from(type, "ascii"), payload]); }

/** An MP4 with no Sony metadata anywhere in it. */
function plainMp4() {
  return Buffer.concat([
    box("ftyp", Buffer.from("mp42\x00\x00\x00\x00mp42iso6", "ascii")),
    box("mdat", Buffer.alloc(64 * 1024, 0x11)),
    box("moov", Buffer.alloc(256))
  ]);
}

/** An MP4 that does carry the metadata, in a trailing meta box like the A7S III. */
function sonyMp4(xml) {
  return Buffer.concat([
    box("ftyp", Buffer.from("mp42\x00\x00\x00\x00mp42iso6", "ascii")),
    box("mdat", Buffer.alloc(64 * 1024, 0x11)),
    box("moov", Buffer.alloc(256)),
    box("meta", Buffer.concat([Buffer.alloc(32), Buffer.from(xml, "utf8"), Buffer.alloc(8)]))
  ]);
}

async function main() {
  console.log("\nSony Shot Marks — source-selection suite\n");

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "shotmark-clips-"));

  console.log("lookup order");
  await test("MP4 and MOV prefer the copy embedded in the file", () => {
    assert.deepStrictEqual(Clips.sourceOrder("/a/C0001.MP4"), ["embedded", "sidecar"]);
    assert.deepStrictEqual(Clips.sourceOrder("/a/C0001.mov"), ["embedded", "sidecar"]);
    assert.deepStrictEqual(Clips.sourceOrder("D:\\a\\C0001.m4v"), ["embedded", "sidecar"]);
  });

  await test("MXF prefers the sidecar, since the container cannot be walked cheaply", () => {
    assert.deepStrictEqual(Clips.sourceOrder("/a/C0001.MXF"), ["sidecar", "embedded"]);
  });

  console.log("\nfallback behaviour");
  await test("an MP4 with the metadata inside it needs no sidecar", async () => {
    const p = path.join(tmp, "EMBED0001.MP4");
    fs.writeFileSync(p, sonyMp4(SIDECAR_XML));
    const r = await Clips.readClipMarks(p, DEPS);
    assert.ok(r.clip, r.error || "no marks");
    assert.strictEqual(r.from, "embedded");
    assert.strictEqual(r.clip.userMarkCount, 2);
    assert.deepStrictEqual(r.clip.marks.map(m => m.captureFrame), [50, 120]);
    assert.ok(/embedded \(meta box\)/.test(Clips.describeSource(r)), Clips.describeSource(r));
  });

  await test("a stripped MP4 falls back to the M01.XML left beside it", async () => {
    const p = path.join(tmp, "SIDE0002.MP4");
    fs.writeFileSync(p, plainMp4());
    fs.writeFileSync(path.join(tmp, "SIDE0002M01.XML"), SIDECAR_XML);
    const r = await Clips.readClipMarks(p, DEPS);
    assert.ok(r.clip, r.error || "no marks");
    assert.strictEqual(r.from, "sidecar");
    assert.strictEqual(r.clip.userMarkCount, 2);
    assert.ok(/sidecar SIDE0002M01\.XML/.test(Clips.describeSource(r)), Clips.describeSource(r));
  });

  await test("a lowercase sidecar is found too", async () => {
    const p = path.join(tmp, "SIDE0003.MP4");
    fs.writeFileSync(p, plainMp4());
    fs.writeFileSync(path.join(tmp, "SIDE0003M01.xml"), SIDECAR_XML);
    const r = await Clips.readClipMarks(p, DEPS);
    assert.ok(r.clip, r.error || "no marks");
    assert.strictEqual(r.from, "sidecar");
  });

  await test("an MXF with only a sidecar works without scanning the container", async () => {
    const p = path.join(tmp, "MXF0004.MXF");
    fs.writeFileSync(p, Buffer.alloc(256 * 1024, 0x06));   // no metadata anywhere in it
    fs.writeFileSync(path.join(tmp, "MXF0004M01.XML"), SIDECAR_XML);
    const r = await Clips.readClipMarks(p, DEPS);
    assert.ok(r.clip, r.error || "no marks");
    assert.strictEqual(r.from, "sidecar");
    assert.strictEqual(r.clip.model, "ILME-FX3");
  });

  await test("non-Sony footage reports 'no marks', not an error", async () => {
    const p = path.join(tmp, "GOPRO0005.MP4");
    fs.writeFileSync(p, plainMp4());
    const r = await Clips.readClipMarks(p, DEPS);
    assert.strictEqual(r.clip, null);
    assert.strictEqual(r.error, null, "a plain MP4 is a normal outcome, not a failure");
  });

  await test("a project item that is not media is refused up front", async () => {
    const r = await Clips.readClipMarks("/a/b/graphic.psd", DEPS);
    assert.strictEqual(r.clip, null);
    assert.strictEqual(r.error, "unsupported file type");
    const empty = await Clips.readClipMarks("", DEPS);
    assert.strictEqual(empty.error, "no media path");
  });

  await test("a missing file fails softly instead of throwing", async () => {
    const r = await Clips.readClipMarks(path.join(tmp, "NOPE9999.MP4"), DEPS);
    assert.strictEqual(r.clip, null);
  });

  await test("paths with spaces and unicode round-trip", async () => {
    const dir = path.join(tmp, "My Card ✈");
    fs.mkdirSync(dir, { recursive: true });
    const p = path.join(dir, "C 0006.MP4");
    fs.writeFileSync(p, sonyMp4(SIDECAR_XML));
    const r = await Clips.readClipMarks(p, DEPS);
    assert.ok(r.clip, r.error || "no marks");
    assert.strictEqual(r.clip.userMarkCount, 2);
  });

  console.log("\nreal Sony footage");
  const mp4 = process.env.SHOTMARK_SAMPLE || findSample("SIMON7034.MP4");
  if (mp4 && fs.existsSync(mp4)) {
    await test("the real clip resolves through the embedded path", async () => {
      const r = await Clips.readClipMarks(mp4, DEPS);
      assert.ok(r.clip, r.error || "no marks");
      assert.strictEqual(r.from, "embedded");
      assert.strictEqual(r.clip.userMarkCount, 4);
      assert.deepStrictEqual(
        r.clip.marks.filter(m => m.isUserMark).map(m => m.captureFrame),
        [93, 213, 329, 416]
      );
    });

    await test("a copy of the real clip resolves via its sidecar when the meta box is gone", async () => {
      // Take the real clip's head only — no trailing meta box — and pair it with the real
      // sidecar, which is exactly what a partial offload or a transcode leaves behind.
      const xml = findSample("SIMON7034M01.XML");
      if (!xml) { skip("sidecar fallback on real footage", "M01.XML not present"); return; }
      const stub = path.join(tmp, "SIMON7034.MP4");
      const head = Buffer.alloc(1024 * 1024);
      const fd = fs.openSync(mp4, "r");
      fs.readSync(fd, head, 0, head.length, 0);
      fs.closeSync(fd);
      // Truncate to a valid ftyp + a short mdat so the walk succeeds but finds nothing.
      fs.writeFileSync(stub, Buffer.concat([head.subarray(0, 28), box("mdat", Buffer.alloc(4096))]));
      fs.copyFileSync(xml, path.join(tmp, "SIMON7034M01.XML"));

      const r = await Clips.readClipMarks(stub, DEPS);
      assert.ok(r.clip, r.error || "no marks");
      assert.strictEqual(r.from, "sidecar");
      assert.deepStrictEqual(
        r.clip.marks.filter(m => m.isUserMark).map(m => m.captureFrame),
        [93, 213, 329, 416],
        "the sidecar must agree with the embedded copy"
      );
    });
  } else {
    skip("real footage checks", "SIMON7034.MP4 not on this machine");
  }

  fs.rmSync(tmp, { recursive: true, force: true });

  console.log(`\n${passed} passed, ${failed} failed, ${skipped} skipped\n`);
  if (failed) {
    for (const [n, e] of failures) console.error(`FAILED: ${n}\n${e.stack}\n`);
    process.exit(1);
  }
}

main().catch(e => { console.error(e); process.exit(1); });
