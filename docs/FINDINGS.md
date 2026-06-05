# Where Sony Hides "Shot Marks" — Reverse‑Engineering Notes

> Goal: find the frame‑accurate Shot Marks a camera op drops with the **C1 / C2
> "Add Shot Mark1 / Add Shot Mark2"** custom button, and translate them to timecode
> that Premiere Pro can use.

These notes are derived empirically from two real clips shot on a **Sony A7S III
(`ILCE‑7SM3`)**, XAVC‑HS, 4K, **119.88p**, S‑Log3:

| File | Size | Duration | Start TC | Camera | User marks |
|------|------|----------|----------|--------|------------|
| `SIMON7034.MP4` | 320 MB | 17.5 s (525 fr) @ 29.97p | `20:44:56;24` DF | A7S III, XAVC‑I 4K 4:2:2 10‑bit | **4 × Shot Mark1** ✅ |
| `LR4154.MP4` | 192 MB | 6.006 s (720 fr) @ 119.88p | `02:41:36;01` DF | A7S III + 28‑75mm | none (only `_RecStart`) |
| `LR4253.MP4` | 832 MB | 33.0 s (3960 fr) @ 119.88p | `03:41:54;08` DF | A7S III + 28‑75mm | none (only `_RecStart`) |

`SIMON7034` is real C1‑button footage and is the live proof the format below is
correct — its 4 marks are byte‑identical in the embedded MP4 metadata and the
`M01.XML` sidecar, at capture frames 93 / 213 / 329 / 416.

---

## 1. The container

A Sony XAVC `.MP4` from an Alpha/FX body carries **three** tracks:

```
stream 0  video  h264 (avc1)              "Video Media Handler"
stream 1  audio  pcm_s16be (twos)         "Sound Media Handler"
stream 2  data   rtmd                     "Timed Metadata Media Handler"   <-- KLV
```

Plus a small **`NonRealTimeMeta` XML document embedded near the end of the file**
(on professional media — SxS/CFexpress in pro cameras — the same document is the
`Cxxxx M01.XML` sidecar). On these A7S III clips the XML sits at byte offset
`~file_size − 2137`.

There are therefore **two** metadata channels, and a Shot Mark can live in either:

| Channel | Codec/Format | What it holds | Per‑frame? |
|---------|--------------|---------------|------------|
| `NonRealTimeMeta` XML | text/XML | clip summary: duration, **LTC table**, video/audio fmt, device, lens, **`KlvPacketTable` = the essence‑mark list** | no, clip‑level |
| `rtmd` stream (track 2) | SMPTE KLV | per‑frame acquisition data (exposure, WB, gyro, timecode…) | yes, 1 set/frame |

**Shot Marks are essence marks, and they are listed in the `KlvPacketTable` of the
`NonRealTimeMeta` XML.** They are *not* stored as ASCII text anywhere else — the
`rtmd` stream is pure binary KLV with no essence‑mark key in these clips.

---

## 2. The essence‑mark record

Inside `NonRealTimeMeta`:

```xml
<KlvPacketTable>
  <KlvPacket key="060E2B34010101050301020A02000000"
             frameCount="0"
             lengthValue="095F5265635374617274"
             status="spot"/>
</KlvPacketTable>
```

Decode of one record:

| Attribute | Meaning |
|-----------|---------|
| `key` | SMPTE Universal Label `06 0E 2B 34 01 01 01 05 03 01 02 0A 02 00 00 00` = **"Comment / Essence Mark"**. Always this value for marks. |
| `frameCount` | mark position, in **capture frames** from clip start (here 119.88 fps), 0‑based |
| `lengthValue` | BER‑style: **first byte = string length**, the rest = ASCII label |
| `status` | `spot` (a point), vs `start`/`end` for ranged marks |

`lengthValue` decoding — first byte is the length, remainder is ASCII:

```
09 5F 52 65 63 53 74 61 72 74            len=9   -> "_RecStart"   (automatic, every clip)
0A 5F 53 68 6F 74 4D 61 72 6B 31         len=10  -> "_ShotMark1"  (C1 button)
0A 5F 53 68 6F 74 4D 61 72 6B 32         len=10  -> "_ShotMark2"  (C2 button)
08 5F 52 65 63 45 6E 64                  len=8   -> "_RecEnd"     (automatic)
```

So a user Shot Mark pressed 5 s into a 119.88p clip serialises as:

```xml
<KlvPacket key="060E2B34010101050301020A02000000"
           frameCount="600" lengthValue="0A5F53686F744D61726B31" status="spot"/>
```

(`600 / 119.88 ≈ 5.005 s`.)

### ⚠️ Important finding about the two sample clips

**Both `LR4154` and `LR4253` contain only the automatic `_RecStart` mark at frame 0
— no user Shot Marks were pressed on‑camera.** This was verified three ways:

1. `KlvPacketTable` has exactly one `<KlvPacket>` (the frame‑0 `_RecStart`).
2. The literal strings `_ShotMark1`/`_ShotMark2` appear **0 times** in either file.
3. The `rtmd` stream's per‑frame KLV key set is **identical across all 720 / 3960
   frames** — no per‑frame mark anomaly.

