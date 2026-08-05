/*
 * paths.js - path handling that has to behave identically on macOS and Windows.
 *
 * Premiere's getMediaFilePath() hands back a native path: "/Volumes/Card/C0001.MP4" on
 * macOS, "D:\Card\C0001.MP4" on Windows (and "\\\\nas\\share\\C0001.MP4" for a UNC share).
 * UXP's fs wants forward slashes and a file: scheme. Everything that has to know the
 * difference between those two worlds lives in this file so it can be unit-tested without
 * a Windows machine in the loop.
 */
(function (root, factory) {
  var mod = factory(root);
  root.ShotMarkPaths = mod;
  if (typeof module === "object" && module.exports) module.exports = mod;
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";

  /** Backslashes -> forward slashes, collapsing runs but preserving a UNC "//" prefix. */
  function normalizeSeparators(p) {
    if (!p) return "";
    var unc = /^[\\/]{2}[^\\/]/.test(p);
    var s = String(p).replace(/\\/g, "/").replace(/\/{2,}/g, "/");
    return unc ? "/" + s : s;
  }

  function isWindowsPath(p) {
    return /^[A-Za-z]:[\\/]/.test(p || "") || /^[\\/]{2}[^\\/]/.test(p || "");
  }

  function basename(p) {
    var s = normalizeSeparators(p);
    var i = s.lastIndexOf("/");
    return i === -1 ? s : s.slice(i + 1);
  }

  function dirname(p) {
    var s = normalizeSeparators(p);
    var i = s.lastIndexOf("/");
    if (i === -1) return "";
    if (i === 0) return "/";
    return s.slice(0, i);
  }

  function extname(p) {
    var b = basename(p);
    var i = b.lastIndexOf(".");
    return i <= 0 ? "" : b.slice(i);
  }

  /** Filename without its extension. */
  function stem(p) {
    var b = basename(p);
    var i = b.lastIndexOf(".");
    return i <= 0 ? b : b.slice(0, i);
  }

  function join(dir, name) {
    if (!dir) return name;
    return dir.replace(/\/+$/, "") + "/" + name;
  }

  /**
   * UXP's fs takes a "file:" URL with forward slashes; a Windows drive path becomes
   * file:/D:/Card/C0001.MP4. We deliberately do NOT percent-encode: UXP resolves these as
   * plain paths, and encoding a space to %20 makes it fail to find the file.
   */
  function toFileUrl(p) {
    if (!p) return "";
    if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(p) && !/^[A-Za-z]:[\\/]/.test(p)) {
      return p;                                  // already a scheme (file:, plugin:, ...)
    }
    var s = normalizeSeparators(p);
    if (s.charAt(0) !== "/") s = "/" + s;        // "D:/x" -> "/D:/x"
    return "file:" + s;
  }

  var MEDIA_EXTENSIONS = [".mp4", ".mov", ".m4v", ".mxf"];

  function isSupportedMedia(p) {
    var e = extname(p).toLowerCase();
    for (var i = 0; i < MEDIA_EXTENSIONS.length; i++) if (e === MEDIA_EXTENSIONS[i]) return true;
    return false;
  }

  /**
   * Sony writes the same NonRealTimeMeta document as a sidecar next to the clip:
   * C0001.MP4 -> C0001M01.XML. Case varies by body and by how the card was offloaded
   * (and macOS volumes are usually case-insensitive while Linux/NAS shares are not), so
   * we hand back every plausible spelling and let the caller try each.
   */
  function sidecarCandidates(mediaPath) {
    if (!mediaPath) return [];
    var dir = dirname(mediaPath);
    var base = stem(mediaPath);
    var names = [];
    var suffixes = ["M01.XML", "M01.xml", "M01.Xml"];
    for (var i = 0; i < suffixes.length; i++) {
      names.push(base + suffixes[i]);
      names.push(base.toUpperCase() + suffixes[i]);
    }
    names.push(base + ".XML");
    names.push(base + ".xml");

    var seen = {}, out = [];
    for (var j = 0; j < names.length; j++) {
      var full = join(dir, names[j]);
      if (seen[full]) continue;
      seen[full] = true;
      out.push(full);
    }
    return out;
  }

  return {
    normalizeSeparators: normalizeSeparators,
    isWindowsPath: isWindowsPath,
    basename: basename,
    dirname: dirname,
    extname: extname,
    stem: stem,
    join: join,
    toFileUrl: toFileUrl,
    isSupportedMedia: isSupportedMedia,
    sidecarCandidates: sidecarCandidates,
    MEDIA_EXTENSIONS: MEDIA_EXTENSIONS
  };
});
