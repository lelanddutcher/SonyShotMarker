// Embedder.swift — pure-Swift Sony Shot Mark → embedded XMP. No python, no exiftool,
// no external processes. Mirrors tools/sony_shotmark.py's validated algorithm.
//
// Reads the Sony NonRealTimeMeta XML from the file, decodes the Shot Marks + start
// timecode, builds an Adobe Dynamic Media (xmpDM) marker doc, and writes a COPY of the
// clip with that XMP appended as a top-level Adobe XMP `uuid` box (offset-safe at EOF).

import Foundation

struct ShotMark {
    let label: String, name: String, frame: Int, elapsed: Double, tc: String
}

enum Embedder {
    static let essenceKey = "060E2B34010101050301020A02000000"
    static let adobeUUID: [UInt8] = [0xBE,0x7A,0xCF,0xCB,0x97,0xA9,0x42,0xE8,0x9C,0x71,0x99,0x94,0x91,0xE3,0xAF,0xAC]
    static let fpsTable: [String:(Int,Int)] = [
        "23.98":(24000,1001),"23.976":(24000,1001),"24":(24,1),"25":(25,1),
        "29.97":(30000,1001),"30":(30,1),"47.95":(48000,1001),"48":(48,1),
        "50":(50,1),"59.94":(60000,1001),"60":(60,1),"100":(100,1),
        "119.88":(120000,1001),"120":(120,1)]

    enum Result { case embedded(Int, URL), skippedNoMarks, notSony, failed(String), cancelled }

    // MARK: parsing helpers
    private static func fpsRational(_ s: String) -> (Int, Int) {
        let key = s.trimmingCharacters(in: CharacterSet(charactersIn: "pPiI ")).trimmingCharacters(in: .whitespaces)
        if let v = fpsTable[key] { return v }
        let f = Double(key) ?? 30
        return abs(f - f.rounded()) > 0.01 ? (Int(f.rounded()) * 1000, 1001) : (Int(f.rounded()), 1)
    }

    private static func hexBytes(_ s: String) -> [UInt8] {
        var out = [UInt8](); var i = s.startIndex
        while i < s.endIndex, let j = s.index(i, offsetBy: 2, limitedBy: s.endIndex) {
            if let b = UInt8(s[i..<j], radix: 16) { out.append(b) }
            i = j
        }
        return out
    }

    private static func decodeLTC(_ hex: String) -> (Int,Int,Int,Int,Bool) {
        let b = hexBytes(hex); guard b.count == 4 else { return (0,0,0,0,false) }
        let drop = b[0] & 0x40 != 0
        let F = Int((b[0] & 0x30) >> 4) * 10 + Int(b[0] & 0x0F)
        let S = Int((b[1] & 0x70) >> 4) * 10 + Int(b[1] & 0x0F)
        let M = Int((b[2] & 0x70) >> 4) * 10 + Int(b[2] & 0x0F)
        let H = Int((b[3] & 0x30) >> 4) * 10 + Int(b[3] & 0x0F)
        return (H,M,S,F,drop)
    }

    private static func tcToFrames(_ h:Int,_ m:Int,_ s:Int,_ f:Int,_ nominal:Int,_ drop:Bool) -> Int {
        var total = ((h*60+m)*60+s)*nominal + f
        if drop { let dpm = 2*(nominal/30); let tm = h*60+m; total -= dpm*(tm - tm/10) }
        return total
    }

    private static func framesToTC(_ frames0:Int,_ nominal:Int,_ drop:Bool) -> String {
        var frames = frames0
        if drop {
            let dpm = 2*(nominal/30)
            let f10 = nominal*600 - dpm*9
            let d = frames / f10, mod = frames % f10
            let fpm = nominal*60 - dpm
            frames += dpm*9*d + (mod >= dpm ? dpm*((mod-dpm)/fpm) : 0)
        }
        let sep = drop ? ";" : ":"
        return String(format: "%02d:%02d:%02d%@%02d",
                      frames/(nominal*3600)%24, frames/(nominal*60)%60, frames/nominal%60, sep, frames%nominal)
    }

