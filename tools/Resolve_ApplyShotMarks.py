#!/usr/bin/env python3
"""
Resolve_ApplyShotMarks - LEGACY console script for DaVinci Resolve.

SUPERSEDED BY plugin/resolve/SonyShotMarker.py, which does the same job with a window,
a colour picker and a Scan-before-you-commit step. This file is kept because it is what
earlier releases shipped, people have it installed and wired into their own workflows,
and it still works. It is not going away.

The difference: this one runs headless the moment you pick it from the Scripts menu and
marks everything, printing to Workspace > Console. No window, no options. If that is what
you want in a batch or an automated setup, it is arguably the better tool.

DEPENDENCIES: none beyond the Python standard library.

INSTALL: copy into Resolve's Scripts/Utility folder:
  macOS : ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/
  Win   : %APPDATA%\\Blackmagic Design\\DaVinci Resolve\\Support\\Fusion\\Scripts\\Utility\\

USE: import your footage, then Workspace > Scripts > Resolve_ApplyShotMarks.

TEST THE READER WITHOUT RESOLVE:
  python3 Resolve_ApplyShotMarks.py /path/to/CLIP.MP4
"""
import os, sys, re, struct

# ShotMark1 -> Blue, ShotMark2 -> Cyan. The panel lets you choose; this one does not, and
# these are the colours earlier releases used, so existing projects stay consistent.
COLOR = {"_ShotMark1": "Blue", "_ShotMark2": "Cyan"}

ESSENCE_MARK_KEY = "060E2B34010101050301020A02000000"   # SMPTE comment/essence-mark UL
FPS_EXACT = {"23.98": (24000, 1001), "23.976": (24000, 1001), "24": (24, 1), "25": (25, 1),
             "29.97": (30000, 1001), "30": (30, 1), "47.95": (48000, 1001), "48": (48, 1),
             "50": (50, 1), "59.94": (60000, 1001), "60": (60, 1), "100": (100, 1),
             "119.88": (120000, 1001), "120": (120, 1)}


def _fps_rational(s):
    key = re.sub(r"[pPiI]$", "", (s or "30").strip())
    if key in FPS_EXACT:
        return FPS_EXACT[key]
    f = float(key)
    return (int(round(f)) * 1000, 1001) if abs(f - round(f)) > 0.01 else (int(round(f)), 1)


def _decode_ltc(hexval):
    """Sony packed LTC: 4 bytes BCD, order FF SS MM HH; frame byte bit6 = drop-frame."""
    ff, ss, mm, hh = bytes.fromhex(hexval)
    drop = bool(ff & 0x40)
    F = ((ff & 0x30) >> 4) * 10 + (ff & 0x0F)
    S = ((ss & 0x70) >> 4) * 10 + (ss & 0x0F)
    M = ((mm & 0x70) >> 4) * 10 + (mm & 0x0F)
    H = ((hh & 0x30) >> 4) * 10 + (hh & 0x0F)
    return H, M, S, F, drop


