# Adobe Developer Distribution listing — Sony Shot Marker

Copy for the plugin's marketplace listing. Field limits noted where the portal enforces
them. Keep this in sync with what is actually live.

> Careful with the two product names. **Sony Shot Marker** is this plugin: it reads marks
> in place and never touches your media. **Shot Mark Embedder** is the separate macOS app
> that writes marks into a *copy* of each clip. Describing the plugin as "baking marks in"
> is not a wording nit — it is the opposite of what it does, and the no-copy behaviour is
> the reason people would choose it.

---

## Public plugin name  (limit 45)

```
Sony Shot Marker
```

## Subtitle  (limit 30)

```
Read Sony Shot Marks in place
```

Sony's feature is called "Shot Mark", so use that rather than "Cam Marks" — it is the
phrase people will search for. "In place" carries the differentiator in the two words the
field has room for.

## Support email

```
leland@lelanddutcher.com
```

## Help URL

```
https://github.com/lelanddutcher/SonyShotMarker
```

## Description  (limit ~5000)

```
Your Sony camera can drop frame-accurate markers while you roll. Hit C1 mid-take and it
flags the exact frame - one of the most underrated features on the A7S III and the FX
line. The problem is what happens next: you import that footage into Premiere and the
marks are gone. Premiere doesn't speak Sony's metadata, so the work you did on set
evaporates.

Sony Shot Marker fixes that. Select your clips in the Project panel, click once, and your
Shot Marks appear as native Premiere clip markers at the exact frames you pressed the
button.

It reads the marks in place. Nothing is copied, nothing is re-wrapped, and nothing is
written to your media - your camera originals are opened read-only and left byte-for-byte
identical. There is no second copy of your footage to manage and no "embedded" duplicate
to keep track of.

That also makes it quick on real footage. Sony stores Shot Marks in a metadata block that
sits after the video data, so finding them in a 335 MB clip takes about 283 KB of reading,
under 0.1% of the file. The cost doesn't grow with the size of your media, which means you
can point it at a full card offload rather than proxies.

- Markers land frame-exact, computed in Premiere's own tick base with no rounding
- Named Shot Mark 1 / Shot Mark 2, with source timecode and camera model in the comment
- Choose any of Premiere's eight marker colours; green by default
- Run it on a selection or across the whole project
- Scan first to see what's there without changing anything
- Safe to run twice: it only adds what's missing
- Everything it adds is a single undo step
- Optionally include the camera's automatic Rec Start / Rec End marks

Built and verified on the Sony A7S III shooting XAVC-I, XAVC-S and XAVC-HS. Sony writes
Shot Marks the same way across its modern XAVC bodies, so the FX3, FX30, FX6, A1 and A7 IV
should work too. Reads MP4, MOV and MXF, and falls back to the camera's M01.XML sidecar if
a transcode stripped the embedded copy.

Free and open source under the MIT licence. No subscription, no watermark, and no Catalyst
Prepare licence required.
```

## Release notes — 1.0.0  (limit 1000)

```
Sony Shot Marker 1.0.0 - first release.

Reads Sony Shot Marks, the C1 and C2 button presses you make while recording, straight out
of your footage and adds them as native Premiere clip markers.

- Select clips in the Project panel, or scan the whole project
- Scan first to see what's there without changing anything
- Markers land frame-exact, with source timecode and camera model in the comment
- Pick any of Premiere's eight marker colours; green by default
- Safe to re-run: it only adds what's missing, in a single undo step
- Optional automatic Rec Start / Rec End marks

Your media is never modified. Files are opened read-only and left byte-for-byte identical:
no copies, no re-wrap. Reading a 335 MB clip costs about 283 KB, so it stays quick on a
full card offload.

Handles MP4, MOV and MXF, falling back to the camera's M01.XML sidecar. Built and verified
on the A7S III; other modern XAVC bodies use the same metadata.

Requires Premiere Pro 25.6 or newer.
```
