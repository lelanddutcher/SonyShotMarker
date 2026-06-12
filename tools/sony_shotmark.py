#!/usr/bin/env python3
"""
sony_shotmark.py — Extract Sony "Shot Mark" essence marks from XAVC media and
translate them to frame-accurate timecode for Premiere Pro.

WHERE SONY HIDES SHOT MARKS
---------------------------
Sony Alpha / FX / Cinema-Line bodies (A7S III, A1, FX3, FX6, FX30 ...) recording
XAVC-S / XAVC-HS to an SD/CFexpress card embed a per-clip metadata document
*inside* the .MP4 (or as a Cxxxxx M01.XML sidecar on pro media). That document is:

    <NonRealTimeMeta xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20">

Two parts matter:

  1. <LtcChangeTable tcFps="30" halfStep="true">      <- the clip's source timecode
        <LtcChange frameCount="0" value="48544103" .../>   packed LTC, see decode below

  2. <KlvPacketTable>                                  <- the essence-mark list
        <KlvPacket key="060E2B34010101050301020A02000000"
                   frameCount="123" lengthValue="0A5F53686F744D61726B31" status="spot"/>

     The KLV "key" 06 0E 2B 34 01 01 01 05 03 01 02 0A 02 00 00 00 is the SMPTE
     "Comment / Essence Mark" Universal Label. The `lengthValue` is BER-style:
     first byte = string length, remaining bytes = ASCII label:

        09 5F 52 65 63 53 74 61 72 74            -> "_RecStart"   (auto, every clip)
        0A 5F 53 68 6F 74 4D 61 72 6B 31         -> "_ShotMark1"  (C1 button)
        0A 5F 53 68 6F 74 4D 61 72 6B 32         -> "_ShotMark2"  (C2 button)

     `frameCount` is the mark position in *capture* frames from clip start
     (e.g. 119.88 fps for a 4K120 clip), NOT in timecode frames.

TRANSLATION TO TIMECODE
-----------------------
    elapsed_seconds = frameCount / capture_fps_exact
    source_tc       = start_LTC + elapsed_seconds   (rendered at the TC base, DF-aware)

We emit, per mark: capture frame number, elapsed seconds, and source timecode.

Usage:
    sony_shotmark.py <clip.mp4 | clip_M01.xml> [--json out.json] [--csv out.csv]
                     [--fcpxml out.fcpxml] [--premiere-markers out.csv]
"""

from __future__ import annotations
import sys, os, re, json, argparse
from dataclasses import dataclass, asdict, field
from typing import Optional

# SMPTE Universal Label for a comment/essence mark (what Shot Marks ride on).
ESSENCE_MARK_KEY = "060E2B34010101050301020A02000000"

# Sony essence-mark label -> friendly name. Anything starting with "_ShotMark"
# is a user-pressed shot mark; the rest are automatic system marks.
KNOWN_LABELS = {
    "_RecStart": "Recording Start (auto)",
    "_RecEnd":   "Recording End (auto)",
    "_ShotMark1": "Shot Mark 1",
    "_ShotMark2": "Shot Mark 2",
}

# Sony fps strings -> exact rational (num, den)
FPS_EXACT = {
    "23.98": (24000, 1001), "23.976": (24000, 1001), "24": (24, 1),
    "25": (25, 1), "29.97": (30000, 1001), "30": (30, 1),
    "47.95": (48000, 1001), "48": (48, 1), "50": (50, 1),
    "59.94": (60000, 1001), "60": (60, 1), "100": (100, 1),
    "119.88": (120000, 1001), "120": (120, 1),
}


def fps_to_rational(s: str) -> tuple[int, int]:
    key = re.sub(r"[pPiI]$", "", s.strip())          # strip trailing p/i
    if key in FPS_EXACT:
        return FPS_EXACT[key]
    f = float(key)
    # NTSC-family values are *.98/*.94/*.97 -> /1001; otherwise integer
    if abs(f - round(f)) > 0.01:
        return (int(round(f)) * 1000, 1001)
    return (int(round(f)), 1)


