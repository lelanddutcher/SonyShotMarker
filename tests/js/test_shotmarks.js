#!/usr/bin/env node
/*
 * test_shotmarks.js — the plugin's parser test suite.
 *
 * Runs the exact JavaScript the Premiere panel runs, under Node, over the same Reader
 * interface. Real Sony footage is used when it is present (set SHOTMARK_SAMPLE, or drop
 * SIMON7034.MP4 in the repo root); everything else runs on synthetic fixtures that are
 * built here, so the suite is meaningful on a bare CI runner too.
 *
 *   node tests/js/test_shotmarks.js
 */
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const SM = require("../../plugin/premiere-uxp/src/lib/shotmarks.js");
const P = require("../../plugin/premiere-uxp/src/lib/paths.js");
const { createNodeFileReader, createBufferReader, instrument } = require("./node-reader.js");

let passed = 0, failed = 0, skipped = 0;
const failures = [];

function test(name, fn) {
  try {
    const r = fn();
    if (r && typeof r.then === "function") {
      return r.then(
        () => { passed++; console.log(`  ok   ${name}`); },
        (e) => { failed++; failures.push([name, e]); console.log(`  FAIL ${name}\n       ${e.message}`); }
      );
    }
    passed++; console.log(`  ok   ${name}`);
  } catch (e) {
    failed++; failures.push([name, e]);
    console.log(`  FAIL ${name}\n       ${e.message}`);
  }
  return Promise.resolve();
}

function skip(name, why) {
  skipped++;
  console.log(`  skip ${name}  (${why})`);
}

// ── fixtures ────────────────────────────────────────────────────────────────────
const REPO = path.resolve(__dirname, "..", "..");

/** Locate a real Sony clip if the machine has one. Never required. */
function findSample(name) {
  const candidates = [
    process.env.SHOTMARK_SAMPLE && path.dirname(process.env.SHOTMARK_SAMPLE),
    REPO,
    path.resolve(REPO, "..", "..", ".."),   // repo root when running from a git worktree
    path.resolve(REPO, "samples")
  ].filter(Boolean);
  for (const dir of candidates) {
    const p = path.join(dir, name);
    if (fs.existsSync(p)) return p;
  }
  return null;
}

const NRT_TEMPLATE = (opts) => `<?xml version="1.0" encoding="UTF-8"?>
<NonRealTimeMeta xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20" lastUpdate="2026-06-04T11:26:05-05:00">
\t<TargetMaterial umidRef="060A2B34010101050101"/>
\t<Duration value="${opts.duration || 525}"/>
\t<LtcChangeTable tcFps="${opts.tcFps || 30}" halfStep="${opts.halfStep || "false"}">
\t\t<LtcChange frameCount="0" value="${opts.ltc || "64564420"}" status="increment"/>
\t</LtcChangeTable>
\t<CreationDate value="2026-06-04T11:25:47-05:00"/>
\t<KlvPacketTable>
${(opts.marks || []).map(m =>
  `\t\t<KlvPacket key="060E2B34010101050301020A02000000" frameCount="${m[0]}" lengthValue="${m[1]}" status="spot"/>`
).join("\n")}
\t</KlvPacketTable>
\t<VideoFormat>
\t\t<VideoFrame videoCodec="AVC140_3840_2160_H422P@L51" captureFps="${opts.fps || "29.97p"}" formatFps="${opts.fps || "29.97p"}"/>
\t</VideoFormat>
\t<Device manufacturer="Sony" modelName="${opts.model || "ILCE-7SM3"}" serialNo="4294967295"/>
</NonRealTimeMeta>`;

const LV_RECSTART = "095F5265635374617274";
const LV_SHOTMARK1 = "0A5F53686F744D61726B31";
const LV_SHOTMARK2 = "0A5F53686F744D61726B32";

