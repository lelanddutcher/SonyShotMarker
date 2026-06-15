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

spec_rl = importlib.util.spec_from_file_location("run_log", TOOLS / "run_log.py")
assert spec_rl is not None and spec_rl.loader is not None
run_log = importlib.util.module_from_spec(spec_rl)
sys.modules[spec_rl.name] = run_log
spec_rl.loader.exec_module(run_log)

spec_eb = importlib.util.spec_from_file_location("embed_batch", TOOLS / "embed_batch.py")
assert spec_eb is not None and spec_eb.loader is not None
embed_batch = importlib.util.module_from_spec(spec_eb)
sys.modules[spec_eb.name] = embed_batch
spec_eb.loader.exec_module(embed_batch)

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


def test_embed_atomic_cleans_partial_when_finalize_fails(tmp_path, monkeypatch):
    src = tmp_path / "C0004.MP4"
    out = tmp_path / "out" / "C0004.MP4"
    synthetic_clip(src)
    before = src.read_bytes()
    clip = sony_shotmark.parse_clip(str(src))

    def _boom(*a, **k):
        raise OSError("simulated finalize failure")
    monkeypatch.setattr(sony_shotmark.os, "replace", _boom)

    try:
        sony_shotmark.embed_xmp_into_mp4(clip, str(src), str(out))
    except OSError:
        pass
    else:
        raise AssertionError("expected OSError")

    assert src.read_bytes() == before, "original must never be touched"
    assert not out.exists(), "no half-file may be left at the final path"
    assert not (out.parent / (out.name + ".partial")).exists(), "the .partial must be cleaned up"


def test_enough_output_space_blocks_when_short(tmp_path, monkeypatch):
    import shutil, collections
    clip = tmp_path / "big.MP4"
    clip.write_bytes(b"x" * 5000)
    Usage = collections.namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(10 ** 9, 0, 1000))

    ok, required, free = sony_shotmark.enough_output_space([str(clip)], str(tmp_path))
    assert ok is False
    assert required >= 5000
    assert free == 1000


def test_enough_output_space_ok_when_room(tmp_path, monkeypatch):
    import shutil, collections
    clip = tmp_path / "big.MP4"
    clip.write_bytes(b"x" * 5000)
    Usage = collections.namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(10 ** 9, 0, 10 ** 9))

    ok, required, free = sony_shotmark.enough_output_space([str(clip)], str(tmp_path))
    assert ok is True


def test_verify_embedded_passes_on_real_embed(tmp_path):
    src = tmp_path / "C0005.MP4"
    out = tmp_path / "out" / "C0005.MP4"
    synthetic_clip(src)
    clip = sony_shotmark.parse_clip(str(src))
    sony_shotmark.embed_xmp_into_mp4(clip, str(src), str(out))

    ok, detail = sony_shotmark.verify_embedded(str(out), expected_marks=2)
    assert ok is True
    assert "2 marker(s) verified" in detail


def test_verify_embedded_fails_on_wrong_count(tmp_path):
    src = tmp_path / "C0006.MP4"
    out = tmp_path / "out" / "C0006.MP4"
    synthetic_clip(src)
    clip = sony_shotmark.parse_clip(str(src))
    sony_shotmark.embed_xmp_into_mp4(clip, str(src), str(out))

    ok, detail = sony_shotmark.verify_embedded(str(out), expected_marks=5)
    assert ok is False
    assert "expected 5" in detail


def test_verify_embedded_fails_when_no_adobe_xmp(tmp_path):
    # A raw (un-embedded) Sony clip has the Sony uuid but no Adobe XMP box.
    src = tmp_path / "C0007.MP4"
    synthetic_clip(src)
    ok, detail = sony_shotmark.verify_embedded(str(src))
    assert ok is False
    assert "no Adobe XMP marker box" in detail


def test_preflight_clean_when_ok(tmp_path):
    src = tmp_path / "C0008.MP4"
    synthetic_clip(src)
    dest = tmp_path / "out"
    dest.mkdir()
    assert sony_shotmark.preflight([str(src)], str(dest)) == []


def test_preflight_flags_missing_source(tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    problems = sony_shotmark.preflight([str(tmp_path / "nope.MP4")], str(dest))
    assert any("missing source" in p for p in problems)


def test_preflight_flags_insufficient_space(tmp_path, monkeypatch):
    import shutil, collections
    src = tmp_path / "C0009.MP4"
    src.write_bytes(b"x" * 5000)
    dest = tmp_path / "out"
    dest.mkdir()
    Usage = collections.namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(10 ** 9, 0, 10))
    problems = sony_shotmark.preflight([str(src)], str(dest))
    assert any("not enough space" in p for p in problems)


def test_existing_outputs_detects_clobber(tmp_path):
    src = tmp_path / "C0010.MP4"
    src.write_bytes(b"x" * 10)
    dest = tmp_path / "out"
    dest.mkdir()
    assert sony_shotmark.existing_outputs([str(src)], str(dest)) == []
    (dest / "C0010.MP4").write_bytes(b"old")
    assert sony_shotmark.existing_outputs([str(src)], str(dest)) == ["C0010.MP4"]


def test_process_one_embeds_and_verifies(tmp_path):
    src = tmp_path / "C0011.MP4"
    synthetic_clip(src)
    dest = tmp_path / "out"
    dest.mkdir()
    rec, msg = embed_batch.process_one(str(src), str(dest))
    assert rec["status"] == "embedded"
    assert rec["verified"] is True
    assert "verified" in msg
    assert (dest / "C0011.MP4").exists()