def _tc_to_frames(h, m, s, f, nominal, drop):
    total = ((h * 60 + m) * 60 + s) * nominal + f
    if drop:
        dpm = 2 * (nominal // 30)
        tm = h * 60 + m
        total -= dpm * (tm - tm // 10)
    return total


def _frames_to_tc(frames, nominal, drop):
    if drop:
        dpm = 2 * (nominal // 30)
        f10 = nominal * 600 - dpm * 9
        d, mod = divmod(frames, f10)
        fpm = nominal * 60 - dpm
        add = dpm * 9 * d + (dpm * ((mod - dpm) // fpm) if mod >= dpm else 0)
        frames += add
    sep = ";" if drop else ":"
    return "%02d:%02d:%02d%s%02d" % (frames // (nominal * 3600) % 24,
                                     frames // (nominal * 60) % 60,
                                     frames // nominal % 60, sep, frames % nominal)


WINDOW = 1 << 20          # bytes per scan window
MAX_DOC = 8 << 20         # a NonRealTimeMeta doc is ~2.5 KB; this is a sanity cap
PROBE = 32 << 20          # head/tail bytes scanned in a container we cannot walk


def _walk_top_boxes(fh, total):
    """Yield (kind, offset, size) for top-level MP4/MOV boxes, reading only the 8/16-byte
    headers so a multi-gigabyte `mdat` is seeked past rather than read."""
    pos = 0
    while pos + 8 <= total:
        fh.seek(pos)
        hdr = fh.read(8)
        if len(hdr) < 8:
            return
        size = int.from_bytes(hdr[0:4], "big")
        kind = hdr[4:8]
        if size == 1:                      # 64-bit size follows the type
            ext = fh.read(8)
            if len(ext) < 8:
                return
            size = int.from_bytes(ext, "big")
        elif size == 0:                    # "to end of file"
            size = total - pos
        if size < 8 or pos + size > total:
            return                         # malformed or truncated: stop cleanly
        yield kind, pos, size
        pos += size


DOC_CHUNK = 64 << 10      # the doc is ~2.5 KB; read it in small bites, not 1 MiB gulps


def _scan_for_nrt(fh, start, end, doc_end=None):
    """Return the NonRealTimeMeta document inside [start, end), scanning in bounded
    overlapping windows so nothing large is ever held in memory.

    `doc_end` bounds the follow-on read once the opening tag is found. For a box scan that
    is the end of the box; for a head/tail probe it is the end of the file, since the
    document may continue past the probe boundary.
    """
    if doc_end is None:
        doc_end = end
    pos = start
    while pos < end:
        fh.seek(pos)
        buf = fh.read(min(WINDOW, end - pos))
        if not buf:
            return None
        i = buf.find(b"<NonRealTimeMeta")
        if i != -1:
            at = pos + i
            fh.seek(at)
            doc = b""
            while len(doc) < MAX_DOC and at + len(doc) < doc_end:
                chunk = fh.read(min(DOC_CHUNK, doc_end - at - len(doc)))
                if not chunk:
                    break
                doc += chunk
                j = doc.find(b"</NonRealTimeMeta>")
                if j != -1:
                    return doc[:j + 18].decode("utf-8", "replace")
            return None
        if len(buf) < WINDOW:
            return None
        pos += WINDOW - 64                 # overlap, so a split tag is still found
    return None


def _find_nrt_xml(path):
    """Locate the Sony metadata WITHOUT loading the media.

    On an A7S III clip the metadata sits in a top-level `meta` box after the media payload
    -- byte 335,813,843 of a 335 MB file -- so reading the whole file to find 2.5 KB of XML
    is not an option once you are pointed at a real card offload. MP4/MOV are walked box by
    box with `mdat` skipped; anything else gets a bounded head+tail probe. Finally we fall
    back to the Cxxxx M01.XML sidecar, which is what survives a partial offload.
    """
    try:
        total = os.path.getsize(path)
        with open(path, "rb") as fh:
            boxes = list(_walk_top_boxes(fh, total))
            if boxes and boxes[0][0] in (b"ftyp", b"moov", b"mdat", b"wide", b"skip"):
                for kind, off, size in boxes:
                    if kind == b"mdat":
                        continue
                    doc = _scan_for_nrt(fh, off, off + size)
                    if doc:
                        return doc
            else:
                doc = _scan_for_nrt(fh, 0, min(total, PROBE), doc_end=total)
                if doc:
                    return doc
                if total > PROBE:
                    doc = _scan_for_nrt(fh, total - PROBE, total, doc_end=total)
                    if doc:
                        return doc
    except OSError:
        pass

    base = os.path.splitext(path)[0]
    for suffix in ("M01.XML", "M01.xml", "M01.Xml"):
        side = base + suffix
        if os.path.isfile(side):
            try:
                with open(side, "rb") as fh:
                    doc = _scan_for_nrt(fh, 0, os.path.getsize(side))
                if doc:
                    return doc
            except OSError:
                pass
    return None


def read_shot_marks(path):
    """Return a list of {label,name,frame,elapsed,tc} user Shot Marks, or [] if none."""
    xml = _find_nrt_xml(path)
    if not xml:
        return []

    def attr(tag, a):
        m = re.search(r'<%s[^>]*\b%s="([^"]*)"' % (tag, a), xml)
        return m.group(1) if m else ""

    num, den = _fps_rational(attr("VideoFrame", "captureFps") or attr("VideoFrame", "formatFps"))
    cap_exact = num / den
    tcfps = int(attr("LtcChangeTable", "tcFps") or "30")
    m0 = re.search(r'<LtcChange\b[^>]*value="([0-9A-Fa-f]{8})"', xml)
    H, M, S, F, drop = _decode_ltc(m0.group(1)) if m0 else (0, 0, 0, 0, False)

    ntsc = drop or den == 1001
    tc_num, tc_den = (tcfps * 1000, 1001) if ntsc else (tcfps, 1)
    nominal = round(tc_num / tc_den)
    start = _tc_to_frames(H, M, S, F, nominal, drop)

    marks = []
    for km in re.finditer(
            r'<KlvPacket\b[^>]*key="([0-9A-Fa-f]{32})"[^>]*frameCount="(\d+)"[^>]*lengthValue="([0-9A-Fa-f]+)"',
            xml):
        if km.group(1).upper() != ESSENCE_MARK_KEY:
            continue
        fc = int(km.group(2))
        b = bytes.fromhex(km.group(3))
        label = b[1:1 + b[0]].decode("ascii", "replace")
        if not label.startswith("_ShotMark"):
            continue
        elapsed = fc / cap_exact
        tc = _frames_to_tc(start + round(elapsed * tc_num / tc_den), nominal, drop)
        marks.append({"label": label, "name": "Shot Mark " + label.replace("_ShotMark", ""),
                      "frame": fc, "elapsed": elapsed, "tc": tc})
    return marks


# ── DaVinci Resolve glue ────────────────────────────────────────────────────────
def get_resolve():
    """Works from Resolve's Scripts menu (injected `resolve`) or a terminal."""
    try:
        return resolve  # type: ignore  # injected by Resolve when run from the menu
    except NameError:
        pass
    api = os.environ.get(
        "RESOLVE_SCRIPT_API",
        "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting")
    sys.path.append(os.path.join(api, "Modules"))
    os.environ.setdefault("RESOLVE_SCRIPT_API", api)
    os.environ.setdefault(
        "RESOLVE_SCRIPT_LIB",
        "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so")
    try:
        import DaVinciResolveScript as dvr
        return dvr.scriptapp("Resolve")
    except Exception:
        return None


def _iter_clips(media_pool):
    stack = [media_pool.GetRootFolder()]
    while stack:
        folder = stack.pop()
        for clip in folder.GetClipList():
            yield clip
        stack.extend(folder.GetSubFolderList())


def _note(m):
    return "%s | src TC %s | %.3fs" % (m["label"], m["tc"], m["elapsed"])


def apply_in_resolve():
    r = get_resolve()
    if r is None:
        print("Could not reach Resolve. From a terminal, set Preferences ▸ System ▸ General ▸ "
              "'External scripting using' = Local. (Inside Resolve's Scripts menu this just works.)")
        return
    project = r.GetProjectManager().GetCurrentProject()
    if not project:
        print("No project is open."); return
    print("Applying Sony Shot Marks in '%s'…" % project.GetName())

    cache = {}
    def marks_for(path):
        if path not in cache:
            cache[path] = read_shot_marks(path) if (path and os.path.isfile(path)) else []
        return cache[path]

    # 1) SOURCE clip markers (Media Pool) — sticky for clips added to timelines later
    src_clips = src_added = 0
    for clip in _iter_clips(project.GetMediaPool()):
        marks = marks_for(clip.GetClipProperty("File Path") or "")
        if not marks:
            continue
        src_clips += 1
        existing = set((clip.GetMarkers() or {}).keys())
        n = 0
        for m in marks:
            if float(m["frame"]) in existing:
                continue
            if clip.AddMarker(m["frame"], COLOR.get(m["label"], "Blue"), m["name"], _note(m), 1, m["label"]):
                n += 1
        src_added += n
        print("  [source] %s: +%d marker(s)" % (clip.GetName(), n))

    # 2) TIMELINE instances — clips already edited in (these don't update retroactively
    #    from the source marker, so we mark each timeline item directly). frameId on a
    #    TimelineItem is relative to the item's in-point, i.e. sourceFrame - leftOffset.
    tl_items = tl_added = 0
    for ti in range(1, (project.GetTimelineCount() or 0) + 1):
        tl = project.GetTimelineByIndex(ti)
        if not tl:
            continue
        for tr in range(1, (tl.GetTrackCount("video") or 0) + 1):
            for item in (tl.GetItemListInTrack("video", tr) or []):
                mpi = item.GetMediaPoolItem()
                if not mpi:
                    continue
                marks = marks_for(mpi.GetClipProperty("File Path") or "")
                if not marks:
                    continue
                left = int(item.GetLeftOffset() or 0)
                dur = int(item.GetDuration() or 0)
                existing = set((item.GetMarkers() or {}).keys())
                hit = 0
                for m in marks:
                    fid = m["frame"] - left          # position within this instance
                    if fid < 0 or fid >= dur or float(fid) in existing:
                        continue
                    if item.AddMarker(fid, COLOR.get(m["label"], "Blue"), m["name"], _note(m), 1, m["label"]):
                        hit += 1
                if hit:
                    tl_items += 1; tl_added += hit
                    print("  [timeline:%s] %s: +%d marker(s)" % (tl.GetName(), item.GetName(), hit))

    print("\nDone: %d source marker(s) on %d clip(s); %d marker(s) on %d timeline instance(s)."
          % (src_added, src_clips, tl_added, tl_items))


# ── Entry point: terminal dry-run if given a file, else run inside Resolve ───────
if __name__ == "__main__":
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        marks = read_shot_marks(sys.argv[1])
        print("%s: %d Shot Mark(s)" % (os.path.basename(sys.argv[1]), len(marks)))
        for m in marks:
            print("  frame %5d  %-5s  %-12s  %s  (%.3fs)" %
                  (m["frame"], COLOR.get(m["label"], "Blue"), m["name"], m["tc"], m["elapsed"]))
    else:
        apply_in_resolve()