To validate end‑to‑end we therefore injected three marks into a copy of `LR4253`'s
metadata, byte‑for‑byte as the camera writes them, and round‑tripped them
(see `samples/LR4253_MARKED_M01.xml` and the tool output). **To prove it on real
footage, record a short test clip with C1 mapped to "Add Shot Mark1" and drop a few
marks** — the tooling will then read them with no changes.

---

## 3. The timecode: `LtcChangeTable`

```xml
<LtcChangeTable tcFps="30" halfStep="true">
  <LtcChange frameCount="0"    value="48544103" status="increment"/>
  <LtcChange frameCount="3959" value="49A74203" status="end"/>
</LtcChangeTable>
```

The `value` is **packed LTC: 4 bytes, BCD, ordered `FF SS MM HH`**, with SMPTE flag
bits in the high bits:

```
byte0 (frames):  bit7 = colour‑frame, bit6 = DROP‑FRAME, bits5‑0 = BCD frames  (mask 0x3F)
byte1 (seconds): bit7 = flag,                              bits6‑0 = BCD        (mask 0x7F)
byte2 (minutes): bit7 = flag,                              bits6‑0 = BCD        (mask 0x7F)
byte3 (hours):                                             bits5‑0 = BCD        (mask 0x3F)
```

Worked example, `value="48544103"`:

```
0x48 -> frames : 0x48 & 0x3F = 0x08 = 8     (bit6 set -> DROP FRAME)
0x54 -> seconds: 0x54 & 0x7F = 0x54 = 54
0x41 -> minutes: 0x41 & 0x7F = 0x41 = 41
0x03 -> hours  : 0x03 & 0x3F = 0x03 = 3
=> 03:41:54;08   (drop‑frame, because the DF flag bit is set)
```

The end record `49A74203` decodes to `03:42:27;09` — i.e. start + 33 s + 1 frame,
matching the 3960‑frame / 33 s duration. ✔

`tcFps="30"` + the DF flag → a **29.97 DF** timebase (expected for 119.88p NTSC media).
`halfStep="true"` is Sony's flag for high‑frame‑rate clips; we sidestep its ambiguity
by translating via elapsed seconds (next section).

---

## 4. Translating a mark to timecode

Given a mark at capture `frameCount = F`:

```
capture_fps_exact = 120000 / 1001        # 119.88  (from <VideoFrame captureFps="119.88p">)
elapsed_seconds   = F / capture_fps_exact
source_timecode   = start_LTC + elapsed_seconds      # rendered at the 29.97 DF base
```

We emit three representations per mark, because different targets want different things:

| Representation | Use |
|----------------|-----|
| `capture_frame` (F) | frame‑exact native position; **also the XMP `startTime`** (see below) |
| `elapsed_seconds` | Premiere scripting `createMarker(seconds)`; format‑agnostic |
| `source_timecode` | the burned‑in/source TC a human reads; CSV import |

Validated output for the injected‑mark test clip:

```
 #  LABEL       KIND               CAP-FRAME   ELAPSED  SOURCE TC
 1  _RecStart   Recording Start (auto)     0    0.000s  03:41:54;08
 2  _ShotMark1  USER SHOT MARK           600    5.005s  03:41:59;08
 3  _ShotMark2  USER SHOT MARK          1500   12.512s  03:42:06;25
 4  _ShotMark1  USER SHOT MARK          3000   25.025s  03:42:19;10
```

### The clean coincidence that makes Premiere integration trivial

Premiere stores clip markers in XMP (`xmpDM`) as an **integer frame count at a
declared track frame rate**. If we declare the track frame rate = the capture fps
(`f120000s1001` for 119.88p), then:

```
xmpDM:startTime  ==  Sony frameCount     (no rounding, frame‑exact)
```

So the Sony number drops straight into the Premiere sidecar. See
[`PREMIERE_PLUGIN.md`](PREMIERE_PLUGIN.md) §XMP.

---

## 5. Repro commands

```bash
# identify the camera / format
exiftool -DeviceManufacturer -DeviceModelName -MajorBrand LR4253.MP4

# carve the embedded NonRealTimeMeta XML
python3 - <<'PY'
d=open("LR4253.MP4","rb").read(); i=d.find(b"<?xml"); j=d.find(b"</NonRealTimeMeta>")
open("LR4253_M01.xml","wb").write(d[i:j+18])
PY
xmllint --format LR4253_M01.xml      # read the KlvPacketTable + LtcChangeTable

# extract the rtmd KLV stream and confirm its per-frame key set
ffmpeg -i LR4253.MP4 -map 0:2 -c copy -f data LR4253.rtmd

# do it all with the tool
python3 tools/sony_shotmark.py LR4253.MP4
```

## 6. Other Sony media on this system (for reference)

| File | Device | Marks |
|------|--------|-------|
| `LR4154.MP4`, `LR4253.MP4` | A7S III video | `_RecStart` only |
| `LD400004.ARW` | A7R IV (`ILCE‑7RM4`) **still** | n/a (photo) |

The numeric‑named `120_*.MXF` / `436_*.MXF` are not Sony Alpha/FX clips; the
`*_LAD04.MP4` family is **Canon EOS R5 Mk II** (`XFVC` brand) renamed by a DIT tool.
