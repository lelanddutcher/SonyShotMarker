/*
 * shotmarks.js - read Sony "Shot Marks" out of a media file, without modifying it.
 *
 * This is a straight port of tools/sony_shotmark.py's validated parser (same KLV key,
 * same packed-LTC decode, same fps table, same drop-frame arithmetic), rewritten to run
 * on Adobe UXP's JavaScript engine. It has NO UXP dependency of its own: everything it
 * reads comes through a small Reader interface, so the exact same code runs under Node
 * for the test suite and inside Premiere for the panel.
 *
 *   Reader = {
 *     size(): Promise<number>                       // bytes in the file
 *     read(position, length): Promise<Uint8Array>   // positional read, no seeking state
 *   }
 *
 * WHY POSITIONAL READS MATTER: on an A7S III clip the Sony metadata sits in a top-level
 * `meta` box AFTER the media payload - on the 335 MB sample it starts at byte 335,813,843.
 * Slurping the file to find 2.5 KB of XML is a non-starter when a card offload is 100 GB,
 * so we walk the box headers and only ever read the small non-`mdat` boxes.
 */
/*
 * Registered BOTH as a global and as a CommonJS export. UXP resolves relative require()
 * paths differently depending on how a script was loaded, so the panel pulls these in as
 * ordered <script> tags and reads the globals, while the Node test suite requires them.
 * Exporting both ways means neither host has to be right about the other's convention.
 */
