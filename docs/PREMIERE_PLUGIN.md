# Developing for Premiere Pro — Options, Trade‑offs, Recommendation (2026)

Research current as of **mid‑2026**. Sources are linked inline.

## The headline: you probably don't need a plugin

Premiere persists clip markers in **XMP** using Adobe's Dynamic Media (`xmpDM`)
schema, and it **reads markers back from a sidecar `.xmp` at import time**. So a
plain command‑line tool that writes a correctly‑formatted sidecar gets native clip
markers into Premiere with **no install, no signing, no Adobe API**. This repo's
`tools/sony_shotmark.py --xmp` does exactly that. Treat a panel as an optional
"nicer UX" layer, not a requirement.

Grounded in your machine: your Premiere already writes sidecars as the **appended**
form (`LAD38351.MP4.xmp`, `120_1291.MXF.xmp`), so we target `LR4253.MP4.xmp`.

---

## The four ways to extend Premiere Pro

| Approach | Lang | Status (2026) | Good for | Verdict here |
|----------|------|---------------|----------|--------------|
| **UXP** | JS/React | **GA since Premiere 25.6** ([blog](https://blog.developer.adobe.com/en/publish/2025/12/uxp-arrives-in-premiere-a-new-era-for-plugin-development)) | dockable panels, modern UI, scripting | ✅ the panel path if we build one |
| **CEP** | HTML5 + ExtendScript | supported but **superseded**, ~1‑yr sunset after 25.6 ([Adobe‑CEP](https://github.com/Adobe-CEP/Samples/blob/master/PProPanel/ReadMe.md)) | legacy panels | ⚠️ avoid for new work |
| **ExtendScript** | `.jsx` | frozen, **EOL Sept 2026** ([community](https://community.adobe.com/questions-729/extendscript-to-uxp-for-premiere-pro-1553924)) | quick automation | ⚠️ short shelf‑life |
| **C++ SDK** | C/C++ | active | importers/exporters/effects | ❌ overkill for markers |

### UXP (Unified Extensibility Platform) — the modern panel
- **GA in Premiere 25.6** (public beta from 25.2, Dec 2024). JS, ES2015, React via
  Spectrum Web Component wrappers. TypeScript types: `npm i -D @adobe/premierepro`,
  runtime `require('premierepro')`. ([changelog](https://developer.adobe.com/premiere-pro/uxp/changelog/), [intro](https://developer.adobe.com/premiere-pro/uxp/introduction/))
- **Markers are first‑class.** `Markers` class is **async / transactional** (build an
  Action, run it in a transaction — calls don't block the UI). ([Markers class](https://developer.adobe.com/premiere-pro/uxp/ppro_reference/classes/markers/))
  ```js
  const ppro    = require("premierepro");
  const project = await ppro.Project.getActiveProject();
  const markers = ppro.Markers.getMarkers(clipProjectItem);     // owner = ClipProjectItem or Sequence
  const start   = ppro.TickTime.createWithSeconds(12.345);
  const action  = markers.createAddMarkerAction("Shot Mark 1", "Comment", start,
                                                ppro.TickTime.TIME_ZERO, "OK");
  await project.executeTransaction(/* compound action with `action` */);
  // ⚠ verify TickTime/transaction signatures against the live ref + the `premiere-api` sample
  ```
  Reference sample with working markers: [AdobeDocs/uxp-premiere-pro-samples](https://github.com/AdobeDocs/uxp-premiere-pro-samples).
- **Packaging/distribution:** `.ccx` via the **UXP Developer Tool (UDT)**; dev/OSS
  users can side‑load unpackaged from UDT with **no signing**. Marketplace optional. ([package](https://developer.adobe.com/premiere-pro/uxp/plugins/distribution/package/), [install](https://developer.adobe.com/premiere-pro/uxp/plugins/distribution/install/))

### Legacy ExtendScript marker API (for reference / quick scripts)
`ProjectItem.getMarkers()` → `MarkerCollection`:
```jsx
var it = app.project.rootItem.children[0];
var mk = it.getMarkers().createMarker(12.345);   // SECONDS -> Marker
mk.name = "Shot Mark 1"; mk.comments = "OK"; mk.setTypeAsComment();
// mk.setColorByIndex(1, 0);   // 0..7 colors
```
([MarkerCollection](https://ppro-scripting.docsforadobe.dev/collection/markercollection/), [Marker](https://ppro-scripting.docsforadobe.dev/general/marker/)). Works today, but EOL Sept 2026 — don't build the product on it.

### C++ SDK — when you'd actually need it
Only to register a **new importable format** or an effect/exporter; importers install
to `…/Adobe/Common/Plug-ins/7.0/MediaCore/`. Sony XAVC already imports into Premiere,
so this buys nothing for markers. ([C++ SDK](https://ppro-plugins.docsforadobe.dev/))

---

## XMP — the no‑plugin path, in detail

Namespace `http://ns.adobe.com/xmp/1.0/DynamicMedia/` (`xmpDM`). Markers live in
`xmpDM:Tracks` (an `rdf:Bag` of tracks); each track has `trackName`, `trackType`
(**`Comment`** for normal clip markers), `frameRate`, and `markers` (an `rdf:Seq`).
([Adobe Track type](https://developer.adobe.com/xmp/docs/XMPNamespaces/XMPDataTypes/Track/), [Marker type](https://developer.adobe.com/xmp/docs/XMPNamespaces/XMPDataTypes/Marker/))

**Frame‑rate notation** `f<num>` or `f<num>s<basis>` = `num/basis` fps:

| fps | string |
|-----|--------|
| 23.976 | `f24000s1001` |
| 29.97 | `f30000s1001` |
| 59.94 | `f60000s1001` |
| 119.88 | `f120000s1001` |
| 25 / 30 / 50 / 60 | `f25` / `f30` / `f50` / `f60` |

**Marker `startTime`/`duration` are integer frame counts at the track frame rate.**
Because we set the track rate to the capture fps, **`startTime` == Sony `frameCount`**
— frame‑exact, no rounding. Map: `name`←friendly, `comment`←label+TC, `startTime`←F,
`duration`←0 (point). A per‑marker `xmpDM:guid` helps Premiere de‑dupe on re‑import.

Minimal example this tool emits (`samples/LR4253.MP4.xmp`):
```xml
<rdf:Description xmpDM:trackName="Shot Marks" xmpDM:trackType="Comment"
                 xmpDM:frameRate="f120000s1001">
  <xmpDM:markers><rdf:Seq>
    <rdf:li xmpDM:startTime="600" xmpDM:duration="0"
            xmpDM:name="Shot Mark 1" xmpDM:comment="_ShotMark1 | 03:41:59;08"/>
  </rdf:Seq></xmpDM:markers>
</rdf:Description>
```

**Caveats to test on your build** ([Adobe metadata](https://helpx.adobe.com/premiere-pro/using/metadata.html), [Creative Impatience](https://www.creativeimpatience.com/premiere-pro-clip-markers-solved/)):
1. **Sidecar name** — your Premiere uses `clip.MP4.xmp` (appended). We default to that.
2. **Place the sidecar before first import**; Premiere merges it on import.
3. **Premiere may overwrite the sidecar** when "Write clip markers to XMP" is on and
   you edit markers in‑app. Keep the generated `.xmp` backed up.
4. Enable **Preferences ▸ Media ▸ "Write clip markers to XMP"** for round‑tripping.

### Embedding the markers *inside* the file (vs. a sidecar)

Same `xmpDM` payload, stored in the media instead of beside it. For ISO‑BMFF MP4
(`mp42`/`iso6` — what the A7S III writes) the XMP convention is a **top‑level box of
type `uuid` whose first 16 bytes are the Adobe XMP UUID `BE7ACFCB97A942E89C71999491E3AFAC`**,
followed by the `<?xpacket…?>`‑wrapped XMP. Premiere/Bridge/exiftool all read XMP from
that box.

Offset safety matters: an MP4's `stco`/`co64` tables hold **absolute** chunk offsets
into `mdat`. In the A7S III files `moov` sits **after** `mdat`, so the safe edits are
(a) append the XMP `uuid` box at EOF — nothing moves — or (b) let a tool that rewrites
offsets do it. `tools/sony_shotmark.py --embed` uses **exiftool**, which writes the
`uuid` box in the spec‑correct place and rewrites offsets (it also faststart‑reorders
`moov` ahead of `mdat`, which is harmless/beneficial). Verified on `SIMON7034.MP4`:
the embedded copy keeps both the Sony essence marks and 4 readable `xmpDM` markers, and
`ffprobe` still validates it. **The original is never written** — `--embed` always works
on a copy and refuses an output path equal to the input.

Two known unknowns to settle with Premiere open: whether Premiere reads the embedded
`uuid` XMP for `.mp4` as readily as a sidecar, and whether it prefers the marks at the
clip's native rate vs a conformed rate. Both are quick to confirm via the `premiere-pro`
MCP or a manual import.

---

## Prior art / the gap we fill
- **Sony Catalyst Prepare Plugin** is a **C++ MediaCore plugin**, **version‑locked to
  Premiere 15.4–22.4** and effectively abandoned; users report a watermark unless paid
  and breakage on current Premiere. ([Adobe community](https://community.adobe.com/t5/premiere-pro-discussions/catalyst-prepare-plugin-1-0-0-62/td-p/13201676), [Sony Creators' Cloud](https://creatorscloud.sony.net/catalog/en-us/catalyst/index.html))
- OSS XAVC metadata readers exist ([telemetry-parser](https://github.com/AdrianEddy/telemetry-parser),
  [xavc_rtmd2srt](https://github.com/SK-Hardwired/xavc_rtmd2srt)) but **none write Premiere
  markers**. That half — Shot Marks → Premiere — is the open niche.

---

## Recommended architecture

```
   Sony .MP4 / M01.XML
          │  parse NonRealTimeMeta → KlvPacketTable + LtcChangeTable   (done: sony_shotmark.py)
          ▼
   [ Shot Marks: label, frameCount, elapsed, source TC ]
          │
   ┌──────┴───────────────┬─────────────────────────┐
   ▼ Tier 1 (ship first)  ▼ Tier 2 (nice UX)         ▼ Tier 3 (live)
   XMP sidecar            UXP panel "Import Sony      Premiere MCP /
   clip.MP4.xmp           Shot Marks" → Markers API   automation: add_marker
   (no install)           (.ccx, future‑proof)        on the open project
```

- **Tier 1 — XMP sidecar (recommended first):** zero install, future‑proof, already
  built. Validate the round‑trip on one real marked clip, then it's done.
- **Tier 2 — UXP panel:** one‑click "apply to selected clips," works post‑import,
  distributable `.ccx`. Build only if XMP proves flaky or a GUI is wanted.
- **Tier 3 — live automation:** the connected `premiere-pro` MCP exposes `add_marker`
  / `list_markers` — useful to *prove* the pipeline against a running Premiere and for
  scripted batch runs, but not a distributable artifact.
