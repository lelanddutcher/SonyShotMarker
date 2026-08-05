/*
 * main.js - panel wiring. Reads the controls, drives premiere.js, renders the results.
 *
 * Runs from a <script> in <head>, the way Adobe's Premiere starter template does: UXP has
 * the document ready by then, so top-level querySelector works. Anything that throws
 * during setup is painted into the panel's banner rather than vanishing into a console
 * nobody has open.
 */
/* global document, require, globalThis, window */
(function () {
  "use strict";

  var root = typeof globalThis !== "undefined" ? globalThis : this;

  /**
   * Premiere's marker colour indices, in the order its own marker dialog lists them.
   * Index 0 is Green, which is the default here - index 1 is Red, and a wall of red
   * markers reads as "something is wrong" rather than "here are your keepers".
   */
  var COLORS = [
    { index: 0, name: "Green", hex: "#4fb973" },
    { index: 1, name: "Red", hex: "#e05c5c" },
    { index: 2, name: "Purple", hex: "#a06bd8" },
    { index: 3, name: "Orange", hex: "#e8964b" },
    { index: 4, name: "Yellow", hex: "#e5c84b" },
    { index: 5, name: "White", hex: "#e8e8e8" },
    { index: 6, name: "Blue", hex: "#5b8fe0" },
    { index: 7, name: "Cyan", hex: "#4fc4d6" }
  ];

  var state = { scope: "selection", includeAuto: false, busy: false, color: 0 };
  var Premiere = null;

  function $(id) { return document.getElementById(id); }

  function showBanner(message, kind) {
    var b = $("banner");
    if (!b) return;
    b.textContent = message || "";
    b.className = "banner" + (message ? " visible " + (kind || "bad") : "");
  }

  function loadModule(globalName, path) {
    if (root[globalName]) return root[globalName];
    return require(path);          // if the <script> tags did not run, CommonJS still can
  }

  // -- rendering ----------------------------------------------------------------
  function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }

  function el(tag, className, text) {
    var n = document.createElement(tag);
    if (className) n.className = className;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  var STATUS_CLASS = {
    "applied": "good",
    "already applied": "muted",
    "ready": "good",
    "no marks": "muted",
    "skipped": "muted",
    "error": "bad"
  };

  function renderRows(rows) {
    var box = $("results");
    clear(box);

    var interesting = rows.filter(function (r) {
      return r.status !== "skipped" && !(r.status === "no marks" && !r.clip);
    });
    var hidden = rows.length - interesting.length;

    if (!interesting.length) {
      box.appendChild(el("p", "empty", rows.length
        ? "Nothing with Sony Shot Marks in " + rows.length + " clip" + (rows.length === 1 ? "" : "s") + "."
        : "No clips found. Import some Sony footage first."));
      return;
    }

    interesting.forEach(function (r) {
      var row = el("div", "result");
      var top = el("div", "result-top");
      top.appendChild(el("span", "clip-name", r.name));
      top.appendChild(el("span", "status " + (STATUS_CLASS[r.status] || ""), r.status));
      row.appendChild(top);
      row.appendChild(el("div", "result-detail", r.detail));

      if (r.marks && r.marks.length) {
        var chips = el("div", "chips");
        r.marks.slice(0, 12).forEach(function (m) {
          chips.appendChild(el("span", "chip", m.sourceTimecode));
        });
        if (r.marks.length > 12) chips.appendChild(el("span", "chip more", "+" + (r.marks.length - 12)));
        row.appendChild(chips);
      }
      box.appendChild(row);
    });

    if (hidden > 0) {
      box.appendChild(el("p", "empty",
        hidden + " other item" + (hidden === 1 ? "" : "s") + " skipped (not Sony media)."));
    }
  }

  function renderSummary(summary, mode) {
    var parts = [];
    if (mode === "apply") {
      parts.push(summary.markersAdded
        ? summary.markersAdded + " marker" + (summary.markersAdded === 1 ? "" : "s") +
          " added to " + summary.clipsChanged + " clip" + (summary.clipsChanged === 1 ? "" : "s")
        : "No new markers to add");
      if (summary.alreadyApplied) parts.push(summary.alreadyApplied + " already up to date");
    } else {
      parts.push(summary.withMarks + " of " + summary.considered + " clip" +
                 (summary.considered === 1 ? "" : "s") + " have Shot Marks");
    }
    if (summary.errors) parts.push(summary.errors + " error" + (summary.errors === 1 ? "" : "s"));
    if (summary.usedFallback) parts.push("nothing was selected, so the whole project was scanned");
    $("summary").textContent = parts.join(" - ");
  }

  function setBusy(busy, label) {
    state.busy = busy;
    // The controls are divs, so "disabled" is a class plus the guard at the top of go().
    $("applyBtn").className = "fill fill-primary" + (busy ? " fill-disabled" : "");
    $("scanBtn").className = "fill fill-ghost" + (busy ? " fill-disabled" : "");
    $("progress").className = "progress" + (busy ? " visible" : "");
    if (label) $("summary").textContent = label;
  }

  function setProgress(done, total) {
    $("progressBar").style.width = (total ? Math.round((done / total) * 100) : 0) + "%";
  }

  // -- actions ------------------------------------------------------------------
  async function go(mode) {
    if (state.busy) return;
    showBanner("");
    setBusy(true, mode === "apply" ? "Applying..." : "Scanning...");
    setProgress(0, 1);
    try {
      var result = await Premiere.run(
        { scope: state.scope, includeAuto: state.includeAuto, mode: mode, color: state.color },
        function (done, total, name) {
          setProgress(done, total);
          if (name) $("summary").textContent = (mode === "apply" ? "Applying... " : "Reading... ") + name;
        }
      );
      renderRows(result.rows);
      renderSummary(result.summary, mode);
    } catch (e) {
      clear($("results"));
      $("results").appendChild(el("p", "empty", "Nothing was changed."));
      showBanner((e && e.message) ? e.message : String(e), "bad");
      $("summary").textContent = "Failed";
      reportCapabilities();
    } finally {
      setBusy(false);
    }
  }

  /**
   * A missing positional-read API is the one failure that is neither the user's fault nor
   * obvious from the error text, so name it explicitly when a run blows up.
   */
  function reportCapabilities() {
    try {
      var caps = Premiere.capabilities();
      if (!caps.positionalReads) {
        $("results").appendChild(el("p", "empty",
          "This Premiere build's UXP filesystem does not expose positional reads, so large " +
          "clips cannot be inspected in place. Sidecar M01.XML files still work."));
      }
    } catch (e) { /* diagnostics are best-effort */ }
  }

  // -- theme --------------------------------------------------------------------
  function applyTheme(theme) {
    var light = typeof theme === "string" && theme.indexOf("light") !== -1;
    document.body.className = light ? "theme-light" : "theme-dark";
  }

  function initTheme() {
    try {
      if (document.theme && typeof document.theme.getCurrent === "function") {
        applyTheme(document.theme.getCurrent());
        if (document.theme.onUpdated && document.theme.onUpdated.addListener) {
          document.theme.onUpdated.addListener(applyTheme);
        }
        return;
      }
    } catch (e) { /* fall through to the dark default */ }
    applyTheme("dark");
  }

  // -- controls -----------------------------------------------------------------
  function setScope(scope) {
    if (state.busy) return;
    state.scope = scope;
    var on = scope === "selection";
    $("scopeSelection").className = "pill pill-left " + (on ? "pill-on" : "pill-off");
    $("scopeProject").className = "pill pill-right " + (on ? "pill-off" : "pill-on");
  }

  /**
   * Build the colour row once, then just restyle the dots on selection. Each dot is a
   * div whose colour comes from a class (.c0 - .c7): an inline background-color did not
   * render, and a sized empty <button> collapsed to nothing.
   */
  function buildColors() {
    var box = $("colors");
    clear(box);
    COLORS.forEach(function (c) {
      var dot = el("div", "dot c" + c.index + " dot-off");
      dot.id = "dot" + c.index;
      dot.title = c.name;
      dot.addEventListener("click", function () { setColor(c.index); });
      box.appendChild(dot);
    });
  }

  function setColor(index) {
    if (state.busy) return;
    state.color = index;
    COLORS.forEach(function (c) {
      var dot = $("dot" + c.index);
      if (dot) dot.className = "dot c" + c.index + " " + (c.index === index ? "dot-on" : "dot-off");
      if (c.index === index) $("colorName").textContent = c.name;
    });
  }

  function init() {
    initTheme();

    Premiere = loadModule("ShotMarkPremiere", "./premiere.js");

    // Paint the initial control state from `state` rather than trusting the classes in
    // the markup, so the two can never disagree.
    setScope(state.scope);
    buildColors();
    setColor(state.color);

    $("scopeSelection").addEventListener("click", function () { setScope("selection"); });
    $("scopeProject").addEventListener("click", function () { setScope("project"); });

    var auto = $("includeAuto");
    auto.addEventListener("change", function () { state.includeAuto = !!auto.checked; });
    // Some UXP builds do not fire `change` on a checkbox; click is reliable everywhere.
    auto.addEventListener("click", function () { state.includeAuto = !!auto.checked; });

    // Returning the promise is deliberate: the host ignores it, but it lets the test
    // harness await a click instead of racing the async work behind it.
    $("scanBtn").addEventListener("click", function () { return go("scan"); });
    $("applyBtn").addEventListener("click", function () { return go("apply"); });

    // Register the panel entrypoint. Harmless where the host does not need it, required
    // by some builds before a side-loaded panel will show.
    try {
      var entrypoints = require("uxp").entrypoints;
      if (entrypoints && typeof entrypoints.setup === "function") {
        entrypoints.setup({ panels: { shotMarks: { show: function () {} } } });
      }
    } catch (e) { /* not fatal */ }
  }

  var booted = false;
  function boot() {
    if (booted) return;                    // DOMContentLoaded and load can both fire
    if (!$("scanBtn")) return;             // body not parsed yet - wait for the next event
    booted = true;
    try {
      init();
    } catch (e) {
      showBanner("Panel failed to start: " + ((e && e.message) ? e.message : String(e)), "bad");
      try { console.error(e); } catch (_) { /* no console in some hosts */ }
    }
  }

  // UXP runs head scripts with the document already available, which is why this works at
  // all; the listeners are belt and braces for a host that parses in the other order.
  boot();
  if (!booted && typeof document !== "undefined" && document.addEventListener) {
    document.addEventListener("DOMContentLoaded", boot);
    if (typeof window !== "undefined" && window.addEventListener) window.addEventListener("load", boot);
  }
})();
