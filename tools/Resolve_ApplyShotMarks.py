#!/usr/bin/env python3
"""
Resolve_ApplyShotMarks  —  self-contained DaVinci Resolve drop-in script.

Reads Sony "Shot Marks" straight out of each media file's embedded metadata and adds
them as native Resolve clip markers. Recurses the whole Media Pool. Idempotent.

DEPENDENCIES: NONE beyond the Python standard library. No ffmpeg, no exiftool, no pip
packages. The Sony marks are plain XML inside the file; we just read and parse bytes.
Runs in Resolve's built-in Python. This is ONE file — nothing else to install.

INSTALL (once):
  Copy this file into Resolve's Scripts/Utility folder:
    macOS : ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/
    Win   : %APPDATA%\\Blackmagic Design\\DaVinci Resolve\\Support\\Fusion\\Scripts\\Utility\\
USE:
  1. Import your Sony footage into the project as usual.
  2. Workspace ▸ Scripts ▸ Resolve_ApplyShotMarks   (output shows in Workspace ▸ Console)

TEST THE READER WITHOUT RESOLVE (from a terminal):
  python3 Resolve_ApplyShotMarks.py /path/to/CLIP.MP4     # prints the marks it would add
"""
import os, sys, re, struct

# ── Sony Shot Mark extraction (pure standard library) ───────────────────────────
ESSENCE_MARK_KEY = "060E2B34010101050301020A02000000"   # SMPTE comment/essence-mark UL
COLOR = {"_ShotMark1": "Blue", "_ShotMark2": "Cyan"}     # Resolve marker colors
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


def read_shot_marks(path):
    """Return a list of {label,name,frame,elapsed,tc} user Shot Marks, or [] if none."""
    try:
        raw = open(path, "rb").read()
    except OSError:
        return []
    i = raw.find(b"<NonRealTimeMeta")
    j = raw.find(b"</NonRealTimeMeta>")
    if i == -1 or j == -1:
        return []
    xml = raw[i:j + 18].decode("utf-8", "replace")

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
