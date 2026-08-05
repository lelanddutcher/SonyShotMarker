/*
 * reader-uxp.js - the Reader interface, backed by UXP's filesystem.
 *
 * Everything here is READ-ONLY by construction: files are opened with the "r" flag and
 * the module exposes no write path at all. That is the whole promise of this plugin -
 * your camera originals are never touched, only read.
 *
 * Two backends, tried in order:
 *   1. require("fs")            - has positional reads (fs.read(fd, buf, off, len, pos)),
 *                                 so a 100 GB clip costs a handful of KB to inspect.
 *   2. localFileSystem entries  - whole-file read only. Used for small files (sidecars)
 *                                 and as a fallback if the fs module is unavailable.
 */
(function (root, factory) {
  var mod = factory(root);
  root.ShotMarkReader = mod;
  if (typeof module === "object" && module.exports) module.exports = mod;
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";

  var Paths = root.ShotMarkPaths || require("./paths.js");

  // Above this, refusing is better than trying to hold the file in the panel's heap.
  var WHOLE_FILE_LIMIT = 64 * 1024 * 1024;

  function tryRequire(name) {
    try { return require(name); } catch (e) { return null; }
  }

  var fsMod = null, uxpMod = null;
  function fsModule() {
    if (fsMod === null) fsMod = tryRequire("fs") || false;
    return fsMod || null;
  }
  function uxpStorage() {
    if (uxpMod === null) {
      var u = tryRequire("uxp");
      uxpMod = (u && u.storage) ? u.storage : false;
    }
    return uxpMod || null;
  }

  /** What this host actually gave us - surfaced in the panel so a failure is diagnosable. */
  function capabilities() {
    var fs = fsModule(), st = uxpStorage();
    return {
      fs: !!fs,
      fsOpen: !!(fs && typeof fs.open === "function"),
      fsRead: !!(fs && typeof fs.read === "function"),
      fsClose: !!(fs && typeof fs.close === "function"),
      fsStat: !!(fs && (typeof fs.lstat === "function" || typeof fs.stat === "function")),
      fsReadFile: !!(fs && typeof fs.readFile === "function"),
      localFileSystem: !!(st && st.localFileSystem),
      positionalReads: !!(fs && typeof fs.open === "function" && typeof fs.read === "function")
    };
  }

  function toBytes(value) {
    if (!value) return new Uint8Array(0);
    if (value instanceof Uint8Array) return value;
    if (value.buffer && typeof value.byteLength === "number") {
      return new Uint8Array(value.buffer, value.byteOffset || 0, value.byteLength);
    }
    if (value instanceof ArrayBuffer) return new Uint8Array(value);
    if (typeof value === "string") {                       // a host that handed back text
      var out = new Uint8Array(value.length);
      for (var i = 0; i < value.length; i++) out[i] = value.charCodeAt(i) & 0xff;
      return out;
    }
    return new Uint8Array(0);
  }

  async function statSize(fs, url) {
    var st = null;
    if (typeof fs.lstat === "function") st = await fs.lstat(url);
    else if (typeof fs.stat === "function") st = await fs.stat(url);
    if (!st) return -1;
    var size = (typeof st.size === "number") ? st.size
             : (typeof st.byteLength === "number") ? st.byteLength : -1;
    return size;
  }

  /**
   * Positional reader over UXP's fs. Keeps one descriptor open for the life of the read
   * and closes it in dispose(); callers must always dispose, including on failure.
   */
  function createFsReader(nativePath) {
    var fs = fsModule();
    var url = Paths.toFileUrl(nativePath);
    var fd = null, cachedSize = null;

    return {
      kind: "fs",
      path: nativePath,
      async size() {
        if (cachedSize === null) {
          var s = await statSize(fs, url);
          if (s < 0) throw new Error("could not stat " + nativePath);
          cachedSize = s;
        }
        return cachedSize;
      },
      async read(position, length) {
        if (length <= 0) return new Uint8Array(0);
        if (fd === null) fd = await fs.open(url, "r");
        // UXP wants a raw ArrayBuffer here, not a typed array: hand it a Uint8Array and
        // it rejects the call with "buffer should be ArrayBuffer".
        var ab = new ArrayBuffer(length);
        var res = await fs.read(fd, ab, 0, length, position);
        var n = (res && typeof res.bytesRead === "number") ? res.bytesRead
              : (typeof res === "number") ? res : length;
        var out = toBytes((res && res.buffer) ? res.buffer : ab);
        return n < out.length ? out.subarray(0, n) : out;
      },
      async dispose() {
        if (fd !== null) {
          try { await fs.close(fd); } catch (e) { /* already gone */ }
          fd = null;
        }
      }
    };
  }

  /**
   * Whole-file reader. Only ever used for files small enough to hold comfortably -
   * sidecar XMLs, and small clips on a host without positional reads.
   */
  function createWholeFileReader(nativePath) {
    var url = Paths.toFileUrl(nativePath);
    var bytes = null;

    async function load() {
      if (bytes !== null) return bytes;
      var fs = fsModule();
      if (fs && typeof fs.readFile === "function") {
        bytes = toBytes(await fs.readFile(url));
        return bytes;
      }
      var st = uxpStorage();
      if (st && st.localFileSystem) {
        var entry = await st.localFileSystem.getEntryWithUrl(url);
        var formats = st.formats || {};
        var data = await entry.read(formats.binary ? { format: formats.binary } : undefined);
        bytes = toBytes(data);
        return bytes;
      }
      throw new Error("no filesystem API available to read " + nativePath);
    }

    return {
      kind: "whole-file",
      path: nativePath,
      async size() { return (await load()).length; },
      async read(position, length) {
        var b = await load();
        return b.subarray(position, Math.min(position + length, b.length));
      },
      async dispose() { bytes = null; }
    };
  }

  /**
   * Pick the cheapest safe reader for a path. Positional reads win whenever the host has
   * them; otherwise we fall back to a whole-file read and refuse outright on anything
   * large rather than trying to pull a camera master into a panel's memory.
   */
  async function openReader(nativePath) {
    var caps = capabilities();
    if (caps.positionalReads && caps.fsStat) {
      return createFsReader(nativePath);
    }
    var reader = createWholeFileReader(nativePath);
    var size = await reader.size();
    if (size > WHOLE_FILE_LIMIT) {
      await reader.dispose();
      throw new Error(
        "this Premiere build's UXP filesystem has no positional reads, and " +
        Paths.basename(nativePath) + " is too large to read whole (" +
        Math.round(size / (1024 * 1024)) + " MB). Use the M01.XML sidecar, or the app."
      );
    }
    return reader;
  }

  /** True if a path exists and is readable. Used to pick a sidecar without throwing. */
  async function exists(nativePath) {
    var url = Paths.toFileUrl(nativePath);
    var fs = fsModule();
    try {
      if (fs && (typeof fs.lstat === "function" || typeof fs.stat === "function")) {
        return (await statSize(fs, url)) >= 0;
      }
      var st = uxpStorage();
      if (st && st.localFileSystem) {
        var entry = await st.localFileSystem.getEntryWithUrl(url);
        return !!(entry && entry.isFile);
      }
    } catch (e) { /* missing, or not permitted - same answer either way */ }
    return false;
  }

  return {
    capabilities: capabilities,
    openReader: openReader,
    createFsReader: createFsReader,
    createWholeFileReader: createWholeFileReader,
    exists: exists,
    WHOLE_FILE_LIMIT: WHOLE_FILE_LIMIT
  };
});
