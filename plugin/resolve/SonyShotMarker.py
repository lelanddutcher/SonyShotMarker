#!/usr/bin/env python3
"""
Sony Shot Marker - DaVinci Resolve panel.

Reads Sony "Shot Marks" (the C1 / C2 button presses you make while recording) straight out
of your footage and adds them as native Resolve markers. Your media is only ever read.

This is the Resolve counterpart to the Premiere Pro panel in ../premiere-uxp. Resolve will
not read markers out of a media file no matter how they are embedded - that is a Resolve
limitation, and even Sony's own Catalyst plugin cannot import Shot Marks there - so this
reads the camera metadata itself and applies the marks through Resolve's scripting API.

DEPENDENCIES: none beyond the Python standard library. No ffmpeg, no exiftool, no pip
packages. One file, nothing else to install. It runs in Resolve's built-in interpreter.

INSTALL (once): copy this file into Resolve's Scripts/Utility folder, or run the installer
in this directory.
  macOS : ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/
  Win   : %APPDATA%\\Blackmagic Design\\DaVinci Resolve\\Support\\Fusion\\Scripts\\Utility\\

USE: import your footage, then Workspace > Scripts > SonyShotMarker. A window opens with
the same controls as the Premiere panel. Where Fusion's UI toolkit is unavailable it falls
back to running headlessly and printing to Workspace > Console.

TEST THE READER WITHOUT RESOLVE (from a terminal):
  python3 SonyShotMarker.py /path/to/CLIP.MP4     # prints the marks it would add
"""

import os, sys, re, struct

# ── Sony Shot Mark extraction (pure standard library) ───────────────────────────
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
# Resolve's own marker colour names, in the order its marker menu lists them. Green is the
# default: Resolve's first colour is Blue, but green reads as "these are the keepers"
# rather than as just another blue thing on the timeline.
RESOLVE_COLORS = ["Green", "Blue", "Cyan", "Yellow", "Red", "Pink", "Purple", "Fuchsia",
                  "Rose", "Lavender", "Sky", "Mint", "Lemon", "Sand", "Cocoa", "Cream"]
DEFAULT_COLOR = "Green"


def get_resolve():
    """Works from Resolve's Scripts menu (injected `resolve`) or from a terminal."""
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


def apply_marks(project, color=DEFAULT_COLOR, include_auto=False, do_timelines=True,
                apply_changes=True, log=print):
    """Scan (and optionally mark) every clip in the project.

    Returns a summary dict. With apply_changes=False nothing is written - that is the
    Scan button, and it lets someone see what they are about to get before committing.

    Timeline instances are handled separately on purpose: Resolve does not push a source
    clip's markers onto clips already cut into a timeline, so an editor who has started
    cutting would otherwise see nothing.
    """
    cache = {}

    def marks_for(path):
        if path not in cache:
            found = read_shot_marks(path) if (path and os.path.isfile(path)) else []
            if not include_auto:
                found = [m for m in found if m["label"].startswith("_ShotMark")]
            cache[path] = found
        return cache[path]

    summary = {"clips_with_marks": 0, "source_added": 0, "timeline_added": 0,
               "timeline_items": 0, "considered": 0, "rows": []}

    for clip in _iter_clips(project.GetMediaPool()):
        summary["considered"] += 1
        name = clip.GetName()
        marks = marks_for(clip.GetClipProperty("File Path") or "")
        if not marks:
            continue
        summary["clips_with_marks"] += 1

        existing = set((clip.GetMarkers() or {}).keys())
        added = 0
        for m in marks:
            if float(m["frame"]) in existing:
                continue
            if not apply_changes:
                added += 1
                continue
            if clip.AddMarker(m["frame"], color, m["name"], _note(m), 1, m["label"]):
                added += 1
        summary["source_added"] += added
        summary["rows"].append({
            "name": name,
            "marks": len(marks),
            "added": added,
            "tcs": [m["tc"] for m in marks],
        })
        log("  %s: %d Shot Mark(s), %s%d" % (
            name, len(marks), "would add " if not apply_changes else "+", added))

    if not do_timelines:
        return summary

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
                    if not apply_changes:
                        hit += 1
                        continue
                    if item.AddMarker(fid, color, m["name"], _note(m), 1, m["label"]):
                        hit += 1
                if hit:
                    summary["timeline_items"] += 1
                    summary["timeline_added"] += hit
                    log("  [%s] %s: %s%d" % (tl.GetName(), item.GetName(),
                                             "would add " if not apply_changes else "+", hit))
    return summary


