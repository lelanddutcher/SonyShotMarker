/*
 * premiere.js - the only file that talks to Premiere.
 *
 * Everything Premiere-specific is quarantined here so the parsing and policy layers stay
 * host-agnostic and testable. Nothing in this file writes to a media file; the only thing
 * it changes is the Premiere project's own marker list, inside an undoable transaction.
 */
/* global require, module, globalThis */
(function (root, factory) {
  var mod = factory(root);
  root.ShotMarkPremiere = mod;
  if (typeof module === "object" && module.exports) module.exports = mod;
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";

  var ppro = null;
  function api() {
    if (ppro === null) {
      try { ppro = require("premierepro"); }
      catch (e) { throw new Error("This panel needs Premiere Pro's UXP API (Premiere 25.6 or newer)."); }
    }
    return ppro;
  }

  var SM = root.ShotMarks || require("./lib/shotmarks.js");
  var Clips = root.ShotMarkClips || require("./lib/clipmarks.js");
  var Paths = root.ShotMarkPaths || require("./lib/paths.js");
  var UxpFs = root.ShotMarkReader || require("./lib/reader-uxp.js");

  var DEPS = { openReader: UxpFs.openReader, exists: UxpFs.exists };

  // Premiere's marker colour index, chosen in the panel. 0 is Green, which is the
  // default: index 1 is Red, and a timeline full of red markers reads as a problem
  // rather than as the takes you liked.
  var DEFAULT_COLOR = 0;

  async function getProject() {
    var p = await api().Project.getActiveProject();
    if (!p) throw new Error("No project is open in Premiere.");
    return p;
  }

  function isBin(item) {
    var P = api().ProjectItem;
    return item && item.type === P.TYPE_BIN;
  }

  /** Walk a folder tree, flattening to leaf items. Bins nest arbitrarily deep. */
  async function flatten(items, out, seen) {
    var A = api();
    for (var i = 0; i < items.length; i++) {
      var item = items[i];
      if (!item) continue;
      if (isBin(item)) {
        var folder = A.FolderItem.cast(item);
        if (folder) await flatten(await folder.getItems(), out, seen);
        continue;
      }
      var id;
      try { id = item.getId(); } catch (e) { id = null; }
      if (id && seen[id]) continue;
      if (id) seen[id] = true;
      out.push(item);
    }
    return out;
  }

  /**
   * The clips we should consider. `scope` is "selection" or "project"; a selection that
   * contains bins is expanded, and an empty selection quietly falls back to the project
   * so the button never does nothing without saying why.
   */
  async function collectClips(project, scope) {
    var A = api();
    var items = [];
    if (scope === "selection") {
      var sel = await A.ProjectUtils.getSelection(project);
      if (sel) items = await sel.getItems();
    }
    var usedFallback = false;
    if (!items || !items.length) {
      var root = await project.getRootItem();
      items = await root.getItems();
      usedFallback = (scope === "selection");
    }

    var flat = await flatten(items, [], {});
    var clips = [];
    for (var i = 0; i < flat.length; i++) {
      var item = flat[i];
      var clip = null;
      try { clip = A.ClipProjectItem.cast(item); } catch (e) { clip = null; }
      if (!clip) continue;
      try { if (await clip.isSequence()) continue; } catch (e) { /* treat as media */ }
      clips.push({ item: item, clip: clip, name: item.name });
    }
    return { clips: clips, usedFallback: usedFallback };
  }

  async function mediaPathOf(entry) {
    try {
      var p = await entry.clip.getMediaFilePath();
      return p || "";
    } catch (e) {
      return "";
    }
  }

  /**
   * Marker identity for idempotency: same position and same name is the same mark.
   *
   * Both the "what is already on the clip" and the "what do we want to add" sides go
   * through this one function. If those two ever formatted the key differently, the
   * panel would silently duplicate every marker on a second run.
   */
  function signature(ticks, name) {
    return String(ticks) + "|" + String(name);
  }

  function signatureOf(marker) {
    var ticks = "";
    try { ticks = String(marker.getStart().ticks); } catch (e) { ticks = ""; }
    var name = "";
    try { name = marker.getName() || ""; } catch (e) { name = ""; }
    return signature(ticks, name);
  }

  function commentFor(mark, clip) {
    return mark.label + "  |  src TC " + mark.sourceTimecode +
           "  |  " + mark.elapsedSeconds.toFixed(3) + "s" +
           (clip.model ? "  |  " + clip.model : "");
  }

  /**
   * Read one clip's Shot Marks. Pure inspection - nothing is changed, which is what the
   * panel's Scan button runs.
   */
  async function inspect(entry, options) {
    var path = await mediaPathOf(entry);
    var row = {
      name: entry.name, path: path, marks: [], clip: null, source: "",
      status: "", detail: "", ok: false
    };
    if (!path) { row.status = "skipped"; row.detail = "no media path (sequence or offline)"; return row; }
    if (!Paths.isSupportedMedia(path)) {
      row.status = "skipped"; row.detail = "not Sony XAVC media"; return row;
    }

    var res = await Clips.readClipMarks(path, DEPS);
    if (!res.clip) {
      row.status = res.error ? "error" : "no marks";
      row.detail = res.error || "no Sony metadata in this file";
      return row;
    }
    row.clip = res.clip;
    row.source = Clips.describeSource(res);
    row.marks = SM.selectMarks(res.clip, !!options.includeAuto);
    row.ok = true;
    row.status = row.marks.length ? "ready" : "no marks";
    row.detail = row.marks.length
      ? row.marks.length + " Shot Mark" + (row.marks.length === 1 ? "" : "s") + " - " + row.source
      : "clip has Sony metadata but no Shot Marks" + (options.includeAuto ? "" : " (auto marks hidden)");
    return row;
  }

  /**
   * Apply one clip's marks. Adds are batched into a single undoable transaction so the
   * editor gets one Cmd-Z, not one per marker.
   */
  async function applyToClip(project, entry, row, options) {
    var A = api();
    if (!row.ok || !row.marks.length) return row;

    var markers = await A.Markers.getMarkers(entry.clip);
    var existing = {};
    var current = markers.getMarkers() || [];
    for (var i = 0; i < current.length; i++) existing[signatureOf(current[i])] = true;

    var wanted = [];
    for (var j = 0; j < row.marks.length; j++) {
      var m = row.marks[j];
      var name = m.friendly;
      var ticks = m.ticks;
      if (existing[signature(ticks, name)]) continue;
      wanted.push({ mark: m, name: name, ticks: ticks });
    }

    if (!wanted.length) {
      row.status = "already applied";
      row.detail = row.marks.length + " mark" + (row.marks.length === 1 ? "" : "s") +
                   " already on this clip - " + row.source;
      row.added = 0;
      return row;
    }

    // Actions are built INSIDE the transaction callback. Creating them beforehand and
    // only adding them here fails with "Script action failed to execute." - Premiere ties
    // an action to the transaction that is open when it is created.
    var failure = null;
    var ok = false;
    try {
      ok = project.executeTransaction(function (compound) {
        for (var k = 0; k < wanted.length; k++) {
          var w = wanted[k];
          var action = markers.createAddMarkerAction(
            w.name,
            A.Marker.MARKER_TYPE_COMMENT,
            A.TickTime.createWithTicks(w.ticks),
            A.TickTime.TIME_ZERO,
            commentFor(w.mark, row.clip)
          );
          compound.addAction(action);
        }
      }, "Add Sony Shot Marks");
    } catch (e) {
      failure = (e && e.message) ? e.message : String(e);
    }

    if (!ok || failure) {
      row.status = "error";
      row.detail = failure || "Premiere rejected the marker transaction";
      row.added = 0;
      return row;
    }

    row.added = wanted.length;
    row.status = "applied";
    row.detail = "added " + wanted.length + " marker" + (wanted.length === 1 ? "" : "s") +
                 (wanted.length !== row.marks.length ? " (" + (row.marks.length - wanted.length) + " already there)" : "") +
                 " - " + row.source;

    await colorize(project, markers, wanted, options);
    return row;
  }

  /**
   * Second pass: tint the markers we just added. Colour is cosmetic, so a failure here
   * downgrades to "markers are in, just not coloured" rather than failing the run.
   */
  async function colorize(project, markers, wanted, options) {
    try {
      var chosen = (options && typeof options.color === "number") ? options.color : DEFAULT_COLOR;
      var byTicks = {};
      for (var i = 0; i < wanted.length; i++) byTicks[wanted[i].ticks] = chosen;
      var current = markers.getMarkers() || [];
      var targets = [];
      for (var j = 0; j < current.length; j++) {
        var t = String(current[j].getStart().ticks);
        if (!Object.prototype.hasOwnProperty.call(byTicks, t)) continue;
        targets.push({ marker: current[j], index: byTicks[t] });
      }
      if (!targets.length) return;
      // Same rule as the add pass: build the action inside the transaction.
      project.executeTransaction(function (compound) {
        for (var k = 0; k < targets.length; k++) {
          compound.addAction(targets[k].marker.createSetColorByIndexAction(targets[k].index));
        }
      }, "Colour Sony Shot Marks");
    } catch (e) {
      /* markers landed; colour is a nicety */
    }
  }

  /**
   * Scan or apply across a scope, reporting progress per clip so a big bin does not look
   * frozen. `mode` is "scan" or "apply".
   */
  async function run(options, onProgress) {
    var project = await getProject();
    var found = await collectClips(project, options.scope);
    var entries = found.clips;
    var rows = [];
    var summary = {
      projectName: project.name,
      considered: entries.length,
      withMarks: 0,
      markersAdded: 0,
      clipsChanged: 0,
      alreadyApplied: 0,
      errors: 0,
      usedFallback: found.usedFallback
    };

    for (var i = 0; i < entries.length; i++) {
      if (onProgress) onProgress(i, entries.length, entries[i].name);
      var row;
      try {
        row = await inspect(entries[i], options);
        if (options.mode === "apply" && row.ok && row.marks.length) {
          row = await applyToClip(project, entries[i], row, options);
        }
      } catch (e) {
        row = {
          name: entries[i].name, marks: [], ok: false,
          status: "error", detail: (e && e.message) ? e.message : String(e)
        };
      }
      if (row.marks && row.marks.length) summary.withMarks++;
      if (row.status === "error") summary.errors++;
      if (row.status === "applied") { summary.clipsChanged++; summary.markersAdded += (row.added || 0); }
      if (row.status === "already applied") summary.alreadyApplied++;
      rows.push(row);
    }

    if (onProgress) onProgress(entries.length, entries.length, null);
    return { rows: rows, summary: summary };
  }

  return {
    getProject: getProject,
    collectClips: collectClips,
    inspect: inspect,
    run: run,
    capabilities: UxpFs.capabilities
  };
});