(function (root, factory) {
  var mod = factory(root);
  root.ShotMarks = mod;
  if (typeof module === "object" && module.exports) module.exports = mod;
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";

  // SMPTE Universal Label for a comment/essence mark - what Shot Marks ride on.
  var ESSENCE_MARK_KEY = "060E2B34010101050301020A02000000";

  // Premiere's internal time base. Every marker position is ultimately an integer
  // number of these, so we compute in ticks and never let a float near a frame number.
  var TICKS_PER_SECOND = 254016000000;

  var KNOWN_LABELS = {
    _RecStart: "Recording Start (auto)",
    _RecEnd: "Recording End (auto)",
    _ShotMark1: "Shot Mark 1",
    _ShotMark2: "Shot Mark 2"
  };

  var FPS_EXACT = {
    "23.98": [24000, 1001], "23.976": [24000, 1001], "24": [24, 1],
    "25": [25, 1], "29.97": [30000, 1001], "30": [30, 1],
    "47.95": [48000, 1001], "48": [48, 1], "50": [50, 1],
    "59.94": [60000, 1001], "60": [60, 1], "100": [100, 1],
    "119.88": [120000, 1001], "120": [120, 1]
  };

  // How far we are willing to read looking for metadata, and how much of it we accept.
  var WINDOW = 1 << 20;        // 1 MiB scan window
  var WINDOW_OVERLAP = 64;     // enough to catch a tag split across two windows
  var MAX_DOC = 8 << 20;       // a NonRealTimeMeta doc is ~2.5 KB; 8 MB is a sanity cap
  var NON_BMFF_PROBE = 32 << 20; // head/tail bytes we scan in a container we can't walk

  // -----------------------------------------------------------------------------
  // byte helpers (no Buffer, no Node built-ins - UXP gives us plain typed arrays)
  // -----------------------------------------------------------------------------
  function asciiBytes(s) {
    var out = new Uint8Array(s.length);
    for (var i = 0; i < s.length; i++) out[i] = s.charCodeAt(i) & 0xff;
    return out;
  }

  function indexOfBytes(haystack, needle, from) {
    var n = needle.length, limit = haystack.length - n;
    if (n === 0) return from | 0;
    var first = needle[0];
    for (var i = from | 0; i <= limit; i++) {
      if (haystack[i] !== first) continue;
      var j = 1;
      while (j < n && haystack[i + j] === needle[j]) j++;
      if (j === n) return i;
    }
    return -1;
  }

  function readU32(b, o) {
    return ((b[o] << 24) >>> 0) + (b[o + 1] << 16) + (b[o + 2] << 8) + b[o + 3];
  }

  function readU64(b, o) {
    // MP4 large-box sizes. Doubles are exact to 2^53; no real file gets near that.
    return readU32(b, o) * 4294967296 + readU32(b, o + 4);
  }

  function decodeUtf8(bytes) {
    if (typeof TextDecoder !== "undefined") {
      return new TextDecoder("utf-8", { fatal: false }).decode(bytes);
    }
    var s = "";                                  // minimal fallback; NRT is ASCII in practice
    for (var i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return s;
  }

  function concatBytes(chunks, total) {
    var out = new Uint8Array(total), at = 0;
    for (var i = 0; i < chunks.length; i++) { out.set(chunks[i], at); at += chunks[i].length; }
    return out;
  }

  function hexToBytes(hex) {
    var n = hex.length >> 1, out = new Uint8Array(n);
    for (var i = 0; i < n; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
    return out;
  }

  // -----------------------------------------------------------------------------
  // frame rate + timecode  (identical arithmetic to sony_shotmark.py)
  // -----------------------------------------------------------------------------
  function fpsToRational(s) {
    var key = String(s == null ? "" : s).trim().replace(/[pPiI]$/, "");
    if (Object.prototype.hasOwnProperty.call(FPS_EXACT, key)) return FPS_EXACT[key].slice();
    var f = parseFloat(key);
    if (!isFinite(f) || f <= 0) return [30, 1];
    // NTSC-family rates land on *.98 / *.94 / *.97 and are really n*1000/1001.
    return Math.abs(f - Math.round(f)) > 0.01 ? [Math.round(f) * 1000, 1001] : [Math.round(f), 1];
  }

  /**
   * Sony's LtcChange `value` is 4 BCD bytes ordered FF SS MM HH.
   * frames byte: bit7 colour-frame, bit6 drop-frame, bits5-0 BCD frames.
   */
  function decodeLtc(hex) {
    var b = hexToBytes(hex);
    if (b.length !== 4) return { h: 0, m: 0, s: 0, f: 0, drop: false };
    return {
      f: ((b[0] & 0x30) >> 4) * 10 + (b[0] & 0x0f),
      s: ((b[1] & 0x70) >> 4) * 10 + (b[1] & 0x0f),
      m: ((b[2] & 0x70) >> 4) * 10 + (b[2] & 0x0f),
      h: ((b[3] & 0x30) >> 4) * 10 + (b[3] & 0x0f),
      drop: (b[0] & 0x40) !== 0
    };
  }

  function tcToFrames(h, m, s, f, nominal, drop) {
    var total = ((h * 60 + m) * 60 + s) * nominal + f;
    if (drop) {
      // 29.97DF drops 2 frames/min except every 10th; 59.94 drops 4; 119.88 drops 8.
      var dpm = 2 * Math.floor(nominal / 30);
      var tm = h * 60 + m;
      total -= dpm * (tm - Math.floor(tm / 10));
    }
    return total;
  }

  function framesToTc(frames, nominal, drop) {
    if (drop) {
      var dpm = 2 * Math.floor(nominal / 30);
      var f10 = nominal * 600 - dpm * 9;
      var d = Math.floor(frames / f10), mod = frames % f10;
      var fpm = nominal * 60 - dpm;
      frames += dpm * 9 * d + (mod >= dpm ? dpm * Math.floor((mod - dpm) / fpm) : 0);
    }
    var pad = function (n) { return (n < 10 ? "0" : "") + n; };
    return pad(Math.floor(frames / (nominal * 3600)) % 24) + ":" +
           pad(Math.floor(frames / (nominal * 60)) % 60) + ":" +
           pad(Math.floor(frames / nominal) % 60) + (drop ? ";" : ":") +
           pad(frames % nominal);
  }

  /**
   * Exact tick position of a capture frame, as a decimal string.
   *
   * TICKS_PER_SECOND is divisible by every rate Sony writes (30000/1001 -> 8475667200
   * ticks/frame, 120000/1001 -> 2118916800, 25 -> 10160640000 ...), so this is frame-exact
   * with no floating point anywhere in the path. Falls back to rounding if a camera ever
   * reports a rate that does not divide evenly.
   */
  function ticksForFrame(frame, fpsNum, fpsDen) {
    if (typeof BigInt === "function") {
      // BigInt(...) calls rather than 0n literals: a literal is a parse-time feature and
      // would take the whole file down on an engine that lacks it, defeating the fallback.
      var num = BigInt(TICKS_PER_SECOND) * BigInt(fpsDen) * BigInt(frame);
      var den = BigInt(fpsNum);
      var q = num / den, r = num % den;
      if (r * BigInt(2) >= den) q = q + BigInt(1);   // round half up on an odd rate
      return q.toString();
    }
    return String(Math.round(frame * TICKS_PER_SECOND * fpsDen / fpsNum));
  }

  // -----------------------------------------------------------------------------
  // locating the NonRealTimeMeta document inside a file
  // -----------------------------------------------------------------------------
  var NRT_OPEN = asciiBytes("<NonRealTimeMeta");
  var NRT_CLOSE = asciiBytes("</NonRealTimeMeta>");
  var XML_DECL = asciiBytes("<?xml");

  /**
   * Walk the top-level MP4/MOV boxes by reading only their 8/16-byte headers, so a
   * multi-gigabyte `mdat` is seeked past and never read.
   */
  async function walkTopLevelBoxes(reader) {
    var total = await reader.size();
    var boxes = [];
    var pos = 0;
    while (pos + 8 <= total) {
      // Exactly 8 bytes - the box header and nothing more. Reading a speculative 16 would
      // reach into the payload of whatever box follows, which for `mdat` means touching
      // media bytes; the extra read for a 64-bit box is cheap and keeps that promise literal.
      var hdr = await reader.read(pos, 8);
      if (!hdr || hdr.length < 8) break;
      var size = readU32(hdr, 0);
      var type = String.fromCharCode(hdr[4], hdr[5], hdr[6], hdr[7]);
      if (size === 1) {
        if (pos + 16 > total) break;
        var ext = await reader.read(pos + 8, 8);
        if (!ext || ext.length < 8) break;
        size = readU64(ext, 0);
      } else if (size === 0) {
        size = total - pos;                       // "to end of file"
      }
      if (size < 8 || pos + size > total) break;  // malformed / truncated - stop cleanly
      boxes.push({ type: type, offset: pos, size: size });
      pos += size;
    }
    return boxes;
  }

  function looksLikeIsoBmff(boxes) {
    if (!boxes.length) return false;
    for (var i = 0; i < boxes.length && i < 4; i++) {
      var t = boxes[i].type;
      if (t === "ftyp" || t === "moov" || t === "mdat" || t === "wide" || t === "skip") return true;
    }
    return false;
  }

  /** Read forward from `start` until the closing tag, in bounded chunks. */
  async function readDocumentAt(reader, start, hardEnd) {
    var chunks = [], total = 0, pos = start, closeAt = -1;
    while (pos < hardEnd && total < MAX_DOC) {
      var len = Math.min(WINDOW, hardEnd - pos, MAX_DOC - total);
      var buf = await reader.read(pos, len);
      if (!buf || !buf.length) break;
      chunks.push(buf);
      total += buf.length;
      var joined = chunks.length === 1 ? chunks[0] : concatBytes(chunks, total);
      chunks = [joined];
      closeAt = indexOfBytes(joined, NRT_CLOSE, 0);
      if (closeAt !== -1) break;
      pos += buf.length;
    }
    if (closeAt === -1) return null;
    return decodeUtf8(chunks[0].subarray(0, closeAt + NRT_CLOSE.length));
  }

  /** Scan [start, end) in overlapping windows for the opening tag. */
  async function scanRangeForNrt(reader, start, end) {
    var pos = start;
    while (pos < end) {
      var len = Math.min(WINDOW, end - pos);
      var buf = await reader.read(pos, len);
      if (!buf || !buf.length) break;
      var i = indexOfBytes(buf, NRT_OPEN, 0);
      if (i !== -1) return await readDocumentAt(reader, pos + i, end);
      if (buf.length < len) break;
      if (len < WINDOW) break;
      pos += WINDOW - WINDOW_OVERLAP;
    }
    return null;
  }

  /**
   * Find the Sony metadata document in a file, reading as little as possible.
   * Returns { xml, via } where `via` describes how it was found, or null.
   */
  async function findNrt(reader) {
    var total = await reader.size();
    if (!total) return null;

    var boxes = [];
    try { boxes = await walkTopLevelBoxes(reader); } catch (e) { boxes = []; }

    if (looksLikeIsoBmff(boxes)) {
      // Sony puts NonRealTimeMeta in a small top-level box - `meta` at EOF on the A7S III,
      // sometimes inside `moov/udta`. Either way it is never in `mdat`, so skip that.
      for (var i = 0; i < boxes.length; i++) {
        var b = boxes[i];
        if (b.type === "mdat") continue;
        var xml = await scanRangeForNrt(reader, b.offset, b.offset + b.size);
        if (xml) return { xml: xml, via: "embedded:" + b.type };
      }
      return null;
    }

    // Not a box container (MXF, or a bare sidecar we were handed directly). Small files we
    // read whole; big ones get a head+tail probe, which is where MXF keeps its metadata
    // partitions. This is a bounded best-effort - the sidecar path is the reliable one.
    if (total <= NON_BMFF_PROBE) {
      var whole = await scanRangeForNrt(reader, 0, total);
      return whole ? { xml: whole, via: "raw" } : null;
    }
    var head = await scanRangeForNrt(reader, 0, NON_BMFF_PROBE);
    if (head) return { xml: head, via: "raw:head" };
    var tail = await scanRangeForNrt(reader, total - NON_BMFF_PROBE, total);
    return tail ? { xml: tail, via: "raw:tail" } : null;
  }

  // -----------------------------------------------------------------------------
  // parsing the document
  // -----------------------------------------------------------------------------
  function xmlAttr(xml, tag, attr) {
    var re = new RegExp("<" + tag + "[^>]*\\b" + attr + '="([^"]*)"');
    var m = re.exec(xml);
    return m ? m[1] : "";
  }

  function decodeKlvLabel(lengthValueHex) {
    var b = hexToBytes(lengthValueHex);
    if (!b.length) return "";
    var n = Math.min(b[0], b.length - 1);
    var s = "";
    for (var i = 1; i <= n; i++) s += String.fromCharCode(b[i]);
    return s;
  }

  /**
   * Parse a NonRealTimeMeta document into clip info + marks.
   * Deliberately regex-based and namespace-agnostic: Sony ships ver.2.10 through 2.30+
   * and a strict XML parse would break on schema drift that we do not actually care about.
   */
  function parseNrtXml(xml, sourceName) {
    var captureFps = xmlAttr(xml, "VideoFrame", "captureFps") || xmlAttr(xml, "VideoFrame", "formatFps");
    var r = fpsToRational(captureFps || "30");
    var fpsNum = r[0], fpsDen = r[1];
    var capExact = fpsNum / fpsDen;

    var tcFps = parseInt(xmlAttr(xml, "LtcChangeTable", "tcFps") || "30", 10) || 30;

    var m0 = /<LtcChange\b[^>]*frameCount="0"[^>]*value="([0-9A-Fa-f]{8})"/.exec(xml) ||
             /<LtcChange\b[^>]*value="([0-9A-Fa-f]{8})"/.exec(xml);
    var ltc = m0 ? decodeLtc(m0[1]) : { h: 0, m: 0, s: 0, f: 0, drop: false };

    // TC base is NTSC-fractional exactly when the capture rate is - matches sony_shotmark.py.
    var ntsc = Math.abs(capExact - Math.round(capExact)) > 0.01;
    var tcNum = ntsc ? tcFps * 1000 : tcFps;
    var tcDen = ntsc ? 1001 : 1;
    var nominal = Math.round(tcNum / tcDen);
    var startFrames = tcToFrames(ltc.h, ltc.m, ltc.s, ltc.f, nominal, ltc.drop);

    var clip = {
      sourceFile: sourceName || "",
      device: xmlAttr(xml, "Device", "manufacturer"),
      model: xmlAttr(xml, "Device", "modelName"),
      lens: xmlAttr(xml, "Lens", "modelName"),
      captureFps: captureFps,
      fpsNum: fpsNum,
      fpsDen: fpsDen,
      tcFps: tcFps,
      dropFrame: ltc.drop,
      halfStep: xmlAttr(xml, "LtcChangeTable", "halfStep") === "true",
      startTimecode: framesToTc(startFrames, nominal, ltc.drop),
      durationFrames: parseInt(xmlAttr(xml, "Duration", "value") || "0", 10) || 0,
      creationDate: xmlAttr(xml, "CreationDate", "value"),
      marks: [],
      userMarkCount: 0
    };

    var re = /<KlvPacket\b[^>]*key="([0-9A-Fa-f]{32})"[^>]*frameCount="(\d+)"[^>]*lengthValue="([0-9A-Fa-f]+)"(?:[^>]*status="([^"]*)")?/g;
    var km;
    while ((km = re.exec(xml)) !== null) {
      if (km[1].toUpperCase() !== ESSENCE_MARK_KEY) continue;
      var frame = parseInt(km[2], 10);
      var label = decodeKlvLabel(km[3]);
      var elapsed = frame / capExact;
      clip.marks.push({
        label: label,
        friendly: KNOWN_LABELS[label] || label,
        isUserMark: label.indexOf("_ShotMark") === 0,
        captureFrame: frame,
        elapsedSeconds: Math.round(elapsed * 10000) / 10000,
        ticks: ticksForFrame(frame, fpsNum, fpsDen),
        sourceTimecode: framesToTc(startFrames + Math.round(elapsed * tcNum / tcDen), nominal, ltc.drop),
        status: km[4] || ""
      });
    }
    clip.userMarkCount = clip.marks.filter(function (m) { return m.isUserMark; }).length;
    return clip;
  }

  /**
   * Full read path: locate the metadata, parse it, hand back the clip.
   * Purely read-only - nothing in this module ever opens a file for writing.
   */
  async function readShotMarks(reader, sourceName) {
    var found = await findNrt(reader);
    if (!found) return null;
    var clip = parseNrtXml(found.xml, sourceName);
    clip.metadataSource = found.via;
    return clip;
  }

  /** Marks a user would want as markers, honouring the "include auto marks" option. */
  function selectMarks(clip, includeAuto) {
    if (!clip || !clip.marks) return [];
    if (includeAuto) return clip.marks.slice();
    return clip.marks.filter(function (m) { return m.isUserMark; });
  }

  return {
    ESSENCE_MARK_KEY: ESSENCE_MARK_KEY,
    TICKS_PER_SECOND: TICKS_PER_SECOND,
    KNOWN_LABELS: KNOWN_LABELS,
    fpsToRational: fpsToRational,
    decodeLtc: decodeLtc,
    tcToFrames: tcToFrames,
    framesToTc: framesToTc,
    ticksForFrame: ticksForFrame,
    walkTopLevelBoxes: walkTopLevelBoxes,
    looksLikeIsoBmff: looksLikeIsoBmff,
    findNrt: findNrt,
    parseNrtXml: parseNrtXml,
    readShotMarks: readShotMarks,
    selectMarks: selectMarks,
    decodeKlvLabel: decodeKlvLabel,
    indexOfBytes: indexOfBytes
  };
});
