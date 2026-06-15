#!/usr/bin/env python3
"""
embed_batch.py — the engine behind the "drop files → embedded copies" Mac app.

Assumes the DIT already offloaded. Takes a list of Sony clips and an output directory
and writes XMP-marker-embedded COPIES into:

    <output>/footage embedded markers/

Originals are never touched. Clips with no user Shot Marks are skipped (reported).

  --progress  emit machine-readable "@@P done total state name" lines for a GUI
  --json      emit one JSON result object at the end

Usage:
  embed_batch.py --out /path/to/output  CLIP1.MP4 CLIP2.MP4 ...
"""
import os, sys, json, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sony_shotmark as S
import run_log

OUT_FOLDER_NAME = "footage embedded markers"


def process_one(src, dest_dir):
    """Embed one clip into dest_dir. Returns (rec, human_message)."""
    name = os.path.basename(src)
    rec = {"file": name, "status": "", "state": "skip", "marks": 0, "output": ""}
    if not os.path.isfile(src):
        rec.update(status="missing"); return rec, f"✗ {name}: not found"
    try:
        clip = S.parse_clip(src)
    except SystemExit:
        rec.update(status="not-sony"); return rec, f"– {name}: no Sony metadata, skipped"
    except Exception as e:
        rec.update(status="error", state="err"); return rec, f"✗ {name}: {e}"

    user = [m for m in clip.marks if m.is_user_mark]
    if not user:
        rec.update(status="no-marks"); return rec, f"– {name}: no Shot Marks, skipped"
    if not src.lower().endswith((".mp4", ".mov", ".m4v")):
        rec.update(status="unsupported"); return rec, f"– {name}: {len(user)} mark(s), not MP4/MOV"

    out = os.path.join(dest_dir, name)
    try:
        S.embed_xmp_into_mp4(clip, src, out)   # copies src->out, embeds; never touches src
        rec.update(status="embedded", state="ok", marks=len(user), output=out)
        return rec, f"✓ {name}: {len(user)} Shot Mark(s) embedded"
    except SystemExit as e:
        rec.update(status=f"embed-failed", state="err"); return rec, f"✗ {name}: {e}"


def _human(n):
    if n is None or n < 0:
        return "unknown"
    f, units = float(n), ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while f >= 1024 and i < len(units) - 1:
        f /= 1024; i += 1
    return f"{f:.1f} {units[i]}"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Embed Sony Shot Marks into copies in an output folder")
    ap.add_argument("--out", required=True)
    ap.add_argument("--progress", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--force", action="store_true", help="skip the free-space preflight")
    ap.add_argument("files", nargs="+")
    args = ap.parse_args(argv)

    dest_dir = os.path.join(args.out, OUT_FOLDER_NAME)
    os.makedirs(dest_dir, exist_ok=True)

    # Free-space preflight — block a doomed run before copying anything. (CoW shared-volume
    # cloning is a Mac-app-only optimization; Python's copy is a real byte copy.)
    space_ok, required, free = S.enough_output_space(args.files, dest_dir)
    if not args.force and not space_ok:
        msg = (f"Not enough space: embedding can need up to ~{_human(required)} on "
               f"{args.out}, but only {_human(free)} is free. Free up space, choose a "
               f"different output, or re-run with --force.")
        if args.progress:
            print(f"@@SPACE {required} {free}", flush=True)
        if args.json:
            print(json.dumps({"error": "insufficient-space", "required": required,
                              "free": free, "dest": dest_dir}))
        else:
            print("✗ " + msg)
        return 2

    rl = run_log.RunLog()
    rl.header(dest_dir, dest_volume=args.out, dest_free=free)
    rl.inputs(args.files)

    N = len(args.files)
    results = []
    for i, src in enumerate(args.files):
        if args.progress:
            print(f"@@P {i} {N} work {os.path.basename(src)}", flush=True)
        rec, msg = process_one(src, dest_dir)
        results.append(rec)
        rl.result(msg)
        if not args.json:
            print("  " + msg, flush=True)
        if args.progress:
            print(f"@@P {i+1} {N} {rec['state']} {rec['file']}", flush=True)

    n_ok = sum(1 for r in results if r["status"] == "embedded")
    rl.summary(f"{n_ok}/{N} embedded")
    log_path = rl.write()
    if args.progress:
        print(f"@@P {N} {N} done {n_ok}", flush=True)
        print(f"@@LOG {log_path}", flush=True)
    if args.json:
        print(json.dumps({"dest": dest_dir, "embedded": n_ok, "total": N,
                          "log": log_path, "results": results}))
    elif not args.progress:
        print(f"\n✓ {n_ok}/{N} embedded → {dest_dir}\n  log: {log_path}\n")
    else:
        print(f"@@DONE {n_ok}/{N} embedded → {dest_dir}", flush=True)
    return 0 if n_ok else 1


if __name__ == "__main__":
    sys.exit(main())