# --------------------------------------------------------------------------- #
# Packed-LTC decoding
# --------------------------------------------------------------------------- #
def decode_ltc(value_hex: str) -> dict:
    """
    Sony LtcChange `value` is 4 bytes, BCD, ordered FF SS MM HH, with flag bits:
        frames byte: bit7 = colour-frame, bit6 = drop-frame, bits5-0 = BCD frames
        sec/min byte: bit7 = flag,        bits6-0 = BCD
        hours byte:   bits5-0 = BCD hours
    """
    b = bytes.fromhex(value_hex)
    if len(b) != 4:
        raise ValueError(f"LTC value must be 4 bytes, got {value_hex!r}")
    ff_b, ss_b, mm_b, hh_b = b
    drop = bool(ff_b & 0x40)
    ff = (ff_b & 0x30) >> 4
    ff = ff * 10 + (ff_b & 0x0F)
    ss = ((ss_b & 0x70) >> 4) * 10 + (ss_b & 0x0F)
    mm = ((mm_b & 0x70) >> 4) * 10 + (mm_b & 0x0F)
    hh = ((hh_b & 0x30) >> 4) * 10 + (hh_b & 0x0F)
    return {"h": hh, "m": mm, "s": ss, "f": ff, "drop": drop}


# --------------------------------------------------------------------------- #
# Timecode arithmetic (drop-frame aware)
# --------------------------------------------------------------------------- #
class Timecode:
    def __init__(self, h, m, s, f, fps_num, fps_den, drop):
        self.fps_num, self.fps_den, self.drop = fps_num, fps_den, drop
        self.nominal = round(fps_num / fps_den)          # 24,25,30,50,60,120...
        self.total = self._to_frames(h, m, s, f)

    def _to_frames(self, h, m, s, f):
        fps = self.nominal
        total = ((h * 60 + m) * 60 + s) * fps + f
        if self.drop:
            # 29.97DF drops 2 frames/min except every 10th; 59.94 drops 4; 119.88 drops 8
            drop_per_min = 2 * (fps // 30)
            total_minutes = h * 60 + m
            total -= drop_per_min * (total_minutes - total_minutes // 10)
        return total

    @classmethod
    def from_frames(cls, frames, fps_num, fps_den, drop):
        tc = cls.__new__(cls)
        tc.fps_num, tc.fps_den, tc.drop = fps_num, fps_den, drop
        tc.nominal = round(fps_num / fps_den)
        tc.total = int(frames)
        return tc

    def add_seconds(self, seconds: float) -> "Timecode":
        added = round(seconds * self.fps_num / self.fps_den)
        return Timecode.from_frames(self.total + added, self.fps_num, self.fps_den, self.drop)

    def __str__(self):
        fps = self.nominal
        frames = self.total
        if self.drop:
            drop_per_min = 2 * (fps // 30)
            frames_per_10min = fps * 600 - drop_per_min * 9
            d, m = divmod(frames, frames_per_10min)
            frames_per_min = fps * 60 - drop_per_min
            if m >= drop_per_min:
                add = drop_per_min * (9 * d + (m - drop_per_min) // frames_per_min)
            else:
                add = drop_per_min * 9 * d
            frames += add
        sep = ";" if self.drop else ":"
        f = frames % fps
        s = (frames // fps) % 60
        mi = (frames // (fps * 60)) % 60
        h = (frames // (fps * 3600)) % 24
        return f"{h:02d}:{mi:02d}:{s:02d}{sep}{f:02d}"


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class ShotMark:
    label: str
    friendly: str
    is_user_mark: bool
    capture_frame: int
    elapsed_seconds: float
    source_timecode: str
    status: str = ""


@dataclass
class ClipMarks:
    source_file: str
    device: str = ""
    model: str = ""
    lens: str = ""
    capture_fps: str = ""
    tc_fps: int = 0
    drop_frame: bool = False
    start_timecode: str = ""
    duration_frames: int = 0
    creation_date: str = ""
    marks: list = field(default_factory=list)
    user_mark_count: int = 0


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def read_nrt_xml(path: str) -> str:
    """Return the NonRealTimeMeta XML text from an .xml sidecar or an .mp4/.mxf."""
    raw = open(path, "rb").read()
    start = raw.find(b"<NonRealTimeMeta")
    if start == -1:
        # maybe a sidecar that begins straight with <?xml
        start = raw.find(b"<?xml")
    end = raw.find(b"</NonRealTimeMeta>")
    if start == -1 or end == -1:
        raise SystemExit(f"No NonRealTimeMeta document found in {path}")
    # back up to the <?xml declaration if present just before the root
    decl = raw.rfind(b"<?xml", 0, start + 1)
    if decl != -1 and start - decl < 80:
        start = decl
    return raw[start:end + len(b"</NonRealTimeMeta>")].decode("utf-8", "replace")


def decode_klv_label(length_value_hex: str) -> str:
    b = bytes.fromhex(length_value_hex)
    n = b[0]
    return b[1:1 + n].decode("ascii", "replace")


def parse_clip(path: str) -> ClipMarks:
    xml = read_nrt_xml(path)
    # namespace-agnostic regex parsing keeps us robust to ver.2.x schema drift
    def attr(tag, a):
        m = re.search(rf'<{tag}[^>]*\b{a}="([^"]*)"', xml)
        return m.group(1) if m else ""

    capture_fps = attr("VideoFrame", "captureFps") or attr("VideoFrame", "formatFps")
    fps_num, fps_den = fps_to_rational(capture_fps or "30")

    tc_fps = int(attr("LtcChangeTable", "tcFps") or "30")
    half_step = attr("LtcChangeTable", "halfStep") == "true"

    # start timecode = first LtcChange entry
    m0 = re.search(r'<LtcChange\b[^>]*frameCount="0"[^>]*value="([0-9A-Fa-f]{8})"', xml) \
        or re.search(r'<LtcChange\b[^>]*value="([0-9A-Fa-f]{8})"', xml)
    if m0:
        ltc = decode_ltc(m0.group(1))
    else:
        ltc = {"h": 0, "m": 0, "s": 0, "f": 0, "drop": False}

    tc_num, tc_den = (tc_fps * 1000, 1001) if abs((fps_num / fps_den) - round(fps_num / fps_den)) > 0.01 else (tc_fps, 1)
    start_tc = Timecode(ltc["h"], ltc["m"], ltc["s"], ltc["f"], tc_num, tc_den, ltc["drop"])

    clip = ClipMarks(
        source_file=os.path.basename(path),
        device=attr("Device", "manufacturer"),
        model=attr("Device", "modelName"),
        lens=attr("Lens", "modelName"),
        capture_fps=capture_fps,
        tc_fps=tc_fps,
        drop_frame=ltc["drop"],
        start_timecode=str(start_tc),
        duration_frames=int(attr("Duration", "value") or "0"),
        creation_date=attr("CreationDate", "value"),
    )

    cap_fps_exact = fps_num / fps_den
    for km in re.finditer(
        r'<KlvPacket\b[^>]*key="([0-9A-Fa-f]{32})"[^>]*frameCount="(\d+)"[^>]*lengthValue="([0-9A-Fa-f]+)"(?:[^>]*status="([^"]*)")?',
        xml,
    ):
        key, fc, lv, status = km.group(1), int(km.group(2)), km.group(3), km.group(4) or ""
        if key.upper() != ESSENCE_MARK_KEY:
            continue
        label = decode_klv_label(lv)
        is_user = label.startswith("_ShotMark")
        elapsed = fc / cap_fps_exact
        clip.marks.append(ShotMark(
            label=label,
            friendly=KNOWN_LABELS.get(label, label),
            is_user_mark=is_user,
            capture_frame=fc,
            elapsed_seconds=round(elapsed, 4),
            source_timecode=str(start_tc.add_seconds(elapsed)),
            status=status,
        ))
    clip.user_mark_count = sum(1 for m in clip.marks if m.is_user_mark)
    return clip


# --------------------------------------------------------------------------- #
# Output writers
# --------------------------------------------------------------------------- #
def write_premiere_markers_csv(clip: ClipMarks, out: str):
    """CSV matching Premiere Pro's Marker panel import columns."""
    import csv
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Marker Name", "Description", "In", "Out", "Duration", "Marker Type"])
        for i, m in enumerate([x for x in clip.marks if x.is_user_mark] or clip.marks, 1):
            w.writerow([f"{m.friendly}", m.label, m.source_timecode, m.source_timecode, "00:00:00:00", "Comment"])


def fps_to_xmp_framerate(fps_str: str) -> str:
    """'119.88p' -> 'f120000s1001'; '25' -> 'f25'. Matches Adobe FrameRate notation."""
    num, den = fps_to_rational(fps_str or "30")
    return f"f{num}s{den}" if den != 1 else f"f{num}"


def _xmp_guid(seed: str) -> str:
    """Deterministic UUID-shaped id so re-runs are stable and Premiere can de-dupe."""
    import hashlib
    h = hashlib.md5(seed.encode()).hexdigest()
    return f"{h[0:8]}-{h[8:12]}-4{h[13:16]}-8{h[17:20]}-{h[20:32]}"


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def build_xmp(clip: ClipMarks, include_auto: bool = False) -> str:
    """
    Build an Adobe Dynamic Media (xmpDM) marker document Premiere reads as clip
    markers. The Sony capture `frameCount` IS the xmpDM:startTime when the track
    frameRate equals the capture fps -> frame-exact, zero rounding.
    """
    rate = fps_to_xmp_framerate(clip.capture_fps)
    marks = clip.marks if include_auto else [m for m in clip.marks if m.is_user_mark]
    if not marks:
        marks = clip.marks  # never write an empty doc; fall back to all marks
    li = []
    for m in marks:
        comment = f"{m.label}  |  src TC {m.source_timecode}  |  {m.elapsed_seconds:.3f}s"
        guid = _xmp_guid(f"{clip.source_file}:{m.label}:{m.capture_frame}")
        li.append(
            f'              <rdf:li xmpDM:startTime="{m.capture_frame}" '
            f'xmpDM:duration="0" xmpDM:name="{_xml_escape(m.friendly)}" '
            f'xmpDM:comment="{_xml_escape(comment)}" xmpDM:guid="{guid}"/>'
        )
    body = "\n".join(li)
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="SonyShotMarker">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about="" xmlns:xmpDM="http://ns.adobe.com/xmp/1.0/DynamicMedia/">
      <xmpDM:Tracks>
        <rdf:Bag>
          <rdf:li>
            <rdf:Description xmpDM:trackName="Shot Marks" xmpDM:trackType="Comment" xmpDM:frameRate="{rate}">
              <xmpDM:markers>
                <rdf:Seq>
{body}
                </rdf:Seq>
              </xmpDM:markers>
            </rdf:Description>
          </rdf:li>
        </rdf:Bag>
      </xmpDM:Tracks>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
'''


def write_xmp_sidecar(clip: ClipMarks, out: str, include_auto: bool = False):
    """Write the xmpDM doc as a sidecar (default name is the appended clip.MP4.xmp)."""
    open(out, "w").write(build_xmp(clip, include_auto))


ADOBE_XMP_UUID = bytes.fromhex("BE7ACFCB97A942E89C71999491E3AFAC")


def _read_box_size(raw: bytes, offset: int) -> int:
    size = int.from_bytes(raw[offset:offset + 4], "big")
    if size == 1:
        if offset + 16 > len(raw):
            return 0
        return int.from_bytes(raw[offset + 8:offset + 16], "big")
    if size == 0:
        return len(raw) - offset
    return size


def _top_level_boxes(raw: bytes):
    pos = 0
    n = len(raw)
    while pos + 8 <= n:
        size = _read_box_size(raw, pos)
        kind = raw[pos + 4:pos + 8]
        if size < 8 or pos + size > n:
            break
        yield kind, pos, size
        pos += size


def _xmp_uuid_box(packet: bytes) -> bytes:
    payload = ADOBE_XMP_UUID + packet
    size = len(payload) + 8
    if size > 0xFFFFFFFF:
        return (1).to_bytes(4, "big") + b"uuid" + size.to_bytes(8, "big") + payload
    return size.to_bytes(4, "big") + b"uuid" + payload


def embed_xmp_into_mp4(clip: ClipMarks, src: str, out: str, include_auto: bool = False):
    """
    Embed xmpDM markers INTO a copy of an MP4/MOV/M4V without external tools.

    The Mac app's validated strategy is intentionally mirrored here for Windows:
    copy the source, find reusable top-level `free`/`skip` space before `mdat`, write
    a standard Adobe XMP `uuid` box into that space, then leave the media payload and
    chunk offsets untouched. Originals are never modified and there is no exiftool
    dependency for Windows users.
    """
    import shutil

    if os.path.abspath(src) == os.path.abspath(out):
        raise SystemExit("refusing to embed into the original; choose a different output path")
    raw = open(src, "rb").read()
    boxes = list(_top_level_boxes(raw))
    mdat_offsets = [offset for kind, offset, _size in boxes if kind == b"mdat"]
    if not mdat_offsets:
        raise SystemExit("no mdat box found; not a writable MP4/MOV")
    mdat_offset = min(mdat_offsets)

    core = build_xmp(clip, include_auto).split("?>", 1)[1].strip()
    packet = ('<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
              + core + '\n<?xpacket end="w"?>').encode("utf-8")
    uuid_box = _xmp_uuid_box(packet)
    needed = len(uuid_box) + 8  # leave a valid trailing free box header

    reusable = None
    neutralize_offsets = []
    for kind, offset, size in boxes:
        if offset >= mdat_offset:
            continue
        if kind in (b"free", b"skip") and size >= needed and reusable is None:
            reusable = (offset, size)
        if kind == b"uuid" and size >= 24 and raw[offset + 8:offset + 24] == ADOBE_XMP_UUID:
            neutralize_offsets.append(offset)

    if reusable is None:
        raise SystemExit(f"no reusable free space before mdat (need {needed} bytes)")

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    shutil.copy2(src, out)
    free_offset, free_size = reusable
    trailing_free_size = free_size - len(uuid_box)
    try:
        with open(out, "r+b") as fh:
            # Neutralize previous Adobe XMP boxes so Premiere sees this run's markers first.
            for offset in neutralize_offsets:
                fh.seek(offset + 4)
                fh.write(b"free")
            fh.seek(free_offset)
            fh.write(uuid_box)
            fh.write(trailing_free_size.to_bytes(4, "big") + b"free")
    except Exception:
        try:
            os.unlink(out)
        except OSError:
            pass
        raise

    n = packet.count(b"xmpDM:startTime")
    print(f"  -> EMBED {out}  ({n} clip markers embedded; Sony marks preserved)")


def write_fcpxml(clip: ClipMarks, out: str):
    """FCP7-style xmeml that Premiere imports, with clip markers (seconds-based)."""
    marks = [m for m in clip.marks if m.is_user_mark] or clip.marks
    rate = round(fps_to_rational(clip.capture_fps or "30")[0] / fps_to_rational(clip.capture_fps or "30")[1])
    rows = "\n".join(
        f'''        <marker>
          <comment>{m.label}</comment>
          <name>{m.friendly}</name>
          <in>{m.capture_frame}</in>
          <out>-1</out>
        </marker>''' for m in marks)
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="4">
  <clip>
    <name>{clip.source_file}</name>
    <rate><timebase>{rate}</timebase><ntsc>TRUE</ntsc></rate>
{rows}
  </clip>
</xmeml>'''
    open(out, "w").write(xml)


def print_report(clip: ClipMarks):
    print(f"\n  Source : {clip.source_file}")
    print(f"  Camera : {clip.device} {clip.model}   Lens: {clip.lens}")
    print(f"  Format : {clip.capture_fps}  |  TC base {clip.tc_fps}{'DF' if clip.drop_frame else 'NDF'}"
          f"  |  start TC {clip.start_timecode}  |  {clip.duration_frames} frames")
    print(f"  Marks  : {len(clip.marks)} total, {clip.user_mark_count} user Shot Mark(s)\n")
    if not clip.marks:
        print("  (no essence marks found)\n"); return
    print(f"  {'#':>2}  {'LABEL':<11} {'KIND':<18} {'CAP-FRAME':>9} {'ELAPSED':>9}  SOURCE TC")
    print(f"  {'-'*2}  {'-'*11} {'-'*18} {'-'*9} {'-'*9}  {'-'*12}")
    for i, m in enumerate(clip.marks, 1):
        kind = "USER SHOT MARK" if m.is_user_mark else m.friendly
        print(f"  {i:>2}  {m.label:<11} {kind:<18} {m.capture_frame:>9} {m.elapsed_seconds:>8.3f}s  {m.source_timecode}")
    print()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Extract Sony Shot Marks -> timecode")
    ap.add_argument("input", help="Sony .mp4 / .mxf clip or Cxxxx M01.XML sidecar")
    ap.add_argument("--json", help="write structured JSON")
    ap.add_argument("--premiere-markers", help="write Premiere Marker-panel CSV")
    ap.add_argument("--fcpxml", help="write FCP7 xmeml with clip markers")
    ap.add_argument("--xmp", nargs="?", const="AUTO",
                    help="write Premiere-readable XMP sidecar. With no value, writes "
                         "<clip>.xmp next to the input (appended form).")
    ap.add_argument("--embed", nargs="?", const="AUTO",
                    help="embed markers INTO a copy of the MP4 (never the original). "
                         "With no value, writes <clip>_embedded.<ext>.")
    ap.add_argument("--include-auto", action="store_true",
                    help="include automatic _RecStart/_RecEnd marks in outputs")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    clip = parse_clip(args.input)
    if not args.quiet:
        print_report(clip)
    if args.json:
        json.dump(asdict(clip), open(args.json, "w"), indent=2)
        print(f"  -> JSON  {args.json}")
    if args.premiere_markers:
        write_premiere_markers_csv(clip, args.premiere_markers)
        print(f"  -> CSV   {args.premiere_markers}")
    if args.fcpxml:
        write_fcpxml(clip, args.fcpxml)
        print(f"  -> XML   {args.fcpxml}")
    if args.xmp:
        out = args.xmp
        if out == "AUTO":
            out = args.input + ".xmp" if args.input.lower().endswith((".mp4", ".mxf", ".mov")) \
                else os.path.splitext(args.input)[0] + ".xmp"
        write_xmp_sidecar(clip, out, include_auto=args.include_auto)
        print(f"  -> XMP   {out}")
    if args.embed:
        if not args.input.lower().endswith((".mp4", ".mov", ".m4v")):
            raise SystemExit("--embed needs an .mp4/.mov input (not an .xml sidecar)")
        out = args.embed
        if out == "AUTO":
            base, ext = os.path.splitext(args.input)
            out = f"{base}_embedded{ext}"
        embed_xmp_into_mp4(clip, args.input, out, include_auto=args.include_auto)
    return clip


if __name__ == "__main__":
    main()