    private static func firstMatch(_ s: String, _ pattern: String) -> String? {
        guard let re = try? NSRegularExpression(pattern: pattern) else { return nil }
        let r = NSRange(s.startIndex..., in: s)
        guard let m = re.firstMatch(in: s, range: r), let g = Range(m.range(at: 1), in: s) else { return nil }
        return String(s[g])
    }

    private static func attr(_ xml: String, _ tag: String, _ a: String) -> String {
        firstMatch(xml, "<\(tag)[^>]*\\b\(a)=\"([^\"]*)\"") ?? ""
    }

    static func readMarks(from url: URL) -> [ShotMark]? {
        guard let xml = locateNonRealTimeMeta(in: url) else { return nil }

        let (num, den) = fpsRational(attr(xml, "VideoFrame", "captureFps").isEmpty
                                     ? attr(xml, "VideoFrame", "formatFps") : attr(xml, "VideoFrame", "captureFps"))
        let capExact = Double(num) / Double(den)
        let tcfps = Int(attr(xml, "LtcChangeTable", "tcFps")) ?? 30
        let ltcHex = firstMatch(xml, "<LtcChange\\b[^>]*value=\"([0-9A-Fa-f]{8})\"") ?? "00000000"
        let (H,M,S,F,drop) = decodeLTC(ltcHex)
        let ntsc = drop || den == 1001
        let (tcNum, tcDen) = ntsc ? (tcfps*1000, 1001) : (tcfps, 1)
        let nominal = Int((Double(tcNum)/Double(tcDen)).rounded())
        let start = tcToFrames(H,M,S,F,nominal,drop)

        var marks = [ShotMark]()
        let pat = "<KlvPacket\\b[^>]*key=\"([0-9A-Fa-f]{32})\"[^>]*frameCount=\"(\\d+)\"[^>]*lengthValue=\"([0-9A-Fa-f]+)\""
        guard let re = try? NSRegularExpression(pattern: pat) else { return marks }
        re.enumerateMatches(in: xml, range: NSRange(xml.startIndex..., in: xml)) { mm, _, _ in
            guard let m = mm,
                  let kr = Range(m.range(at:1), in:xml), let fr = Range(m.range(at:2), in:xml),
                  let lr = Range(m.range(at:3), in:xml) else { return }
            if String(xml[kr]).uppercased() != essenceKey { return }
            let lb = hexBytes(String(xml[lr]))
            guard lb.count >= 1 else { return }
            let n = Int(lb[0]); guard lb.count >= 1+n else { return }
            let label = String(decoding: lb[1..<1+n], as: UTF8.self)
            if !label.hasPrefix("_ShotMark") { return }
            let frame = Int(xml[fr]) ?? 0
            let elapsed = Double(frame) / capExact
            let tc = framesToTC(start + Int((elapsed * Double(tcNum)/Double(tcDen)).rounded()), nominal, drop)
            marks.append(ShotMark(label: label, name: "Shot Mark " + label.replacingOccurrences(of: "_ShotMark", with: ""),
                                  frame: frame, elapsed: elapsed, tc: tc))
        }
        return marks
    }

    // MARK: XMP build
    static func xmpFrameRate(from url: URL) -> String {
        guard let xml = locateNonRealTimeMeta(in: url) else { return "f30" }
        let fps = attr(xml, "VideoFrame", "captureFps").isEmpty
            ? attr(xml, "VideoFrame", "formatFps") : attr(xml, "VideoFrame", "captureFps")
        let (num, den) = fpsRational(fps)
        return den != 1 ? "f\(num)s\(den)" : "f\(num)"
    }

    private static func esc(_ s: String) -> String {
        s.replacingOccurrences(of:"&",with:"&amp;").replacingOccurrences(of:"<",with:"&lt;")
         .replacingOccurrences(of:">",with:"&gt;").replacingOccurrences(of:"\"",with:"&quot;")
    }

