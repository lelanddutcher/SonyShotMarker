"""Tests for the DaVinci Resolve drop-in script's metadata reader.

The Resolve script has to stay one self-contained stdlib-only file, so it carries its own
copy of the parser rather than importing the toolkit. These tests pin it to the same
answers the main engine gives, and guard the property that actually matters on set: it
must never pull a media file into memory to find 2.5 KB of XML.
"""
import io
import os
import sys
import importlib.util

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPT = os.path.join(REPO, "plugin", "resolve", "SonyShotMarker.py")

spec = importlib.util.spec_from_file_location("sony_shot_marker_resolve", SCRIPT)
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)

LV_RECSTART = "095F5265635374617274"
LV_SHOTMARK1 = "0A5F53686F744D61726B31"
LV_SHOTMARK2 = "0A5F53686F744D61726B32"


def nrt(marks, fps="29.97p", ltc="64564420", tcfps=30):
    packets = "\n".join(
        '<KlvPacket key="060E2B34010101050301020A02000000" frameCount="%d" '
        'lengthValue="%s" status="spot"/>' % (f, lv) for f, lv in marks
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<NonRealTimeMeta xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20">'
        '<Duration value="525"/>'
        '<LtcChangeTable tcFps="%d" halfStep="false">'
        '<LtcChange frameCount="0" value="%s" status="increment"/>'
        '</LtcChangeTable>'
        '<KlvPacketTable>%s</KlvPacketTable>'
        '<VideoFormat><VideoFrame captureFps="%s" formatFps="%s"/></VideoFormat>'
        '<Device manufacturer="Sony" modelName="ILCE-7SM3"/>'
        '</NonRealTimeMeta>' % (tcfps, ltc, packets, fps, fps)
    )


def _box(kind, payload):
    return (len(payload) + 8).to_bytes(4, "big") + kind + payload


def sony_mp4(xml, mdat_bytes=4 * 1024 * 1024):
    """An A7S III-shaped file: metadata in a `meta` box AFTER a large `mdat`."""
    return b"".join([
        _box(b"ftyp", b"mp42\x00\x00\x00\x00mp42iso6"),
        _box(b"mdat", b"\x77" * mdat_bytes),
        _box(b"moov", b"\x00" * 256),
        _box(b"meta", b"\x00" * 32 + xml.encode("utf-8") + b"\x00" * 8),
    ])


def plain_mp4(mdat_bytes=64 * 1024):
    return b"".join([
        _box(b"ftyp", b"mp42\x00\x00\x00\x00mp42iso6"),
        _box(b"mdat", b"\x11" * mdat_bytes),
    ])


def test_reads_marks_from_a_trailing_meta_box(tmp_path):
    p = tmp_path / "C0001.MP4"
    p.write_bytes(sony_mp4(nrt([(0, LV_RECSTART), (93, LV_SHOTMARK1), (213, LV_SHOTMARK1)])))
    marks = R.read_shot_marks(str(p))
    # _RecStart is an automatic mark; Resolve only gets the user's button presses.
    assert [m["frame"] for m in marks] == [93, 213]
    assert [m["name"] for m in marks] == ["Shot Mark 1", "Shot Mark 1"]


def test_shot_mark_1_and_2_stay_distinguishable(tmp_path):
    p = tmp_path / "C0002.MP4"
    p.write_bytes(sony_mp4(nrt([(10, LV_SHOTMARK1), (20, LV_SHOTMARK2)])))
    marks = R.read_shot_marks(str(p))
    assert [m["label"] for m in marks] == ["_ShotMark1", "_ShotMark2"]
    assert [m["name"] for m in marks] == ["Shot Mark 1", "Shot Mark 2"]


def test_the_panel_offers_resolve_colours_and_defaults_to_green():
    # Resolve's own first colour is Blue; green is chosen deliberately so the marks read
    # as "these are the keepers" rather than as one more blue thing on the timeline.
    assert R.DEFAULT_COLOR == "Green"
    assert R.DEFAULT_COLOR in R.RESOLVE_COLORS
    for expected in ("Blue", "Cyan", "Yellow", "Red"):
        assert expected in R.RESOLVE_COLORS


def test_timecodes_match_the_reference_engine(tmp_path):
    """The exact values tools/sony_shotmark.py prints for the validated SIMON7034 clip."""
    p = tmp_path / "SIMON7034.MP4"
    p.write_bytes(sony_mp4(nrt([(0, LV_RECSTART), (93, LV_SHOTMARK1), (213, LV_SHOTMARK1),
                                (329, LV_SHOTMARK1), (416, LV_SHOTMARK1)])))
    marks = R.read_shot_marks(str(p))
    assert [m["frame"] for m in marks] == [93, 213, 329, 416]
    assert [m["tc"] for m in marks] == [
        "20:44:59;27", "20:45:03;29", "20:45:07;25", "20:45:10;22"
    ]


