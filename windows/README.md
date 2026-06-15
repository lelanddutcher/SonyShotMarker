# Shot Mark Embedder — Windows port *(experimental)*

> ⚠️ **Experimental / beta.** Built and smoke-tested by CI on `windows-latest`, but not yet validated end-to-end on real Sony footage on a Windows machine. The marker engine is shared verbatim with the Mac app (identical, source-safe embed); the Tkinter GUI wrapper is what still needs real-world shakeout. The packaged `.exe` is **unsigned**, so Windows SmartScreen will show an "Unknown Publisher" prompt — choose **More info → Run anyway**.

This is the Windows shell for the same workflow as the macOS app:

1. add/drop already-offloaded Sony `.MP4` / `.MOV` / `.M4V` clips,
2. choose an output folder,
3. click **Embed Markers**,
4. get copies in `footage embedded markers/` with Premiere-readable XMP clip markers.

Originals are never modified.

## Build on Windows

```powershell
cd SonyShotMarker
py -3.12 -m venv .venv-win
.\.venv-win\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-windows.txt
python -m pytest -q tests
powershell -ExecutionPolicy Bypass -File windows\build_windows.ps1
```

Output:

```text
dist\Shot Mark Embedder\Shot Mark Embedder.exe
```

The app is intentionally dependency-light at runtime: the marker embedder is pure Python and does **not** require ExifTool. `tkinterdnd2` is bundled by PyInstaller for drag/drop support; if running from source without it, the **Add Clips…** button still works.

## Debug notes

- The core embed path is covered by `tests/test_embed_engine.py` with synthetic MP4 box-layout fixtures.
- The Windows executable is built/tested by `.github/workflows/windows-build.yml` on `windows-latest`.
- Real Sony sample clips should still be used for final user-facing release validation. Synthetic fixtures prove the parser/embedder mechanics, not Premiere's whole import stack.