    static func buildXMP(marks: [ShotMark], frameRate: String) -> String {
        let li = marks.map { m in
            "              <rdf:li xmpDM:startTime=\"\(m.frame)\" xmpDM:duration=\"0\" " +
            "xmpDM:name=\"\(esc(m.name))\" xmpDM:comment=\"\(esc(m.label)) | src TC \(m.tc) | " +
            String(format:"%.3f", m.elapsed) + "s\"/>"
        }.joined(separator: "\n")
        let body = """
        <?xpacket begin="\u{FEFF}" id="W5M0MpCehiHzreSzNTczkc9d"?>
        <x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="SonyShotMarker">
          <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
            <rdf:Description rdf:about="" xmlns:xmpDM="http://ns.adobe.com/xmp/1.0/DynamicMedia/">
              <xmpDM:Tracks><rdf:Bag><rdf:li>
                <rdf:Description xmpDM:trackName="Shot Marks" xmpDM:trackType="Comment" xmpDM:frameRate="\(frameRate)">
                  <xmpDM:markers><rdf:Seq>
        \(li)
                  </rdf:Seq></xmpDM:markers>
                </rdf:Description>
              </rdf:li></rdf:Bag></xmpDM:Tracks>
            </rdf:Description>
          </rdf:RDF>
        </x:xmpmeta>
        <?xpacket end="w"?>
        """
        return body
    }

    // MARK: MP4 box walking (big-endian; files < 2 GB)
    private struct Mp4Box { let type: String; let offset: Int; let size: Int }

    private static func beU32(_ d: Data, _ o: Int) -> Int {
        let b = d.startIndex + o
        return (Int(d[b]) << 24) | (Int(d[b+1]) << 16) | (Int(d[b+2]) << 8) | Int(d[b+3])
    }
    private static func beU64(_ d: Data, _ o: Int) -> Int {
        (beU32(d, o) << 32) | beU32(d, o + 4)
    }
    private static func boxType(_ d: Data, _ o: Int) -> String {
        let b = d.startIndex + o
        return String(bytes: d[b..<b+4], encoding: .ascii) ?? "????"
    }
    private static func topBoxes(_ d: Data) -> [Mp4Box] {
        var r = [Mp4Box](); var pos = 0; let n = d.count
        while pos + 8 <= n {
            let s32 = beU32(d, pos); let t = boxType(d, pos + 4)
            var size = s32
            if s32 == 1 { size = beU64(d, pos + 8) } else if s32 == 0 { size = n - pos }
            if size < 8 || pos + size > n { break }
            r.append(Mp4Box(type: t, offset: pos, size: size))
            pos += size
        }
        return r
    }

    // Seek-based box walk: read only the 8/16-byte headers and seek past payloads, so a
    // multi-GB `mdat` is skipped, never paged in. Mirrors handoff/swift/SonyShotMarks.swift.
    private static func topBoxesStreaming(_ fh: FileHandle, _ end: Int) -> [Mp4Box] {
        var out = [Mp4Box](); var pos = 0
        while pos + 8 <= end {
            try? fh.seek(toOffset: UInt64(pos))
            guard let h = try? fh.read(upToCount: 8), h.count == 8 else { break }
            var size = beU32(h, 0)
            let type = boxType(h, 4)
            if size == 1 {
                guard let e = try? fh.read(upToCount: 8), e.count == 8 else { break }
                size = beU64(e, 0)
            } else if size == 0 { size = end - pos }
            if size < 8 || pos + size > end { break }
            out.append(Mp4Box(type: type, offset: pos, size: size))
            pos += size
        }
        return out
    }