def summarize(summary, applied):
    verb = "added" if applied else "to add"
    parts = ["%d of %d clip(s) have Shot Marks" % (summary["clips_with_marks"],
                                                   summary["considered"])]
    parts.append("%d source marker(s) %s" % (summary["source_added"], verb))
    if summary["timeline_added"]:
        parts.append("%d on %d timeline instance(s)" % (summary["timeline_added"],
                                                        summary["timeline_items"]))
    return " - ".join(parts)


# ── The panel ───────────────────────────────────────────────────────────────────
def _fusion_ui(log=print):
    """Fusion's UI toolkit and a dispatcher source, or (None, None).

    Which globals Resolve injects depends on where the script is run from, so this tries
    every documented route rather than assuming one:

      * `fu` / `fusion` in globals - what the Fusion page injects;
      * `resolve.Fusion()` - the documented accessor, and the one that works from
        Resolve's own Workspace > Scripts menu, where `fusion` may not be injected;
      * a direct import, for a terminal.

    The dispatcher lives in `bmd` when injected, and otherwise in the `fusionscript`
    module that DaVinciResolveScript itself loads.
    """
    fusion_app = None
    for name in ("fu", "fusion"):
        fusion_app = globals().get(name)
        if fusion_app is not None:
            break

    if fusion_app is None:
        r = globals().get("resolve")
        if r is None:
            r = get_resolve()          # also puts the Modules folder on sys.path
        if r is not None:
            try:
                fusion_app = r.Fusion()
            except Exception as exc:
                log("  resolve.Fusion() failed: %s" % exc)

    if fusion_app is None:
        try:
            import DaVinciResolveScript as dvr
            fusion_app = dvr.scriptapp("Fusion")
        except Exception as exc:
            log("  could not reach Fusion: %s" % exc)
            return None, None

    dispatcher_src = globals().get("bmd")
    if dispatcher_src is None or not hasattr(dispatcher_src, "UIDispatcher"):
        for module_name in ("fusionscript", "DaVinciResolveScript"):
            try:
                mod = __import__(module_name)
                if hasattr(mod, "UIDispatcher"):
                    dispatcher_src = mod
                    break
            except Exception:
                continue
    if dispatcher_src is None or not hasattr(dispatcher_src, "UIDispatcher"):
        log("  no UIDispatcher available")
        return None, None

    try:
        return fusion_app.UIManager, dispatcher_src
    except Exception as exc:
        log("  no UIManager on the Fusion app: %s" % exc)
        return None, None


