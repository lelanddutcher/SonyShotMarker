# Sony Shot Marker — Premiere Pro panel

A Premiere Pro panel that reads Sony Shot Marks **out of your footage, in place**, and adds
them as native clip markers. It never writes to your media. There is no copy, no re-wrap,
no "embedded" duplicate to keep track of — you point it at the clips you already imported
and the marks show up.

This is the no-copy path. If you'd rather bake the marks into a copy so they travel with
the file into any NLE, use the [Shot Mark Embedder app](../README.md) instead. Same marks,
different trade-off.

## Install

**With the UXP Developer Tool** (also how you'd package it for Adobe's marketplace):

1. Open UDT, click **Add Plugin**, pick `plugin/premiere-uxp/manifest.json`.
2. **Load**. The panel appears under `Window ▸ Extensions ▸ Sony Shot Marker`.

**Without UDT**, from a release download or a checkout:

```bash
plugin/install_premiere_macos.sh
```

```powershell
powershell -ExecutionPolicy Bypass -File plugin\install_premiere_windows.ps1
```

Both copy the panel into Premiere's side-load folder and register it. Quit Premiere first —
it reads the plugin list at launch. `--uninstall` / `-Uninstall` removes it again.

Needs Premiere Pro 25.6 or newer (that's when UXP went GA). Verified on 26.2.

## Use

1. Select some Sony clips in the Project panel.
2. **Scan** to see what's there — this changes nothing.
3. **Apply Shot Marks**.

Markers land at the exact frame you pressed C1 or C2 on the camera, named `Shot Mark 1` /
`Shot Mark 2`, with the source timecode and camera model in the comment.

Markers are **green** by default. Premiere's colour index 1 is red, and a timeline full of
red markers reads as an error state rather than as the takes you liked. All eight of
Premiere's marker colours are in the panel — pick one and it applies to everything that
run.

Running it twice is safe: it compares against the markers already on the clip and only adds
what's missing. Everything it adds is one undo step.

`Also mark automatic Rec Start / Rec End` includes the marks the camera writes on its own.
Off by default, because they're on every clip and mostly noise.

## Why it doesn't need to copy your footage

Sony writes Shot Marks as SMPTE KLV essence marks inside a `NonRealTimeMeta` XML document.
On an A7S III that document lives in a top-level `meta` box **after** the media payload —
on the 335 MB test clip it starts at byte 335,813,843.

So the panel walks the MP4 box headers, skips `mdat` entirely, and reads only the small
boxes. Finding four marks in that 335 MB clip costs **15 reads totalling 283 KB — 0.08% of
the file**. That's what makes this safe to point at a card offload instead of a proxy: the
cost doesn't grow with the size of your footage.

Files are opened read-only, and nothing in the plugin has a write path.

If the metadata box has been stripped (a transcode, a partial offload), it falls back to the
`Cxxxx M01.XML` sidecar next to the clip. For MXF it tries the sidecar first, since that
container can't be walked cheaply.

## Layout

```
premiere-uxp/
  manifest.json          UXP v5 manifest
  index.html             the panel
  styles.css
  src/
    main.js              controls, rendering
    premiere.js          the only file that talks to Premiere
    lib/
      shotmarks.js       Sony NRT / KLV / timecode parser  (no UXP dependency)
      clipmarks.js       where to look and in what order   (no UXP dependency)
      paths.js           macOS + Windows path handling     (no UXP dependency)
      reader-uxp.js      positional reads over UXP's fs
```

The three `no UXP dependency` files are the reason this is testable. They take bytes through
a two-method `Reader` interface, so the same code runs under Node against real Sony footage
in CI and inside Premiere at runtime.

## Tests

```bash
node tests/js/test_shotmarks.js   # parser, timecode, byte-level reads
node tests/js/test_clipmarks.js   # embedded vs sidecar resolution
node tests/js/test_panel.js       # the panel end to end, against a mock UXP + Premiere
```

They run on a bare machine using synthetic fixtures, and pick up the real `SIMON7034.MP4`
automatically if it's present (or set `SHOTMARK_SAMPLE`). The parser suite includes a
field-by-field differential against `tools/sony_shotmark.py`, so the JavaScript port can't
drift from the engine that produced the XMP embeds people already rely on.

`test_panel.js` loads `index.html` for real — same script tags, same order — into a minimal
DOM with UXP-shaped stubs, then clicks the controls. It exists because the first version of
this panel rendered perfectly and did nothing, and no amount of parser testing would have
caught that. The stubs are deliberately strict — `fs.read` refuses a typed array the way
UXP does, and creating a marker action outside a transaction throws — because a lenient
mock let both of those bugs reach Premiere.

## What UXP will do to you if you edit this

Every one of these cost a debugging round, and every one now has a test.

**Only printable ASCII ships.** UXP's parser rejects bytes Node accepts, and the failure is
brutal: `SyntaxError: Invalid or unexpected token` at a position that looks perfectly fine,
the module never defines itself, and every control goes dead with nothing on screen to
explain it. Two different bytes did this — a NUL that ended up inside a string literal
(JavaScript *permits* NUL in strings, so Node parsed it and the whole suite passed green
while Premiere refused to load the file), and an em dash in a comment, since UXP does not
decode sources as UTF-8. `test_panel.js` and `package_plugin.py` both reject anything
outside printable ASCII plus tab/LF/CR.

**Scripts go in `<head>`.** A script at the end of `<body>` may never execute at all.

**Controls are `<div>`s, not `<button>`s.** A UXP `<button>` is a native widget: it draws
its own label from its text content and does not paint or lay out children. A styled
`<div>` inside one never shows, and a button with no text collapses to zero size — which is
why the segmented control stayed grey through three attempted fixes and the colour dots
were invisible entirely. `background-color` renders on a `<div>`, not on a `<button>` and
not on a `<span>`. Divs receive clicks fine.

**No child combinator, no `text-transform`.** `parent > child` is not honoured, so a fill
cannot be styled from its parent's state class — main.js swaps single-purpose classes on
each element by id instead. `text-transform` is ignored, so text is written in the case it
should display.

**Actions must be created inside the transaction.** Building `createAddMarkerAction` calls
first and adding them to the compound action later fails with "Script action failed to
execute." Premiere binds an action to the transaction open when it is created.

## Packaging

```bash
python3 plugin/package_plugin.py
```

Writes `dist/plugin/Sony-Shot-Marker-<version>.ccx` (what you upload to Adobe Developer
Distribution or load in UDT) and a `.zip` with the install scripts for people side-loading.
Both are byte-reproducible from the same source, so a release can be verified by hash.

`scripts/release.sh` builds and attaches both to the GitHub release automatically, alongside
the macOS app and the Resolve script.

> **Distribution note:** the manifest `id` is `7e04f014`, the ID issued by Adobe Developer
> Distribution. It has to match the portal exactly or the upload is rejected, so treat it as
> fixed — it is not a name you can tidy up. The human-readable name is the separate `name`
> field, and the side-load folder is derived as `<id>_<version>`.