    /// Locate the NonRealTimeMeta XML by reading only the small non-`mdat` boxes (≤4 MB each)
    /// — no whole-file scan. Replaces the old Data(contentsOf:.mappedIfSafe)+range(of:) read.
    private static func locateNonRealTimeMeta(in url: URL) -> String? {
        guard let fh = try? FileHandle(forReadingFrom: url) else { return nil }
        defer { try? fh.close() }
        let end = Int((try? fh.seekToEnd()) ?? 0)
        for b in topBoxesStreaming(fh, end) where b.type != "mdat" {
            try? fh.seek(toOffset: UInt64(b.offset))
            let cap = min(b.size, 4_000_000)
            guard let data = try? fh.read(upToCount: cap),
                  let lo = data.range(of: Data("<NonRealTimeMeta".utf8)),
                  let hi = data.range(of: Data("</NonRealTimeMeta>".utf8)) else { continue }
            return String(decoding: data[lo.lowerBound..<hi.upperBound], as: UTF8.self)
        }
        return nil
    }

    private static func volumeURL(_ u: URL) -> URL? {
        (try? u.resourceValues(forKeys: [.volumeURLKey]))?.volume
    }

    /// True when src and the destination live on the same volume (→ APFS clone, ~free).
    private static func sameVolume(_ src: URL, _ destFolder: URL) -> Bool {
        var probe = destFolder
        while !FileManager.default.fileExists(atPath: probe.path) {
            let parent = probe.deletingLastPathComponent()
            if parent.path == probe.path { break }
            probe = parent
        }
        guard let a = volumeURL(src), let b = volumeURL(probe) else { return false }
        return a == b
    }

    /// Chunked byte copy with live progress + mid-file cancel (cross-volume path). Returns
    /// false if cancelled (caller cleans up the partial). Same-volume copies clone instead.
    private static func chunkedCopy(from src: URL, to dst: URL,
                                    onBytes: ((Int64, Int64) -> Void)?, cancel: CancelToken?) throws -> Bool {
        let total = (try? src.resourceValues(forKeys: [.fileSizeKey]).fileSize).map { Int64($0) } ?? 0
        FileManager.default.createFile(atPath: dst.path, contents: nil)
        let r = try FileHandle(forReadingFrom: src)
        let w = try FileHandle(forWritingTo: dst)
        defer { try? r.close(); try? w.close() }
        let chunk = 4 * 1024 * 1024
        var copied: Int64 = 0
        onBytes?(0, total)
        while true {
            if cancel?.isCancelled == true { return false }
            let buf = (try r.read(upToCount: chunk)) ?? Data()
            if buf.isEmpty { break }
            try w.write(contentsOf: buf)
            copied += Int64(buf.count)
            onBytes?(copied, total)
        }
        try w.synchronize()
        return true
    }

