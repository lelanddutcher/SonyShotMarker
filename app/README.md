# Shot Mark Embedder — macOS app

A small, **self-contained** native (SwiftUI) app: drop already-offloaded Sony footage →
choose an output → get a `footage embedded markers` folder of XMP-embedded copies that
Premiere/Bridge read as clip markers. Originals are never touched.

**No dependencies.** The embed is pure Swift — no python, no exiftool, no external
processes. It reads each clip's Sony metadata, builds the Adobe `xmpDM` markers, and
writes them as an Adobe XMP `uuid` box into the clip's reserved `free` box **before
`mdat`** (where Premiere reads it), neutralizing any prior Adobe XMP box. `mdat` never
moves, so chunk offsets stay valid — no rewrite, no faststart. Works on a copy.

## Build & run

```bash
cd app
swift run                 # dev: launches the window
# or build a real double-click .app:
bash build_app.sh         # → app/dist/Shot Mark Embedder.app   (cat = app icon)
open "dist/Shot Mark Embedder.app"
```

Headless (for testing/scripting):

```bash
.build/release/EmbedMarkers --embed-cli /output/dir CLIP1.MP4 CLIP2.MP4 …
```

## Design

White gradient background with a tongue-pink accent (tied to the cat in the lower-left,
whose white background is dropped via a multiply blend). Dashed drop zone, output picker,
and a determinate progress bar with a per-file status line.

## Files

```
Package.swift
Sources/EmbedMarkers/EmbedMarkersApp.swift   UI + headless CLI entry
Sources/EmbedMarkers/Embedder.swift          pure-Swift Sony-marks → embedded XMP
build_app.sh                                 assembles the .app (icon from the cat)
```

## Verified

`--embed-cli` on SIMON7034.MP4 → 4 markers at startTimes 93/213/329/416 (exiftool reads
them back), ffprobe-valid, Sony NRT preserved, original untouched.

> One thing to confirm in your Premiere: the app appends the XMP at EOF (vs the
> exiftool path which placed it before `mdat`). Both are standard locations and read
> back via exiftool; verify a Shot-Mark file imports with markers in your Premiere.
