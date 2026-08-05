# Sony Shot Marker — DaVinci Resolve panel

Reads Sony Shot Marks straight out of your footage and adds them as native Resolve markers.
Your media is only ever read.

Resolve will not read markers out of a media file no matter how they're embedded — that's a
Resolve limitation, and even Sony's own Catalyst plugin can't import Shot Marks there. So
this reads the camera's metadata itself and applies the marks through Resolve's scripting
API. Same marks, same frames, same numbers as the Premiere panel.

## Install

```bash
../install_resolve_macos.sh
```

```powershell
powershell -ExecutionPolicy Bypass -File ..\install_resolve_windows.ps1
```

It's one file copied into Resolve's Utility scripts folder, so you can do it by hand:

- macOS: `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/`
- Windows: `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\`

No restart needed — Resolve rescans the Scripts menu when you open it. The installers also
clear the older `Resolve_ApplyShotMarks.py`, which this replaces, so you don't end up with
two menu entries doing the same thing.

## Use

Import your footage, then `Workspace ▸ Scripts ▸ SonyShotMarker`.

- **Scan** shows what's there and changes nothing.
- **Apply Shot Marks** writes them.
- **Colour** — any of Resolve's sixteen marker colours. Green by default: Resolve's own
  first colour is Blue, and green reads as "these are the keepers" rather than as one more
  blue thing on the timeline.
- **Also mark clips already cut into timelines** — on by default, and it matters. Resolve
  doesn't retroactively push a source clip's markers onto instances you've already edited
  in, so those have to be marked directly. Positions are offset by the item's in-point.
- **Also mark automatic Rec Start / Rec End** — off by default; they're on every clip.

Safe to run twice: it compares against markers already present and only adds what's missing.

## Requirements

Nothing beyond Resolve. One file, standard library only — no ffmpeg, no exiftool, no pip
packages — running in Resolve's built-in Python. Works in free Resolve as well as Studio.

If Fusion's UI toolkit isn't reachable, it falls back to running headless and printing to
`Workspace ▸ Console`, so it still works in environments where the window can't open.

## Reading a card offload without loading it

On an A7S III the Sony metadata sits in a `meta` box **after** the video data — byte
335,813,843 of the 335 MB test clip. The obvious implementation reads the whole file to
find 2.5 KB of XML, which is fine for one clip and ruinous for a card.

So this walks the MP4 box headers, skips `mdat` entirely, and reads only the small boxes.
On that same clip it peaks at **16 MB of memory instead of 351 MB** — and the cost doesn't
grow with the size of your footage.

If the metadata box has been stripped by a transcode, it falls back to the `Cxxxx M01.XML`
sidecar. For MXF it tries the sidecar first, since that container can't be walked cheaply.

## The other Resolve tools

Two older scripts are still in the repo and still work. They aren't deprecated in the
"about to be deleted" sense — they do things this panel doesn't, and the installers here
deliberately leave them alone.

**`tools/Resolve_ApplyShotMarks.py`** — the original drop-in. Runs headless the instant you
pick it from the Scripts menu: no window, no options, marks everything, prints to
`Workspace ▸ Console`. If you want a one-click batch with nothing to click through, or
you're driving Resolve from an automated setup, it's the better tool. It shares this
panel's seek-based reader, so it's just as safe on a big card.

**`tools/resolve_shotmarks.py`** — a command-line tool that marks **one specific clip**
rather than the whole project, with `--dry-run` and `--include-auto`. Useful for scripting
a per-clip pipeline. Needs `Preferences ▸ System ▸ General ▸ "External scripting using" =
Local`, since it drives Resolve from outside.

The panel is what most people want. These are for the cases it doesn't cover.

## Testing without Resolve

Point it at a clip from a terminal and it prints what it would add:

```bash
python3 SonyShotMarker.py /path/to/CLIP.MP4
```

The suite in `tests/test_resolve_reader.py` covers the reader — including a test that fails
if any read reaches into the media payload, which is the guarantee that makes this safe to
run against originals.

```bash
python3 -m pytest -q tests
```