    // MARK: embed — write the XMP into the `free` box BEFORE mdat (where Premiere reads
    // it), and neutralize any existing Adobe XMP box so ours wins. mdat never moves, so
    // chunk offsets stay valid — no rewrite, no faststart needed.
    static func embed(src: URL, intoFolder folder: URL,
                      onBytes: ((Int64, Int64) -> Void)? = nil, cancel: CancelToken? = nil) -> Result {
        guard let marks0 = readMarks(from: src) else { return .notSony }
        let user = marks0.filter { $0.label.hasPrefix("_ShotMark") }
        guard !user.isEmpty else { return .skippedNoMarks }
        guard ["mp4","mov","m4v"].contains(src.pathExtension.lowercased()) else { return .skippedNoMarks }

        // build our Adobe XMP uuid box
        let xmp = Data(buildXMP(marks: user, frameRate: xmpFrameRate(from: src)).utf8)
        var uuidBox = Data()
        var bsize = UInt32(8 + 16 + xmp.count).bigEndian
        withUnsafeBytes(of: &bsize) { uuidBox.append(contentsOf: $0) }
        uuidBox.append("uuid".data(using: .ascii)!)
        uuidBox.append(contentsOf: adobeUUID)
        uuidBox.append(xmp)
        let L = uuidBox.count

        // Parse only the boxes BEFORE mdat by seeking headers — never page in the media.
        guard let rfh = try? FileHandle(forReadingFrom: src) else { return .failed("cannot open source") }
        let end = Int((try? rfh.seekToEnd()) ?? 0)
        let boxes = topBoxesStreaming(rfh, end)
        guard let mdat = boxes.first(where: { $0.type == "mdat" })?.offset else {
            try? rfh.close(); return .failed("no mdat box")
        }
        guard let free = boxes.first(where: {
            ($0.type == "free" || $0.type == "skip") && $0.offset < mdat && $0.size >= L + 8
        }) else {
            try? rfh.close(); return .failed("no reusable free space before mdat (need \(L + 8) bytes)")
        }
        var neutralize = [Int]()
        for b in boxes where b.type == "uuid" && b.offset < mdat && b.size >= 24 {
            try? rfh.seek(toOffset: UInt64(b.offset + 8))
            if (try? rfh.read(upToCount: 16)) == Data(adobeUUID) { neutralize.append(b.offset) }
        }
        try? rfh.close()

        let fm = FileManager.default
        do { try fm.createDirectory(at: folder, withIntermediateDirectories: true) } catch {}
        let dest = folder.appendingPathComponent(src.lastPathComponent)
        let partial = folder.appendingPathComponent(src.lastPathComponent + ".partial")
        let same = sameVolume(src, folder)
        do {
            if fm.fileExists(atPath: partial.path) { try fm.removeItem(at: partial) }
            if same {
                // same volume → instant APFS clone (~0 extra bytes); report it as complete.
                try fm.copyItem(at: src, to: partial)
                let total = (try? src.resourceValues(forKeys: [.fileSizeKey]).fileSize).map { Int64($0) } ?? 0
                onBytes?(total, total)
            } else {
                // cross volume → real byte copy with a live bar + mid-file cancel.
                if try !chunkedCopy(from: src, to: partial, onBytes: onBytes, cancel: cancel) {
                    try? fm.removeItem(at: partial)
                    return .cancelled
                }
            }
            let h = try FileHandle(forWritingTo: partial)
            // 1) neutralize prior Adobe XMP boxes (type 'uuid' -> 'free')
            for off in neutralize {
                try h.seek(toOffset: UInt64(off + 4)); try h.write(contentsOf: Data("free".utf8))
            }
            // 2) write our uuid box + a shrunk free header into the free box (mdat unmoved)
            var newFree = Data()
            var fsize = UInt32(free.size - L).bigEndian
            withUnsafeBytes(of: &fsize) { newFree.append(contentsOf: $0) }
            newFree.append("free".data(using: .ascii)!)
            try h.seek(toOffset: UInt64(free.offset))
            try h.write(contentsOf: uuidBox)
            try h.write(contentsOf: newFree)
            try h.synchronize()
            try h.close()
            // atomic finalize: the embedded copy only appears at `dest` once fully written
            if fm.fileExists(atPath: dest.path) { try fm.removeItem(at: dest) }
            try fm.moveItem(at: partial, to: dest)
            return .embedded(user.count, dest)
        } catch {
            try? fm.removeItem(at: partial)   // never leave a stray half-file
            return .failed(error.localizedDescription)
        }
    }

