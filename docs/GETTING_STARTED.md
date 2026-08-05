# Getting started

Plain-English walkthrough. No command line, no prior setup assumed. If you can install an
app, you can do this.

**What this does:** your Sony camera can flag exact moments while you're recording — you
press a button mid-take and it remembers that frame. Premiere and Resolve normally throw
those flags away when you import. This puts them back, as markers you can actually see and
jump between.

---

## Contents

1. [Turn the feature on in your camera](#1-turn-the-feature-on-in-your-camera) — one minute, once
2. [Pick which tool you want](#2-pick-which-tool-you-want)
3. [Premiere Pro](#3-premiere-pro)
4. [DaVinci Resolve](#4-davinci-resolve)
5. [When something doesn't work](#5-when-something-doesnt-work)
6. [Questions people actually ask](#6-questions-people-actually-ask)

---

## 1. Turn the feature on in your camera

If you've never used Shot Marks, nothing here will find anything, because there's nothing
to find yet. Do this first.

On your Sony body:

```
MENU → Setup → Operation Customize → Custom Key/Dial Set
```

Make sure you're on the **recording** tab (not playback). Pick a button you can hit without
looking — **C1** is the usual choice, and the **shutter button** works well if you're not
also taking stills — and set it to **Add Shot Mark1**.

That's it. Now, while the camera is rolling, press it whenever something good happens. The
take where the line landed. The moment the dog finally sat. You'll get a flag on that exact
frame.

If you want a second flavour — say one for keepers and one for cutaways — map another
button to **Add Shot Mark2**. Everything here reads both and keeps them separate.

> **Wait, is it recording them?** There's no big on-screen confirmation on most bodies. The
> way to check is to shoot ten seconds, press the button once, then load that clip into any
> of the tools below. If a mark shows up, it's working.

---

## 2. Pick which tool you want

There are three, and they do the same job in different ways. You do **not** need all of
them.

| If you… | Use |
|---|---|
| Edit in **Premiere** and don't want extra copies of your footage | the **Premiere panel** |
| Edit in **DaVinci Resolve** | the **Resolve panel** |
| Want the marks baked into the file so they work anywhere, on any machine, with nothing installed | the **Shot Mark Embedder app** |

The two panels read your footage and leave it completely untouched. The app makes a *copy*
of each clip with the marks written inside — slower and uses disk space, but then the marks
travel with the file. Hand that copy to another editor and the markers are just there.

Most people want a panel. Grab everything from the
[**Releases page**](https://github.com/lelanddutcher/SonyShotMarker/releases).

---

## 3. Premiere Pro

**You need Premiere Pro 25.6 or newer.** Check under `Premiere Pro → About`. Older versions
can't run this kind of plugin at all — that's an Adobe thing, not something we can work
around.

### Install

Download `Sony-Shot-Marker-<version>.ccx` from the Releases page and **double-click it**.
Creative Cloud installs it. Restart Premiere.

If double-clicking does nothing, use the manual installer instead: download the `.zip`,
unzip it, and run the file inside for your platform —
`install_premiere_macos.sh` or `install_premiere_windows.ps1`. **Quit Premiere first**; it
only looks for plugins when it starts.

### Use it

1. Open your project and import your Sony clips as normal.
2. `Window → Extensions → Sony Shot Marker`
3. Click the clips you want in the **Project panel**.
4. Press **Scan**. This only looks — it changes nothing. You'll see how many marks each
   clip has and their timecodes.
5. Press **Apply Shot Marks**.

Markers appear on the clips. Open a clip in the Source monitor and they're on the ruler; the
Markers panel lists them with the timecode and camera model.

**Things worth knowing:**

- **Nothing selected?** It scans the whole project and tells you it did.
- **Colour** — pick any of Premiere's eight. Green by default.
- **Run it twice?** Fine. It checks what's already there and only adds what's missing.
- **Undo** — one Cmd/Ctrl-Z removes everything it just added.
- **"Also mark automatic Rec Start / Rec End"** — the camera adds its own marks at the start
  of every clip. Off by default because they're noise. Tick it if you want them.

---

## 4. DaVinci Resolve

Works in **free Resolve and Studio**.

### Install

Download `Sony-Shot-Marker-Resolve-<version>.zip`, unzip it, and run
`install_resolve_macos.sh` or `install_resolve_windows.ps1`.

Or do it by hand — it's one file. Copy `SonyShotMarker.py` into:

- **macOS:** `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/`
- **Windows:** `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\`

No restart needed.

> On macOS the Library folder is hidden. In Finder, press `Cmd+Shift+G` and paste the path.

### Use it

1. Import your footage into your Resolve project.
2. `Workspace → Scripts → SonyShotMarker`
3. **Scan** to look, **Apply Shot Marks** to commit.

**Also mark clips already cut into timelines** is on by default, and you want it. Resolve
doesn't push a source clip's markers onto clips you've already edited into a timeline, so
those have to be marked separately. If you've started cutting, leave this ticked.

---

## 5. When something doesn't work

### "It says no Shot Marks found"

Most likely there genuinely aren't any. In order of likelihood:

1. **The camera button wasn't mapped**, or was mapped on the playback tab instead of the
   recording tab. Go back to [step 1](#1-turn-the-feature-on-in-your-camera).
2. **You pressed it before hitting record.** The marks live inside the clip, so the camera
   has to be rolling.
3. **These aren't the original camera files.** Transcodes, proxies, anything that's been
   through a converter — the metadata is usually gone. Use the files straight off the card.
   (If the camera's `Cxxxx M01.XML` file came across with them, it'll find the marks there
   instead.)
4. **It's not a Sony**, or not a Sony that writes XAVC.

### The Premiere panel isn't in the Window → Extensions menu

Premiere only looks for plugins at launch — quit it fully and reopen. If it's still missing,
check `Premiere Pro → About` says 25.6 or newer.

### The Resolve window doesn't appear

Check `Workspace → Console`. If you see `Sony Shot Marker - starting...` the script ran and
anything that went wrong is printed right below it. If the Console is empty, the script
isn't installed where Resolve is looking — recheck the folder path above.

### Markers are in the wrong place

They shouldn't be — positions are computed exactly, not approximated. If they're genuinely
off, that's a bug worth reporting. Include the camera model and frame rate.

### Premiere shows old or missing markers after re-importing

Premiere caches clip metadata by file path. If you imported a file before, it may show you
what it remembers rather than what's on disk. Import into a fresh project or bin to check.

### Still stuck

[Open an issue](https://github.com/lelanddutcher/SonyShotMarker/issues) with your camera
model, your Premiere or Resolve version, and what you expected versus what happened. If you
can attach the camera's `Cxxxx M01.XML` file for a clip that isn't working, that's the
single most useful thing — it's a small text file and it contains the marks.

---

## 6. Questions people actually ask

**Will this damage my footage?**
The two panels never write to your media at all — files are opened read-only and left
byte-for-byte identical. The **app** is different: it writes *copies* and leaves your
originals alone, but it does write files, so check the copies play before you delete
anything. There's a fuller warning in the [main README](../README.md).

**Do I need to run this before I start editing?**
No. Run it whenever. In Resolve, if you've already cut clips into a timeline, leave the
timeline option ticked so those get marked too.

**Can I run it on a whole card?**
Yes, and that's the intended use. The panels don't read your video data — only the small
metadata block — so a folder of 100 GB clips costs about as much as a folder of small ones.

**Does it need internet, an account, or a subscription?**
No, no and no. It's free and open source, it runs entirely on your machine, and it doesn't
phone anywhere.

**Sony has an official plugin. Why this?**
Sony's Catalyst Prepare plugin puts a large watermark over your footage unless you pay for
Catalyst Prepare, and it's locked to Premiere versions from several years ago. Sony's own
documentation says its Resolve version can't import Shot Marks at all.

**Which cameras work?**
Built and verified on the **A7S III**. The **FX3, FX30, FX6, A1 and A7 IV** write the same
metadata and should work — not every one has been tested personally. If yours doesn't,
please open an issue; that's how the list gets confirmed.

**What formats?**
`.MP4`, `.MOV` and `.MXF` for the panels. The app writes to `.MP4` and `.MOV`.
