#!/usr/bin/env python3
"""
resolve_shotmarks.py — push Sony Shot Marks into DaVinci Resolve as clip markers.

Why this exists: Resolve does NOT read embedded XMP or `.xmp` sidecar markers (unlike
Premiere). The reliable route is Resolve's scripting API:

    MediaPoolItem.AddMarker(frameId, color, name, note, duration, customData)

The Sony capture `frameCount` is a clip-start frame offset, so it IS the Resolve clip
marker `frameId` — the same number that becomes the Premiere xmpDM `startTime`. One
extraction, both NLEs.

Requires:
  * DaVinci Resolve running
  * Preferences > System > General > "External scripting using" = Local (or Network)
  * the clip already imported into the current project's Media Pool

Usage:
  tools/resolve_shotmarks.py /path/to/CLIP.MP4              # add markers to that clip in Resolve
  tools/resolve_shotmarks.py /path/to/CLIP.MP4 --dry-run    # show what would be added (no Resolve needed)
  tools/resolve_shotmarks.py /path/to/CLIP.MP4 --include-auto
"""
import os, sys, argparse

# ShotMark1 -> Blue, ShotMark2 -> Cyan (Resolve's exact color strings)
COLOR = {"_ShotMark1": "Blue", "_ShotMark2": "Cyan", "_RecStart": "Green", "_RecEnd": "Green"}


def load_resolve():
    """Bootstrap the DaVinciResolveScript module from the standard macOS locations."""
    api = os.environ.get(
        "RESOLVE_SCRIPT_API",
        "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting")
    lib = os.environ.get(
        "RESOLVE_SCRIPT_LIB",
        "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so")
    mod = os.path.join(api, "Modules")
    if mod not in sys.path:
        sys.path.append(mod)
    os.environ.setdefault("RESOLVE_SCRIPT_API", api)
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", lib)
    try:
        import DaVinciResolveScript as dvr
    except ImportError as e:
        raise SystemExit(f"Could not import DaVinciResolveScript ({e}).\n"
                         f"Looked in: {mod}\nIs DaVinci Resolve installed?")
    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        raise SystemExit("Connected to the module but Resolve isn't reachable.\n"
                         "Open Resolve and set Preferences > System > General > "
                         "'External scripting using' = Local, then retry.")
    return resolve


def find_clip(media_pool, basename):
    """Recursively search every bin in the media pool for a clip matching basename."""
    stack = [media_pool.GetRootFolder()]
    while stack:
        folder = stack.pop()
        for clip in folder.GetClipList():
            if clip.GetName() == basename:
                return clip
            fp = clip.GetClipProperty("File Path") or ""
            if fp and os.path.basename(fp) == basename:
                return clip
        stack.extend(folder.GetSubFolderList())
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Push Sony Shot Marks into Resolve as clip markers")
    ap.add_argument("clip", help="Sony clip path (read marks from it + match by name in the Media Pool)")
    ap.add_argument("--include-auto", action="store_true", help="also add _RecStart/_RecEnd")
    ap.add_argument("--dry-run", action="store_true", help="print planned markers; do not touch Resolve")
    args = ap.parse_args(argv)

    # read marks via the sibling extractor (single source of truth)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import sony_shotmark
    clip = sony_shotmark.parse_clip(args.clip)
    marks = clip.marks if args.include_auto else [m for m in clip.marks if m.is_user_mark]

    print(f"\n  {clip.source_file}: {len(marks)} marker(s) to place in Resolve")
    for m in marks:
        print(f"    frame {m.capture_frame:>5}  {COLOR.get(m.label,'Blue'):5}  "
              f"{m.friendly:<12} {m.source_timecode}")
    if args.dry_run:
        print("\n  (dry run — Resolve not contacted)\n")
        return

    resolve = load_resolve()
    project = resolve.GetProjectManager().GetCurrentProject()
    if not project:
        raise SystemExit("No project open in Resolve.")
    item = find_clip(project.GetMediaPool(), clip.source_file)
    if not item:
        raise SystemExit(f"'{clip.source_file}' not found in the Media Pool — import it first.")

    ok = 0
    for m in marks:
        note = f"{m.label} | src TC {m.source_timecode} | {m.elapsed_seconds:.3f}s"
        # duration must be >= 1 frame, frameId is offset from the clip's first source frame
        if item.AddMarker(m.capture_frame, COLOR.get(m.label, "Blue"), m.friendly, note, 1, m.label):
            ok += 1
        else:
            print(f"    ! AddMarker failed at frame {m.capture_frame} "
                  f"(out of range, or a marker already exists there)")
    print(f"\n  ✓ added {ok}/{len(marks)} clip markers to {clip.source_file} in Resolve\n")


if __name__ == "__main__":
    main()