    // MARK: free-space preflight (CoW-aware)
    /// Same-volume copies are APFS clones (~free); cross-volume needs ~the full input size.
    /// Returns whether the destination volume has room, plus the numbers for the UI.
    static func enoughSpace(src: [URL], dest: URL) -> (ok: Bool, required: Int64, free: Int64, sameVolume: Bool) {
        var srcVol: URL? = nil, destVol: URL? = nil, free: Int64 = -1
        if let first = src.first, let rv = try? first.resourceValues(forKeys: [.volumeURLKey]) { srcVol = rv.volume }
        if let rv = try? dest.resourceValues(forKeys: [.volumeURLKey]) { destVol = rv.volume }
        if let rv = try? dest.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]),
           let cap = rv.volumeAvailableCapacityForImportantUsage { free = cap }
        let same = srcVol != nil && srcVol == destVol
        var total: Int64 = 0
        for u in src {
            if let rv = try? u.resourceValues(forKeys: [.fileSizeKey]), let s = rv.fileSize { total += Int64(s) }
        }
        // same-volume clone diverges only by our ~few-KB patch each → tiny; cross-volume = full size.
        let required: Int64 = same ? Int64(128 * 1024 * 1024) : Int64(Double(total) * 1.02)
        let ok = free < 0 || free >= required
        return (ok, required, free, same)
    }

    // MARK: post-embed verify — re-open the written copy and confirm the Adobe XMP markers
    // are present, parseable, and positioned BEFORE mdat (the only place Premiere reads
    // them). This is the integrity gate that lets the app honor "verify before you delete
    // the originals": a clip is only marked ✓ once its copy reads back correctly.
    static func verifyEmbedded(_ url: URL, expected: Int) -> (ok: Bool, detail: String) {
        guard let data = try? Data(contentsOf: url, options: .mappedIfSafe) else {
            return (false, "cannot reopen output")
        }
        let boxes = topBoxes(data)
        guard let mdat = boxes.first(where: { $0.type == "mdat" })?.offset else {
            return (false, "no mdat box in output (structure broken)")
        }
        var payload: Data? = nil
        for b in boxes where b.type == "uuid" && b.offset < mdat && b.size >= 24 {
            let u = data.startIndex + b.offset + 8
            if Array(data[u..<u+16]) == adobeUUID {
                let s = data.startIndex + b.offset + 24
                let e = data.startIndex + b.offset + b.size
                payload = data[s..<e]
            }
        }
        guard let xmp = payload else { return (false, "no Adobe XMP marker box before mdat") }
        let count = String(decoding: xmp, as: UTF8.self).components(separatedBy: "xmpDM:startTime").count - 1
        if count == 0 { return (false, "XMP box present but holds no markers") }
        if count != expected { return (false, "expected \(expected) marker(s), found \(count)") }
        // structural sanity: top-level boxes must tile the whole file with no gap/overrun
        let walked = boxes.reduce(0) { $0 + $1.size }
        if walked != data.count { return (false, "box walk does not cover the whole file") }
        return (true, "\(count) marker(s) verified")
    }

    // MARK: preflight gate — everything that must hold before we touch a byte.
    static func preflightProblems(src: [URL], dest: URL) -> [String] {
        var problems = [String]()
        let fm = FileManager.default
        for f in src {
            if !fm.fileExists(atPath: f.path) { problems.append("missing source: \(f.lastPathComponent)") }
            else if !fm.isReadableFile(atPath: f.path) { problems.append("unreadable source: \(f.lastPathComponent)") }
        }
        var probe = dest
        while !fm.fileExists(atPath: probe.path) {
            let parent = probe.deletingLastPathComponent()
            if parent.path == probe.path { break }
            probe = parent
        }
        if !fm.isWritableFile(atPath: probe.path) { problems.append("output is read-only: \(probe.lastPathComponent)") }
        for f in src where f.deletingLastPathComponent().standardizedFileURL == dest.standardizedFileURL {
            problems.append("output would overwrite the original: \(f.lastPathComponent)")
        }
        // measure free space against the nearest existing dir (the dest subfolder may not exist yet)
        let space = enoughSpace(src: src, dest: probe)
        if !space.ok { problems.append("not enough space: need ~\(RunLog.human(space.required)), \(RunLog.human(space.free)) free") }
        return problems
    }

    /// Copies from a previous run that already exist at the destination and would be replaced.
    static func existingOutputs(src: [URL], dest: URL) -> [URL] {
        let fm = FileManager.default
        return src.compactMap { f in
            let d = dest.appendingPathComponent(f.lastPathComponent)
            return fm.fileExists(atPath: d.path) ? d : nil
        }
    }
}

/// Thread-safe cancel flag shared between the UI (sets it) and the background worker (polls it).
final class CancelToken {
    private let lock = NSLock()
    private var cancelled = false
    var isCancelled: Bool { lock.lock(); defer { lock.unlock() }; return cancelled }
    func cancel() { lock.lock(); cancelled = true; lock.unlock() }
}
