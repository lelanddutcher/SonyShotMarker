# Windows port status

Goal: ship a Windows version of Shot Mark Embedder that keeps the macOS app's framing, never touches originals, and produces Premiere-readable embedded-copy clips in `footage embedded markers/`.

## Current implementation

- Windows UI: `windows/shot_mark_embedder_windows.py`
  - Tkinter desktop shell, 640×580 starting window, pink accent, cat branding, add/drop zone, output picker, per-file log, background worker.
  - Drag/drop is enabled when packaged with `tkinterdnd2`; the normal **Add Clips…** picker remains the fallback.
  - `--smoke` mode imports the bundled core and exits without opening a GUI for CI/package checks.
- Shared core: `tools/sony_shotmark.py`
  - `--embed` no longer depends on ExifTool.
  - It mirrors the Swift app's offset-safe strategy: copy source → find pre-`mdat` top-level `free`/`skip` space → write Adobe XMP `uuid` box → leave media payload/chunk offsets untouched.
  - Existing Adobe XMP `uuid` boxes before `mdat` are neutralized so the newest marker set wins.
- Batch wrapper: `tools/embed_batch.py`
  - Used by the Windows UI worker for per-file status.
- Tests: `tests/test_embed_engine.py`
  - Synthetic Sony metadata fixture.
  - Confirms marks parse, output is written into reusable free space, originals are byte-for-byte unchanged, old Adobe XMP is neutralized, and missing free space fails without partial output.
- Windows packaging: `windows/build_windows.ps1`, `requirements-windows.txt`, `.github/workflows/windows-build.yml`
  - GitHub Actions builds on `windows-latest`, runs tests, compiles/smoke-checks the source launcher, builds the PyInstaller artifact, verifies the exe exists, and uploads `Shot-Mark-Embedder-Windows`.

## Verification run from Linux host

```bash
python3 -m pytest -q tests
python3 -m py_compile windows/shot_mark_embedder_windows.py tools/sony_shotmark.py tools/embed_batch.py
python3 windows/shot_mark_embedder_windows.py --smoke
git diff --check
```

Expected/current: pass.

## Remaining truth gap

Linux can verify the pure core and prepare the Windows CI lane, but final confidence needs one of these:

1. GitHub Actions Windows artifact passes on `windows-latest` after push/PR.
2. A real Windows desktop user runs the artifact on real Sony clips and imports the embedded copies into Premiere to confirm markers display.
3. Optional later: Authenticode signing to reduce SmartScreen hostility. Do not block the functional beta on signing.
