#!/usr/bin/env python3
"""
process_folder.py — the DIT "tight loop": point it at a folder of offloaded footage and
it produces a universal Shot-Mark package that works across NLEs.

For every Sony clip that carries Shot Marks it:
  * embeds Adobe xmpDM markers INTO the file (default) or writes a .xmp sidecar
        -> Premiere / Bridge / Media Encoder read these natively
  * records the marks in a batch manifest (SHOTMARKS.csv + .json)
  * drops a self-contained Resolve kit (the drop-in reader + extractor + install note)
        -> Resolve users run one script; every clip gets its markers
  * writes a README telling each editor exactly what to do

Usage:
  tools/process_folder.py /path/to/CARD                 # embed marks into the files in place
  tools/process_folder.py /path/to/CARD --sidecar       # write .xmp sidecars instead (no rewrite)
  tools/process_folder.py /path/to/CARD --dry-run
"""
import os, sys, json, csv, shutil, subprocess, tempfile, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sony_shotmark as S

VIDEO_EXT = (".mp4", ".mov", ".mxf", ".m4v")


def embed_inplace(clip, path):
    """Embed xmpDM markers directly into the existing MP4 (exiftool, in place)."""
    if shutil.which("exiftool") is None:
        raise SystemExit("embed mode needs exiftool on PATH (use --sidecar otherwise)")
    core = S.build_xmp(clip).split("?>", 1)[1].strip()
    packet = ('<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
              + core + '\n<?xpacket end="w"?>')
    with tempfile.NamedTemporaryFile("w", suffix=".xmp", delete=False) as tf:
        tf.write(packet); tmp = tf.name
    try:
        r = subprocess.run(["exiftool", "-overwrite_original", "-m", f"-XMP<={tmp}", path],
                           capture_output=True, text=True)
        return "1 image files updated" in (r.stdout + r.stderr)
    finally:
        os.unlink(tmp)


def write_resolve_kit(dest):
    os.makedirs(dest, exist_ok=True)
    for f in ("Resolve_ApplyShotMarks.py", "sony_shotmark.py"):
        shutil.copy2(os.path.join(HERE, f), os.path.join(dest, f))
    open(os.path.join(dest, "INSTALL.txt"), "w").write(
        "DaVinci Resolve — apply Sony Shot Marks\n"
        "=======================================\n"
        "1. Copy BOTH files in this folder into Resolve's Scripts/Utility folder:\n"
        "     macOS: ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/"
        "Fusion/Scripts/Utility/\n"
        "     Win:   %APPDATA%\\Blackmagic Design\\DaVinci Resolve\\Support\\Fusion\\"
        "Scripts\\Utility\\\n"
        "2. Import your footage into your Resolve project as usual.\n"
        "3. Run  Workspace > Scripts > Resolve_ApplyShotMarks.\n"
        "   Every clip that has Sony Shot Marks gets blue clip markers. Re-runnable.\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Batch Sony Shot Marks -> universal NLE package")
    ap.add_argument("folder")
    ap.add_argument("--sidecar", action="store_true", help="write .xmp sidecars instead of embedding")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    clips = []
    for root, _, files in os.walk(args.folder):
        for fn in sorted(files):
            if fn.lower().endswith(VIDEO_EXT) and not fn.startswith("._"):
                clips.append(os.path.join(root, fn))

    manifest, n_marked = [], 0
    print(f"\nScanning {args.folder} … {len(clips)} media file(s)\n")
    for path in clips:
        try:
            clip = S.parse_clip(path)
        except SystemExit:
            continue                       # no Sony NRT metadata
        except Exception:
            continue
        user = [m for m in clip.marks if m.is_user_mark]
        if not user:
            continue
        n_marked += 1
        tag = "would mark" if args.dry_run else ("sidecar" if args.sidecar else "embed")
        print(f"  [{tag}] {clip.source_file:24} {len(user)} Shot Mark(s) "
              f"@ {[m.source_timecode for m in user]}")
        if not args.dry_run:
            if args.sidecar:
                S.write_xmp_sidecar(clip, path + ".xmp")
            else:
                if not embed_inplace(clip, path):
                    print(f"        ! embed failed for {clip.source_file}")
        for m in user:
            manifest.append({"file": clip.source_file, "label": m.label,
                             "frame": m.capture_frame, "elapsed_s": m.elapsed_seconds,
                             "source_tc": m.source_timecode})

    if args.dry_run:
        print(f"\n{n_marked} clip(s) carry Shot Marks. (dry run — nothing written)\n"); return

    # batch manifest + Resolve kit + README at the folder root
    with open(os.path.join(args.folder, "SHOTMARKS.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    with open(os.path.join(args.folder, "SHOTMARKS.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["file", "label", "frame", "elapsed_s", "source_tc"])
        w.writeheader(); w.writerows(manifest)
    write_resolve_kit(os.path.join(args.folder, "_ResolveShotMarks"))
    open(os.path.join(args.folder, "README_SHOTMARKS.txt"), "w").write(
        "Sony Shot Marks — this footage has been processed.\n\n"
        f"{n_marked} clip(s) carry Shot Marks ({len(manifest)} marks total). See SHOTMARKS.csv.\n\n"
        "PREMIERE PRO / Bridge / Media Encoder:\n"
        "  Markers are " + ("in a .xmp sidecar beside each clip" if args.sidecar
                            else "embedded in the files") + ". Import normally;\n"
        "  enable Preferences > Media > 'Write clip markers to XMP'. Marks appear as clip markers.\n\n"
        "DAVINCI RESOLVE:\n"
        "  Resolve can't read marks from media. Use the kit in _ResolveShotMarks/ "
        "(see its INSTALL.txt):\n"
        "  install the script once, then Workspace > Scripts > Resolve_ApplyShotMarks.\n")
    print(f"\n✓ Package ready in {args.folder}")
    print(f"   {n_marked} clip(s) marked · SHOTMARKS.csv/json · _ResolveShotMarks/ · README_SHOTMARKS.txt\n")


if __name__ == "__main__":
    main()