def test_falls_back_to_the_sidecar_when_the_file_has_no_metadata(tmp_path):
    p = tmp_path / "C0003.MP4"
    p.write_bytes(plain_mp4())
    (tmp_path / "C0003M01.XML").write_text(nrt([(50, LV_SHOTMARK1)]))
    assert [m["frame"] for m in R.read_shot_marks(str(p))] == [50]


def test_lowercase_sidecar_is_found(tmp_path):
    p = tmp_path / "C0004.MP4"
    p.write_bytes(plain_mp4())
    (tmp_path / "C0004M01.xml").write_text(nrt([(7, LV_SHOTMARK1)]))
    assert [m["frame"] for m in R.read_shot_marks(str(p))] == [7]


def test_mxf_uses_the_sidecar_without_scanning_the_container(tmp_path):
    p = tmp_path / "C0005.MXF"
    p.write_bytes(b"\x06\x0e\x2b\x34" * 40000)          # not a box container
    (tmp_path / "C0005M01.XML").write_text(nrt([(12, LV_SHOTMARK1)], fps="25p", tcfps=25))
    assert [m["frame"] for m in R.read_shot_marks(str(p))] == [12]


def test_non_sony_media_returns_nothing_rather_than_raising(tmp_path):
    p = tmp_path / "GOPRO.MP4"
    p.write_bytes(plain_mp4())
    assert R.read_shot_marks(str(p)) == []


def test_missing_file_returns_nothing(tmp_path):
    assert R.read_shot_marks(str(tmp_path / "nope.MP4")) == []


def test_truncated_file_does_not_raise(tmp_path):
    p = tmp_path / "cut.MP4"
    p.write_bytes(sony_mp4(nrt([(1, LV_SHOTMARK1)]))[:40])
    assert R.read_shot_marks(str(p)) == []


def test_never_reads_the_media_payload(tmp_path, monkeypatch):
    """The guarantee that makes this safe on a 100 GB offload: the `mdat` payload is
    seeked past, never read. Recorded at the file-object level."""
    p = tmp_path / "BIG.MP4"
    mdat = 8 * 1024 * 1024
    p.write_bytes(sony_mp4(nrt([(42, LV_SHOTMARK1)]), mdat_bytes=mdat))

    reads = []
    real_open = io.open

    class Recorder:
        def __init__(self, fh):
            self._fh = fh

        def read(self, n=-1):
            reads.append((self._fh.tell(), n))
            return self._fh.read(n)

        def __getattr__(self, name):
            return getattr(self._fh, name)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return self._fh.__exit__(*a)

    def fake_open(path, mode="r", *args, **kwargs):
        fh = real_open(path, mode, *args, **kwargs)
        return Recorder(fh) if "b" in mode else fh

    monkeypatch.setattr("builtins.open", fake_open)
    marks = R.read_shot_marks(str(p))
    assert [m["frame"] for m in marks] == [42]

    mdat_payload_start = 24 + 8                      # ftyp box, then the mdat header
    mdat_end = mdat_payload_start + mdat
    total = sum(n for _, n in reads if n and n > 0)
    for pos, n in reads:
        if not n or n < 0:
            continue
        assert not (pos < mdat_end and pos + n > mdat_payload_start), \
            "read at %d+%d reached into the media payload" % (pos, n)
    assert total < 1024 * 1024, "read %d bytes to find a few KB of metadata" % total


def _find_real_clip():
    """The validated A7S III clip, if this machine has it. Set SHOTMARK_SAMPLE to point
    at it directly; otherwise look in the repo and up out of a git worktree."""
    env = os.environ.get("SHOTMARK_SAMPLE")
    if env and os.path.exists(env):
        return env
    for base in (REPO, os.path.join(REPO, "..", "..", ".."), os.path.dirname(REPO)):
        p = os.path.abspath(os.path.join(base, "SIMON7034.MP4"))
        if os.path.exists(p):
            return p
    return None


@pytest.mark.skipif(_find_real_clip() is None,
                    reason="the validated Sony clip is not on this machine")
def test_real_clip_matches_the_verified_marks():
    marks = R.read_shot_marks(_find_real_clip())
    assert [m["frame"] for m in marks] == [93, 213, 329, 416]
    assert [m["tc"] for m in marks] == [
        "20:44:59;27", "20:45:03;29", "20:45:07;25", "20:45:10;22"
    ]
