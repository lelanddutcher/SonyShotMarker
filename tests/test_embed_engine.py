import importlib.util
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
spec = importlib.util.spec_from_file_location("sony_shotmark", TOOLS / "sony_shotmark.py")
assert spec is not None and spec.loader is not None
sony_shotmark = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sony_shotmark
spec.loader.exec_module(sony_shotmark)

ADOBE_UUID = bytes.fromhex("BE7ACFCB97A942E89C71999491E3AFAC")


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def synthetic_clip(path: Path, *, free_size: int = 4096, existing_xmp: bool = False) -> None:
    nrt = b"""<NonRealTimeMeta xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20">
<Device manufacturer="Sony" modelName="ILCE-7SM3"/>
<VideoFrame captureFps="119.88" formatFps="119.88"/>
<LtcChangeTable tcFps="30" halfStep="true"><LtcChange frameCount="0" value="00000001"/></LtcChangeTable>
<Duration value="1200"/>
<KlvPacketTable>
<KlvPacket key="060E2B34010101050301020A02000000" frameCount="120" lengthValue="0A5F53686F744D61726B31" status="spot"/>
<KlvPacket key="060E2B34010101050301020A02000000" frameCount="240" lengthValue="0A5F53686F744D61726B32" status="spot"/>
</KlvPacketTable>
</NonRealTimeMeta>"""
    payload = [box(b"ftyp", b"isom" + b"\0" * 12)]
    if existing_xmp:
        payload.append(box(b"uuid", ADOBE_UUID + b"old xmpDM:startTime=\"666\""))
    payload.append(struct.pack(">I4s", free_size, b"free") + (b"\0" * (free_size - 8)))
    payload.append(box(b"uuid", b"\x01" * 16 + nrt))
    payload.append(box(b"mdat", b"not real media, only box-layout test bytes"))
    path.write_bytes(b"".join(payload))


def top_boxes(data: bytes):
    pos = 0
    out = []
    while pos + 8 <= len(data):
        size = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        if size == 1:
            size = int.from_bytes(data[pos + 8:pos + 16], "big")
        elif size == 0:
            size = len(data) - pos
        if size < 8 or pos + size > len(data):
            break
        out.append((kind, pos, size))
        pos += size
    return out


def test_parse_synthetic_sony_marks(tmp_path):
    src = tmp_path / "C0001.MP4"
    synthetic_clip(src)

    clip = sony_shotmark.parse_clip(str(src))

    assert clip.model == "ILCE-7SM3"
    assert clip.user_mark_count == 2
    assert [m.capture_frame for m in clip.marks if m.is_user_mark] == [120, 240]
    assert clip.marks[0].source_timecode.startswith("01:00:01")


def test_pure_python_embed_writes_copy_into_reusable_free_space(tmp_path):
    src = tmp_path / "C0001.MP4"
    out = tmp_path / "out" / "C0001.MP4"
    synthetic_clip(src)
    before = src.read_bytes()
    clip = sony_shotmark.parse_clip(str(src))

    sony_shotmark.embed_xmp_into_mp4(clip, str(src), str(out))

    assert src.read_bytes() == before, "source/original must never be modified"
    data = out.read_bytes()
    boxes = top_boxes(data)
    assert any(kind == b"uuid" and data[pos + 8:pos + 24] == ADOBE_UUID for kind, pos, size in boxes)
    assert data.count(b"xmpDM:startTime") == 2
    assert b"Shot Mark 1" in data
    assert b"Shot Mark 2" in data


def test_existing_adobe_xmp_uuid_is_neutralized_before_new_one_wins(tmp_path):
    src = tmp_path / "C0002.MP4"
    out = tmp_path / "out" / "C0002.MP4"
    synthetic_clip(src, existing_xmp=True)
    clip = sony_shotmark.parse_clip(str(src))

    sony_shotmark.embed_xmp_into_mp4(clip, str(src), str(out))

    data = out.read_bytes()
    boxes = top_boxes(data)
    adobe_uuid_boxes = [b for b in boxes if b[0] == b"uuid" and data[b[1] + 8:b[1] + 24] == ADOBE_UUID]
    assert len(adobe_uuid_boxes) == 1
    assert b"old xmpDM:startTime" in data  # payload bytes remain, but box type is neutralized
    old_offset = data.index(b"old xmpDM:startTime") - 24
    assert data[old_offset + 4:old_offset + 8] == b"free"


def test_embed_fails_cleanly_when_no_pre_mdat_free_space(tmp_path):
    src = tmp_path / "C0003.MP4"
    out = tmp_path / "out" / "C0003.MP4"
    synthetic_clip(src, free_size=16)
    clip = sony_shotmark.parse_clip(str(src))

    try:
        sony_shotmark.embed_xmp_into_mp4(clip, str(src), str(out))
    except SystemExit as exc:
        assert "no reusable free space before mdat" in str(exc)
    else:
        raise AssertionError("expected SystemExit")
    assert not out.exists()