def test_process_one_verify_failure_removes_output(tmp_path, monkeypatch):
    src = tmp_path / "C0012.MP4"
    synthetic_clip(src)
    dest = tmp_path / "out"
    dest.mkdir()
    monkeypatch.setattr(embed_batch.S, "verify_embedded", lambda p, expected_marks=None: (False, "simulated"))
    rec, msg = embed_batch.process_one(str(src), str(dest))
    assert rec["status"] == "verify-failed"
    assert rec["state"] == "err"
    assert not (dest / "C0012.MP4").exists(), "a copy that fails verify must be deleted"
    assert "failed verify" in msg


def test_summarize_counts():
    results = [
        {"status": "embedded", "state": "ok"},
        {"status": "embedded", "state": "ok"},
        {"status": "no-marks", "state": "skip"},
        {"status": "embed-failed", "state": "err"},
    ]
    assert embed_batch.summarize(results) == "✓2 · –1 · ✗1"


NRT_BYTES = (
    b'<NonRealTimeMeta xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20">'
    b'<Device manufacturer="Sony" modelName="ILCE-7SM3"/>'
    b'<VideoFrame captureFps="119.88" formatFps="119.88"/>'
    b'<LtcChangeTable tcFps="30" halfStep="true"><LtcChange frameCount="0" value="00000001"/></LtcChangeTable>'
    b'<Duration value="1200"/><KlvPacketTable>'
    b'<KlvPacket key="060E2B34010101050301020A02000000" frameCount="120" lengthValue="0A5F53686F744D61726B31" status="spot"/>'
    b'<KlvPacket key="060E2B34010101050301020A02000000" frameCount="240" lengthValue="0A5F53686F744D61726B32" status="spot"/>'
    b'</KlvPacketTable></NonRealTimeMeta>'
)


def test_parse_when_nrt_after_mdat(tmp_path):
    # Real Sony layout puts the metadata box AFTER mdat; the seek-based reader must find it
    # without loading the (here, oversized) mdat payload.
    src = tmp_path / "C0099.MP4"
    src.write_bytes(b"".join([
        box(b"ftyp", b"isom" + b"\0" * 12),
        box(b"mdat", b"media payload bytes " * 1000),
        box(b"uuid", b"\x01" * 16 + NRT_BYTES),
    ]))
    clip = sony_shotmark.parse_clip(str(src))
    assert clip.user_mark_count == 2
    assert [m.capture_frame for m in clip.marks if m.is_user_mark] == [120, 240]


def test_copy_with_progress_reports_and_completes(tmp_path):
    src = tmp_path / "in.bin"
    dst = tmp_path / "out.bin"
    src.write_bytes(b"abc" * 5000)
    seen = []
    ok = sony_shotmark.copy_with_progress(str(src), str(dst), on_bytes=lambda c, t: seen.append((c, t)), chunk=4096)
    assert ok is True
    assert dst.read_bytes() == src.read_bytes()
    assert seen[0][0] == 0 and seen[-1][0] == seen[-1][1] == src.stat().st_size
    assert [c for c, _ in seen] == sorted(c for c, _ in seen)  # monotonic


def test_copy_with_progress_cancels(tmp_path):
    src = tmp_path / "in.bin"
    dst = tmp_path / "out.bin"
    src.write_bytes(b"x" * 100000)
    ok = sony_shotmark.copy_with_progress(str(src), str(dst), cancel=lambda: True)
    assert ok is False  # aborted before copying anything


def test_embed_reports_byte_progress(tmp_path):
    src = tmp_path / "C0020.MP4"
    out = tmp_path / "out" / "C0020.MP4"
    synthetic_clip(src)
    clip = sony_shotmark.parse_clip(str(src))
    seen = []
    sony_shotmark.embed_xmp_into_mp4(clip, str(src), str(out), on_bytes=lambda c, t: seen.append((c, t)))
    assert out.exists()
    assert seen[-1][0] == seen[-1][1] == src.stat().st_size


def test_embed_cancel_midcopy_leaves_no_output(tmp_path):
    src = tmp_path / "C0021.MP4"
    out = tmp_path / "out" / "C0021.MP4"
    synthetic_clip(src)
    before = src.read_bytes()
    clip = sony_shotmark.parse_clip(str(src))
    try:
        sony_shotmark.embed_xmp_into_mp4(clip, str(src), str(out), cancel=lambda: True)
    except sony_shotmark.EmbedCancelled:
        pass
    else:
        raise AssertionError("expected EmbedCancelled")
    assert src.read_bytes() == before
    assert not out.exists()
    assert not (out.parent / (out.name + ".partial")).exists()


def test_process_one_cancel_returns_cancelled(tmp_path):
    src = tmp_path / "C0022.MP4"
    synthetic_clip(src)
    dest = tmp_path / "out"
    dest.mkdir()
    rec, msg = embed_batch.process_one(str(src), str(dest), cancel=lambda: True)
    assert rec["status"] == "cancelled"
    assert "cancelled" in msg
    assert not (dest / "C0022.MP4").exists()


def test_run_log_writes_records_and_finds_latest(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOTMARK_LOG_DIR", str(tmp_path / "logs"))
    rl = run_log.RunLog(app_version="9.9.9")
    rl.header("/out/footage embedded markers", dest_volume="/Volumes/X", dest_free=1234567)
    rl.inputs([])
    rl.result("✓ A.MP4 — 2 mark(s)")
    rl.summary("1/1 embedded")
    path = rl.write()

    text = Path(path).read_text(encoding="utf-8")
    assert "app: 9.9.9" in text
    assert "1/1 embedded" in text
    assert "✓ A.MP4" in text
    assert "free: 1.2 MB" in text
    assert run_log.latest_log() == path