def run_panel():
    """Show the window. Falls back to a console run if Fusion's UI is unavailable."""
    # Printed first so that a failure is never silent: if the Scripts menu appears to do
    # nothing, this line in Workspace > Console proves the script at least started.
    print("Sony Shot Marker - starting...")
    r = get_resolve()
    if r is None:
        print("Could not reach Resolve. From a terminal, set Preferences > System > General"
              " > 'External scripting using' = Local. Inside Resolve's Scripts menu this"
              " just works.")
        return

    ui, bmd_mod = _fusion_ui()
    if ui is None:
        print("Fusion's UI toolkit is not available here; running headless instead.")
        return run_console(r)

    try:
        disp = bmd_mod.UIDispatcher(ui)
    except Exception as exc:
        print("Could not create the window dispatcher: %s" % exc)
        return run_console(r)
    WIN_ID = "SonyShotMarkerWin"

    color_items = list(RESOLVE_COLORS)
    win = disp.AddWindow({
        "ID": WIN_ID,
        "WindowTitle": "Sony Shot Marker",
        "Geometry": [200, 200, 560, 460],
        # Without the stays-on-top hint the window opens BEHIND Resolve's main window and
        # looks like the menu item did nothing at all.
        "WindowFlags": {"Window": True, "WindowStaysOnTopHint": True},
    }, [
        ui.VGroup({"Spacing": 8, "Weight": 1}, [
            ui.Label({"ID": "Title", "Text": "SONY SHOT MARKER",
                      "Weight": 0, "Font": ui.Font({"PixelSize": 18, "Bold": True})}),
            ui.Label({"ID": "Sub", "Weight": 0,
                      "Text": "Read straight out of your footage. Never written to it."}),

            ui.HGroup({"Weight": 0, "Spacing": 8}, [
                ui.Label({"Text": "Colour", "Weight": 0, "MinimumSize": [70, 0]}),
                ui.ComboBox({"ID": "Color", "Weight": 1}),
            ]),
            ui.CheckBox({"ID": "Timelines", "Weight": 0, "Checked": True,
                         "Text": "Also mark clips already cut into timelines"}),
            ui.CheckBox({"ID": "IncludeAuto", "Weight": 0, "Checked": False,
                         "Text": "Also mark automatic Rec Start / Rec End"}),

            ui.HGroup({"Weight": 0, "Spacing": 8}, [
                ui.Button({"ID": "Scan", "Text": "Scan", "Weight": 0, "MinimumSize": [110, 30]}),
                ui.Button({"ID": "Apply", "Text": "Apply Shot Marks", "Weight": 1,
                           "MinimumSize": [180, 30]}),
            ]),

            ui.TextEdit({"ID": "Log", "Weight": 1, "ReadOnly": True,
                         "PlaceholderText": "Import your Sony footage, then hit Scan."}),
            ui.Label({"ID": "Status", "Weight": 0,
                      "Text": "Read-only. Your camera originals are never modified."}),
        ]),
    ])

    items = win.GetItems()
    for name in color_items:
        items["Color"].AddItem(name)
    items["Color"].CurrentIndex = color_items.index(DEFAULT_COLOR)

    def write(line=""):
        items["Log"].PlainText = (items["Log"].PlainText or "") + str(line) + "\n"

    def run(apply_changes):
        project = r.GetProjectManager().GetCurrentProject()
        if not project:
            items["Status"].Text = "No project is open."
            return
        items["Log"].PlainText = ""
        write(("Applying" if apply_changes else "Scanning") + " in '%s'..." % project.GetName())
        color = color_items[items["Color"].CurrentIndex]
        try:
            summary = apply_marks(
                project,
                color=color,
                include_auto=items["IncludeAuto"].Checked,
                do_timelines=items["Timelines"].Checked,
                apply_changes=apply_changes,
                log=write,
            )
        except Exception as exc:                       # a bad clip should not kill the panel
            write("Error: %s" % exc)
            items["Status"].Text = "Failed - nothing was changed."
            return
        if not summary["clips_with_marks"]:
            write("No Sony Shot Marks found in this project.")
        items["Status"].Text = summarize(summary, apply_changes)

    win.On[WIN_ID].Close = lambda ev: disp.ExitLoop()
    win.On.Scan.Clicked = lambda ev: run(False)
    win.On.Apply.Clicked = lambda ev: run(True)

    win.Show()
    # Belt and braces: some builds ignore the window flag, so ask for the front explicitly.
    for method in ("Raise", "ActivateWindow"):
        try:
            getattr(win, method)()
        except Exception:
            pass
    disp.RunLoop()
    win.Hide()


def run_console(r=None):
    """Headless equivalent of the Apply button, for the Console or a terminal."""
    r = r or get_resolve()
    if r is None:
        print("Could not reach Resolve.")
        return
    project = r.GetProjectManager().GetCurrentProject()
    if not project:
        print("No project is open.")
        return
    print("Applying Sony Shot Marks in '%s'..." % project.GetName())
    summary = apply_marks(project)
    print("\n" + summarize(summary, True))


# ── Entry point ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        # Terminal dry-run against a single clip: prove the reader without Resolve.
        marks = read_shot_marks(sys.argv[1])
        print("%s: %d Shot Mark(s)" % (os.path.basename(sys.argv[1]), len(marks)))
        for m in marks:
            print("  frame %5d  %-12s  %s  (%.3fs)" % (m["frame"], m["name"], m["tc"], m["elapsed"]))
    else:
        try:
            run_panel()
        except Exception:
            # Resolve swallows an uncaught exception here and the menu item just appears
            # to do nothing. Print it to Workspace > Console instead.
            import traceback
            print("Sony Shot Marker failed to start:")
            traceback.print_exc()