function u32(n) { return Buffer.from([(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255]); }
function box(type, payload) { return Buffer.concat([u32(payload.length + 8), Buffer.from(type, "ascii"), payload]); }

/**
 * Build a synthetic MP4 shaped exactly like an A7S III clip: the metadata sits in a
 * top-level `meta` box AFTER a large `mdat`, which is the layout that makes a
 * whole-file read unacceptable.
 */
function synthMp4(nrtXml, mdatBytes) {
  const ftyp = box("ftyp", Buffer.from("mp42\x00\x00\x00\x00mp42iso6", "ascii"));
  const free = box("free", Buffer.alloc(1024));
  const mdat = box("mdat", Buffer.alloc(mdatBytes === undefined ? 4 * 1024 * 1024 : mdatBytes, 0x77));
  const moov = box("moov", Buffer.alloc(512));
  const meta = box("meta", Buffer.concat([Buffer.alloc(64), Buffer.from(nrtXml, "utf8"), Buffer.alloc(16)]));
  return Buffer.concat([ftyp, free, mdat, moov, meta]);
}

// ── the suite ───────────────────────────────────────────────────────────────────
async function main() {
  console.log("\nSony Shot Marks — parser suite\n");

  console.log("frame rates + timecode");
  await test("fpsToRational handles Sony's NTSC and integer spellings", () => {
    assert.deepStrictEqual(SM.fpsToRational("29.97p"), [30000, 1001]);
    assert.deepStrictEqual(SM.fpsToRational("119.88"), [120000, 1001]);
    assert.deepStrictEqual(SM.fpsToRational("23.98p"), [24000, 1001]);
    assert.deepStrictEqual(SM.fpsToRational("25"), [25, 1]);
    assert.deepStrictEqual(SM.fpsToRational("50p"), [50, 1]);
    assert.deepStrictEqual(SM.fpsToRational(""), [30, 1]);
    assert.deepStrictEqual(SM.fpsToRational("59.94i"), [60000, 1001]);
  });

  await test("decodeLtc unpacks the FF SS MM HH BCD layout", () => {
    // The sample clip's real value. FF=0x64 SS=0x56 MM=0x44 HH=0x20 -> 20:44:56;24.
    // 0x64 has bit6 set, so this clip is drop-frame — matching tools/sony_shotmark.py.
    assert.deepStrictEqual(SM.decodeLtc("64564420"), { f: 24, s: 56, m: 44, h: 20, drop: true });
    // Same frame count, drop-frame bit clear.
    assert.deepStrictEqual(SM.decodeLtc("24564420"), { f: 24, s: 56, m: 44, h: 20, drop: false });
    assert.strictEqual(SM.decodeLtc("44564420").drop, true);
    assert.strictEqual(SM.decodeLtc("04564420").drop, false);
    assert.deepStrictEqual(SM.decodeLtc("00000000"), { f: 0, s: 0, m: 0, h: 0, drop: false });
  });

  await test("drop-frame timecode round-trips", () => {
    for (const [nominal, drop] of [[30, true], [30, false], [60, true], [25, false], [24, false]]) {
      for (const frames of [0, 1, 29, 30, 1799, 1800, 17982, 107892, 1000000]) {
        const tc = SM.framesToTc(frames, nominal, drop);
        const m = /^(\d\d):(\d\d):(\d\d)[:;](\d\d)$/.exec(tc);
        assert.ok(m, `unparseable timecode ${tc}`);
        const back = SM.tcToFrames(+m[1], +m[2], +m[3], +m[4], nominal, drop);
        assert.strictEqual(back, frames % (nominal * 3600 * 24), `${nominal}${drop ? "DF" : ""} @ ${frames} -> ${tc}`);
      }
    }
  });

  await test("ticksForFrame is exact at every Sony rate", () => {
    // Premiere's tick base divides evenly by all of these, so no rounding should occur.
    const cases = [
      [30000, 1001, 8475667200], [120000, 1001, 2118916800], [24000, 1001, 10594584000],
      [60000, 1001, 4237833600], [25, 1, 10160640000], [30, 1, 8467200000],
      [24, 1, 10584000000], [50, 1, 5080320000], [60, 1, 4233600000], [120, 1, 2116800000]
    ];
    for (const [num, den, perFrame] of cases) {
      assert.strictEqual(SM.ticksForFrame(1, num, den), String(perFrame), `${num}/${den}`);
      assert.strictEqual(SM.ticksForFrame(0, num, den), "0");
      assert.strictEqual(SM.ticksForFrame(416, num, den), String(perFrame * 416), `${num}/${den} @416`);
    }
    // and the sample clip's real marks, at 29.97
    assert.strictEqual(SM.ticksForFrame(93, 30000, 1001), "788237049600");
  });

  await test("ticksForFrame is exact even past the float-safe integer range", () => {
    // Long clips push the tick count beyond 2^53, where doubles stop counting by ones.
    // Expectations are computed independently in BigInt, never hand-multiplied.
    for (const [frame, num, den] of [[1296000, 120000, 1001], [5000000, 120000, 1001], [9000000, 30000, 1001]]) {
      const expected = (BigInt(SM.TICKS_PER_SECOND) * BigInt(den) * BigInt(frame)) / BigInt(num);
      const t = SM.ticksForFrame(frame, num, den);
      assert.strictEqual(t, expected.toString(), `frame ${frame} @ ${num}/${den}`);
      assert.ok(/^\d+$/.test(t), `ticks must be a plain integer string, got ${t}`);
    }
    // At 5M frames the value clears 2^53, so a naive double would already be lying.
    const big = SM.ticksForFrame(5000000, 120000, 1001);
    assert.ok(Number(big) > Number.MAX_SAFE_INTEGER, "expected to exceed the float-safe range");
  });

  console.log("\nKLV + document parsing");
  await test("decodeKlvLabel reads the BER length-prefixed labels", () => {
    assert.strictEqual(SM.decodeKlvLabel(LV_RECSTART), "_RecStart");
    assert.strictEqual(SM.decodeKlvLabel(LV_SHOTMARK1), "_ShotMark1");
    assert.strictEqual(SM.decodeKlvLabel(LV_SHOTMARK2), "_ShotMark2");
    assert.strictEqual(SM.decodeKlvLabel(""), "");
    assert.strictEqual(SM.decodeKlvLabel("FF5F52"), "_R");   // length byte overruns the buffer
  });

  await test("parseNrtXml pulls the sample clip's four C1 marks", () => {
    const xml = NRT_TEMPLATE({
      marks: [[0, LV_RECSTART], [93, LV_SHOTMARK1], [213, LV_SHOTMARK1], [329, LV_SHOTMARK1], [416, LV_SHOTMARK1]]
    });
    const clip = SM.parseNrtXml(xml, "SYNTH.MP4");
    assert.strictEqual(clip.marks.length, 5);
    assert.strictEqual(clip.userMarkCount, 4);
    assert.strictEqual(clip.model, "ILCE-7SM3");
    assert.strictEqual(clip.captureFps, "29.97p");
    assert.strictEqual(clip.startTimecode, "20:44:56;24");   // drop-frame, per the LTC flag
    assert.deepStrictEqual(
      SM.selectMarks(clip, false).map(m => m.captureFrame),
      [93, 213, 329, 416]
    );
    assert.deepStrictEqual(
      SM.selectMarks(clip, true).map(m => m.captureFrame),
      [0, 93, 213, 329, 416]
    );
  });

  await test("a non-essence-mark KLV key is ignored", () => {
    const xml = NRT_TEMPLATE({ marks: [[10, LV_SHOTMARK1]] })
      .replace("060E2B34010101050301020A02000000", "060E2B34010101050301020A02000001");
    assert.strictEqual(SM.parseNrtXml(xml, "x").marks.length, 0);
  });

  await test("a clip with no Shot Marks parses to zero user marks", () => {
    const clip = SM.parseNrtXml(NRT_TEMPLATE({ marks: [[0, LV_RECSTART]] }), "x");
    assert.strictEqual(clip.marks.length, 1);
    assert.strictEqual(clip.userMarkCount, 0);
    assert.strictEqual(SM.selectMarks(clip, false).length, 0);
  });

  await test("ShotMark2 is decoded and kept distinct from ShotMark1", () => {
    const clip = SM.parseNrtXml(NRT_TEMPLATE({ marks: [[5, LV_SHOTMARK1], [7, LV_SHOTMARK2]] }), "x");
    assert.deepStrictEqual(clip.marks.map(m => m.friendly), ["Shot Mark 1", "Shot Mark 2"]);
    assert.ok(clip.marks.every(m => m.isUserMark));
  });

  await test("120p marks land on the right ticks and timecode", () => {
    const clip = SM.parseNrtXml(NRT_TEMPLATE({
      fps: "119.88p", tcFps: 30, marks: [[600, LV_SHOTMARK1]]
    }), "x");
    const m = clip.marks[0];
    assert.strictEqual(m.captureFrame, 600);
    assert.strictEqual(m.ticks, String(2118916800 * 600));
    assert.ok(Math.abs(m.elapsedSeconds - 600 / (120000 / 1001)) < 1e-3);
  });

  console.log("\nreading files without loading them");
  await test("walkTopLevelBoxes maps a synthetic A7S III layout", async () => {
    const buf = synthMp4(NRT_TEMPLATE({ marks: [[1, LV_SHOTMARK1]] }));
    const boxes = await SM.walkTopLevelBoxes(createBufferReader(new Uint8Array(buf)));
    assert.deepStrictEqual(boxes.map(b => b.type), ["ftyp", "free", "mdat", "moov", "meta"]);
    assert.strictEqual(boxes[boxes.length - 1].offset + boxes[boxes.length - 1].size, buf.length,
      "boxes must tile the whole file");
    assert.ok(SM.looksLikeIsoBmff(boxes));
  });

  await test("findNrt reaches metadata that sits after a 4 MB mdat, without reading it", async () => {
    const buf = synthMp4(NRT_TEMPLATE({ marks: [[42, LV_SHOTMARK1]] }));
    const inner = createBufferReader(new Uint8Array(buf));
    const reader = instrument(inner);
    const clip = await SM.readShotMarks(reader, "SYNTH.MP4");
    assert.ok(clip, "expected marks");
    assert.strictEqual(clip.marks[0].captureFrame, 42);
    assert.ok(/^embedded:/.test(clip.metadataSource), clip.metadataSource);

    // ftyp(24) + free(1032) puts the mdat box at 1056; its 8-byte header is fair game,
    // but nothing we read may reach into the payload that follows it.
    const mdatPayload = 24 + 1032 + 8;
    const mdatEnd = mdatPayload + 4 * 1024 * 1024;
    for (const r of reader.reads) {
      const overlaps = r.position < mdatEnd && (r.position + r.length) > mdatPayload;
      assert.ok(!overlaps, `read at ${r.position}+${r.length} touched the media payload`);
    }
    assert.ok(reader.totalBytesRead() < 64 * 1024,
      `read ${reader.totalBytesRead()} bytes to find 2 KB of metadata`);
  });

  await test("a truncated file degrades instead of throwing", async () => {
    const buf = synthMp4(NRT_TEMPLATE({ marks: [[1, LV_SHOTMARK1]] }));
    const cut = new Uint8Array(buf.subarray(0, 40));       // header, then nothing
    assert.strictEqual(await SM.readShotMarks(createBufferReader(cut), "cut.MP4"), null);
  });

  await test("a file with no Sony metadata returns null, not an error", async () => {
    const plain = Buffer.concat([
      Buffer.from([0, 0, 0, 20]), Buffer.from("ftyp", "ascii"), Buffer.alloc(12),
      Buffer.from([0, 0, 4, 0]), Buffer.from("mdat", "ascii"), Buffer.alloc(1016)
    ]);
    assert.strictEqual(await SM.readShotMarks(createBufferReader(new Uint8Array(plain)), "x.MP4"), null);
  });

  await test("a bare sidecar XML is read directly", async () => {
    const xml = Buffer.from(NRT_TEMPLATE({ marks: [[7, LV_SHOTMARK1]] }), "utf8");
    const clip = await SM.readShotMarks(createBufferReader(new Uint8Array(xml)), "C0001M01.XML");
    assert.ok(clip);
    assert.strictEqual(clip.marks[0].captureFrame, 7);
    assert.strictEqual(clip.metadataSource, "raw");
  });

  await test("metadata split across a scan-window boundary is still found", async () => {
    // Force the opening tag to straddle the 1 MiB window edge.
    const xml = NRT_TEMPLATE({ marks: [[3, LV_SHOTMARK1]] });
    const pad = (1 << 20) - 8;
    const payload = Buffer.concat([Buffer.alloc(pad, 0x20), Buffer.from(xml, "utf8")]);
    const clip = await SM.readShotMarks(createBufferReader(new Uint8Array(payload)), "pad.xml");
    assert.ok(clip, "tag on the window boundary was missed");
    assert.strictEqual(clip.marks[0].captureFrame, 3);
  });

  await test("a 64-bit (size==1) mdat box is walked correctly", async () => {
    const nrt = Buffer.from(NRT_TEMPLATE({ marks: [[9, LV_SHOTMARK1]] }), "utf8");
    // size==1 means "the real size is the 8 bytes that follow the type" — here 4096,
    // which must match the box's actual length or the walk desynchronises.
    const big = Buffer.concat([
      u32(1), Buffer.from("mdat", "ascii"), Buffer.from([0, 0, 0, 0, 0, 0, 0x10, 0x00]), Buffer.alloc(4096 - 16)
    ]);
    const buf = Buffer.concat([box("ftyp", Buffer.alloc(8)), big, box("meta", nrt)]);
    const boxes = await SM.walkTopLevelBoxes(createBufferReader(new Uint8Array(buf)));
    assert.deepStrictEqual(boxes.map(b => b.type), ["ftyp", "mdat", "meta"]);
    assert.strictEqual(boxes[1].size, 4096);
    const clip = await SM.readShotMarks(createBufferReader(new Uint8Array(buf)), "big.MP4");
    assert.strictEqual(clip.marks[0].captureFrame, 9);
  });

  console.log("\npath handling (macOS + Windows)");
  await test("Windows paths normalize for UXP's file: scheme", () => {
    assert.strictEqual(P.toFileUrl("D:\\Card\\C0001.MP4"), "file:/D:/Card/C0001.MP4");
    assert.strictEqual(P.toFileUrl("C:/Users/me/My Clips/C0001.MP4"), "file:/C:/Users/me/My Clips/C0001.MP4");
    assert.strictEqual(P.toFileUrl("/Volumes/Card/C0001.MP4"), "file:/Volumes/Card/C0001.MP4");
    assert.strictEqual(P.toFileUrl("\\\\nas\\share\\C0001.MP4"), "file://nas/share/C0001.MP4");
    assert.strictEqual(P.toFileUrl("file:/already/encoded.MP4"), "file:/already/encoded.MP4");
  });

  await test("spaces survive URL conversion unencoded", () => {
    // UXP resolves these as paths; percent-encoding a space makes the open fail.
    assert.ok(P.toFileUrl("/Volumes/My Card/A B.MP4").indexOf("%20") === -1);
    assert.strictEqual(P.toFileUrl("/Volumes/My Card/A B.MP4"), "file:/Volumes/My Card/A B.MP4");
  });

  await test("basename/dirname/stem work on both separators", () => {
    assert.strictEqual(P.basename("D:\\Card\\C0001.MP4"), "C0001.MP4");
    assert.strictEqual(P.basename("/Volumes/Card/C0001.MP4"), "C0001.MP4");
    assert.strictEqual(P.dirname("D:\\Card\\C0001.MP4"), "D:/Card");
    assert.strictEqual(P.dirname("/Volumes/Card/C0001.MP4"), "/Volumes/Card");
    assert.strictEqual(P.stem("D:\\Card\\C0001.MP4"), "C0001");
    assert.strictEqual(P.stem("/a/b/no-extension"), "no-extension");
    assert.strictEqual(P.extname("/a/b/C0001.MP4"), ".MP4");
  });

  await test("sidecar candidates follow Sony's naming on both platforms", () => {
    const win = P.sidecarCandidates("D:\\PRIVATE\\M4ROOT\\CLIP\\C0001.MP4");
    assert.ok(win.includes("D:/PRIVATE/M4ROOT/CLIP/C0001M01.XML"), win.join(", "));
    assert.ok(win.includes("D:/PRIVATE/M4ROOT/CLIP/C0001M01.xml"));
    const mac = P.sidecarCandidates("/Volumes/Card/SIMON7034.MP4");
    assert.ok(mac.includes("/Volumes/Card/SIMON7034M01.XML"), mac.join(", "));
    assert.strictEqual(new Set(mac).size, mac.length, "candidates must be de-duplicated");
  });

  await test("only real media extensions are offered to the reader", () => {
    assert.ok(P.isSupportedMedia("/a/C0001.MP4"));
    assert.ok(P.isSupportedMedia("/a/C0001.mov"));
    assert.ok(P.isSupportedMedia("D:\\a\\C0001.MXF"));
    assert.ok(!P.isSupportedMedia("/a/title.psd"));
    assert.ok(!P.isSupportedMedia("/a/sequence"));
  });

  // ── real footage, when this machine has it ────────────────────────────────────
  console.log("\nreal Sony footage");
  const sampleMp4 = process.env.SHOTMARK_SAMPLE || findSample("SIMON7034.MP4");
  const sampleXml = findSample("SIMON7034M01.XML");

  if (sampleMp4 && fs.existsSync(sampleMp4)) {
    await test(`reads ${path.basename(sampleMp4)} (${(fs.statSync(sampleMp4).size / 1e6).toFixed(0)} MB) end to end`, async () => {
      const reader = instrument(createNodeFileReader(sampleMp4));
      const clip = await SM.readShotMarks(reader, path.basename(sampleMp4));
      assert.ok(clip, "no Sony metadata found in the sample");
      assert.strictEqual(clip.model, "ILCE-7SM3");
      assert.strictEqual(clip.captureFps, "29.97p");
      assert.strictEqual(clip.userMarkCount, 4);
      assert.deepStrictEqual(
        SM.selectMarks(clip, false).map(m => m.captureFrame),
        [93, 213, 329, 416],
        "the four verified C1 presses"
      );
      assert.deepStrictEqual(
        SM.selectMarks(clip, false).map(m => m.ticks),
        [93, 213, 329, 416].map(f => String(8475667200 * f))
      );
      // The exact source timecodes tools/sony_shotmark.py prints for this clip.
      assert.strictEqual(clip.startTimecode, "20:44:56;24");
      assert.deepStrictEqual(
        SM.selectMarks(clip, false).map(m => m.sourceTimecode),
        ["20:44:59;27", "20:45:03;29", "20:45:07;25", "20:45:10;22"]
      );
      assert.strictEqual(clip.metadataSource, "embedded:meta");
    });

    await test("finds them without reading a meaningful fraction of the 335 MB file", async () => {
      const reader = instrument(createNodeFileReader(sampleMp4));
      await SM.readShotMarks(reader, "SIMON7034.MP4");
      const total = reader.totalBytesRead();
      const fileSize = fs.statSync(sampleMp4).size;
      assert.ok(total < 1024 * 1024, `read ${total} bytes (want < 1 MB)`);
      assert.ok(total / fileSize < 0.005, `read ${(100 * total / fileSize).toFixed(3)}% of the file`);
      console.log(`       read ${total.toLocaleString()} bytes of ${fileSize.toLocaleString()} ` +
                  `(${(100 * total / fileSize).toFixed(4)}%) in ${reader.reads.length} reads`);
    });

    await test("never opens the sample for writing", async () => {
      const before = fs.statSync(sampleMp4);
      const reader = createNodeFileReader(sampleMp4);
      await SM.readShotMarks(reader, "SIMON7034.MP4");
      reader.close();
      const after = fs.statSync(sampleMp4);
      assert.strictEqual(after.size, before.size);
      assert.strictEqual(after.mtimeMs, before.mtimeMs, "the source file was modified");
    });
  } else {
    skip("real .MP4 sample", "SIMON7034.MP4 not on this machine — set SHOTMARK_SAMPLE");
  }

  if (sampleXml && fs.existsSync(sampleXml)) {
    await test("the M01.XML sidecar agrees with the embedded copy", async () => {
      const clip = await SM.readShotMarks(createNodeFileReader(sampleXml), path.basename(sampleXml));
      assert.ok(clip);
      assert.deepStrictEqual(
        SM.selectMarks(clip, false).map(m => m.captureFrame),
        [93, 213, 329, 416]
      );
      assert.strictEqual(clip.startTimecode, "20:44:56;24");
    });
  } else {
    skip("real M01.XML sidecar", "not on this machine");
  }

  // ── differential: the JS port must agree with the Python engine that produced the
  //    XMP embeds people are already using. Any drift here is a real bug, not a nit.
  console.log("\nagreement with tools/sony_shotmark.py");
  const pythonTarget = (sampleMp4 && fs.existsSync(sampleMp4)) ? sampleMp4
                     : (sampleXml && fs.existsSync(sampleXml)) ? sampleXml : null;
  if (pythonTarget) {
    let py = null;
    try {
      const { execFileSync } = require("child_process");
      const out = execFileSync("python3", [
        path.join(REPO, "tools", "sony_shotmark.py"), pythonTarget, "--json", "/dev/stdout", "--quiet"
      ], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
      py = JSON.parse(out.slice(out.indexOf("{"), out.lastIndexOf("}") + 1));
    } catch (e) {
      skip("field-by-field match with the Python engine", "python3 or the tool is unavailable");
    }
    if (py) {
      await test("field-by-field match with the Python engine", async () => {
        const js = await SM.readShotMarks(createNodeFileReader(pythonTarget), path.basename(pythonTarget));
        assert.ok(js, "JS found no marks where Python did");
        assert.strictEqual(js.captureFps, py.capture_fps, "capture fps");
        assert.strictEqual(js.tcFps, py.tc_fps, "tc fps");
        assert.strictEqual(js.dropFrame, py.drop_frame, "drop frame");
        assert.strictEqual(js.startTimecode, py.start_timecode, "start timecode");
        assert.strictEqual(js.durationFrames, py.duration_frames, "duration");
        assert.strictEqual(js.model, py.model, "camera model");
        assert.strictEqual(js.userMarkCount, py.user_mark_count, "user mark count");
        assert.strictEqual(js.marks.length, py.marks.length, "total mark count");
        for (let i = 0; i < py.marks.length; i++) {
          const a = js.marks[i], b = py.marks[i];
          assert.strictEqual(a.label, b.label, `mark ${i} label`);
          assert.strictEqual(a.captureFrame, b.capture_frame, `mark ${i} frame`);
          assert.strictEqual(a.isUserMark, b.is_user_mark, `mark ${i} user flag`);
          assert.strictEqual(a.sourceTimecode, b.source_timecode, `mark ${i} timecode`);
          assert.ok(Math.abs(a.elapsedSeconds - b.elapsed_seconds) < 1e-4, `mark ${i} elapsed`);
        }
        console.log(`       ${py.marks.length} marks agree on label, frame, timecode and elapsed`);
      });
    }
  } else {
    skip("field-by-field match with the Python engine", "no real sample on this machine");
  }

  console.log(`\n${passed} passed, ${failed} failed, ${skipped} skipped\n`);
  if (failed) {
    for (const [name, e] of failures) console.error(`FAILED: ${name}\n${e.stack}\n`);
    process.exit(1);
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
