/*
 * clipmarks.js - "given a path to a clip, what Shot Marks does it have?"
 *
 * The strategy layer between the byte parser and the host. It decides where to look and
 * in what order, and it takes its filesystem through a `deps` object so the whole policy
 * can be exercised under Node with the real Sony files.
 *
 *   deps = {
 *     openReader(path): Promise<Reader & { dispose?() }>
 *     exists(path): Promise<boolean>
 *   }
 */
(function (root, factory) {
  var mod = factory(root);
  root.ShotMarkClips = mod;
  if (typeof module === "object" && module.exports) module.exports = mod;
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";

  var SM = root.ShotMarks || require("./shotmarks.js");
  var Paths = root.ShotMarkPaths || require("./paths.js");

  /**
   * Order of preference:
   *
   *  - MP4/MOV: the copy embedded in the file first. It is the authoritative one and it
   *    travels with the media, which is the entire point of reading natively rather than
   *    depending on whatever survived the offload. The box walk costs a few KB.
   *  - MXF and anything else: the sidecar first, because we cannot walk the container
   *    cheaply and a blind scan of a large MXF is not worth it.
   *
   * Either way the other source is tried as a fallback, so a clip whose sidecar was left
   * behind on the card still works, and so does one whose metadata box got stripped by a
   * transcode that kept the sidecar.
   */
  function sourceOrder(mediaPath) {
    var ext = Paths.extname(mediaPath).toLowerCase();
    var isoBmff = ext === ".mp4" || ext === ".mov" || ext === ".m4v";
    return isoBmff ? ["embedded", "sidecar"] : ["sidecar", "embedded"];
  }

  async function readWith(deps, path, sourceName) {
    var reader = await deps.openReader(path);
    try {
      return await SM.readShotMarks(reader, sourceName);
    } finally {
      if (reader && typeof reader.dispose === "function") {
        try { await reader.dispose(); } catch (e) { /* nothing useful to do */ }
      }
    }
  }

  async function firstExistingSidecar(deps, mediaPath) {
    var candidates = Paths.sidecarCandidates(mediaPath);
    for (var i = 0; i < candidates.length; i++) {
      if (await deps.exists(candidates[i])) return candidates[i];
    }
    return null;
  }

  /**
   * Returns { clip, from, sidecarPath } or { clip: null, from: null, error }.
   * Never throws for an ordinary "this clip has no Sony metadata" - that is a normal
   * answer for non-Sony footage sitting in the same project.
   */
  async function readClipMarks(mediaPath, deps) {
    var result = { clip: null, from: null, sidecarPath: null, error: null };
    if (!mediaPath) { result.error = "no media path"; return result; }
    if (!Paths.isSupportedMedia(mediaPath)) { result.error = "unsupported file type"; return result; }

    var order = sourceOrder(mediaPath);
    var lastError = null;

    for (var i = 0; i < order.length; i++) {
      try {
        if (order[i] === "embedded") {
          var clip = await readWith(deps, mediaPath, Paths.basename(mediaPath));
          if (clip) { result.clip = clip; result.from = "embedded"; return result; }
        } else {
          var side = await firstExistingSidecar(deps, mediaPath);
          if (side) {
            var sclip = await readWith(deps, side, Paths.basename(side));
            if (sclip) {
              result.clip = sclip;
              result.from = "sidecar";
              result.sidecarPath = side;
              return result;
            }
          }
        }
      } catch (e) {
        lastError = (e && e.message) ? e.message : String(e);
      }
    }

    result.error = lastError;                    // null when the file simply has no marks
    return result;
  }

  /** One-line description of where the marks came from, for the panel's result rows. */
  function describeSource(result) {
    if (!result || !result.clip) return "";
    if (result.from === "sidecar") return "sidecar " + Paths.basename(result.sidecarPath || "");
    var via = result.clip.metadataSource || "embedded";
    return via.indexOf("embedded:") === 0 ? "embedded (" + via.slice(9) + " box)" : via;
  }

  return {
    readClipMarks: readClipMarks,
    sourceOrder: sourceOrder,
    firstExistingSidecar: firstExistingSidecar,
    describeSource: describeSource
  };
});
