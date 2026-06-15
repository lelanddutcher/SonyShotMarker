// EmbedMarkers — self-contained macOS app: drop Sony clips → a "footage embedded
// markers" folder of XMP-embedded copies (for Premiere/Bridge). Pure Swift, no python,
// no exiftool, no external processes. Run:  cd app && swift run

import SwiftUI
import AppKit
import UniformTypeIdentifiers

private let tonguePink = Color(red: 0.97, green: 0.34, blue: 0.55)   // ties to the cat's tongue
private let ink = Color(white: 0.12)        // near-black title text (readable on white)
private let inkSoft = Color(white: 0.40)    // secondary text, still high-contrast on white
private let hairline = Color(white: 0.55)   // borders

@main
struct EmbedMarkersApp: App {
    init() {
        let a = CommandLine.arguments
        if let i = a.firstIndex(of: "--embed-cli") {
            EmbedCLI.run(Array(a[(i + 1)...]))
            exit(0)
        }
    }
    var body: some Scene {
        WindowGroup("Shot Mark Embedder") {
            ContentView().frame(width: 640, height: 580)
        }
        .windowResizability(.contentSize)
        .commands {
            CommandGroup(replacing: .appInfo) {
                Button("About Shot Mark Embedder") { showAboutPanel() }
            }
            CommandGroup(after: .appInfo) {
                Button("Report a Problem…") { RunLog.reportProblem() }
                Button("Open Logs Folder") { NSWorkspace.shared.activateFileViewerSelecting([RunLog.dir]) }
            }
        }
    }
}

private func showAboutPanel() {
    let credits = """
    Sony Shot Marks → native clip markers, for free.

    Drop already-offloaded Sony clips and Shot Mark Embedder reads the on-camera \
    Shot Marks (the frame-accurate marks you drop with the C1/C2 button) and writes \
    them into a copy of each file as Adobe XMP markers — so they show up natively in \
    Premiere Pro on import. No plugin, no subscription, no watermark. The repo also \
    ships a DaVinci Resolve script that applies the same marks as Resolve clip markers.

    Originals are never modified. Only the copies in “footage embedded markers” are written.

    github.com/lelanddutcher/SonyShotMarker
    """
    let opts: [NSApplication.AboutPanelOptionKey: Any] = [
        .credits: NSAttributedString(string: credits, attributes: [
            .font: NSFont.systemFont(ofSize: 11),
            .foregroundColor: NSColor.labelColor,
            .paragraphStyle: { let p = NSMutableParagraphStyle(); p.lineSpacing = 2; return p }()
        ]),
        NSApplication.AboutPanelOptionKey(rawValue: "Copyright"): "© 2026 Leland Dutcher · MIT License"
    ]
    NSApplication.shared.orderFrontStandardAboutPanel(options: opts)
    NSApplication.shared.activate(ignoringOtherApps: true)
}

enum EmbedCLI {
    static func run(_ args: [String]) {
        guard let outArg = args.first else { print("usage: --embed-cli <outdir> <files...>"); return }
        let out = URL(fileURLWithPath: outArg)
        let folder = out.appendingPathComponent("footage embedded markers")
        let files = args.dropFirst().map { URL(fileURLWithPath: $0) }
        let space = Embedder.enoughSpace(src: files, dest: out)
        var log = RunLog.header(output: out, freeBytes: space.free, sameVolume: space.sameVolume)
        log.append("inputs (\(files.count)):")
        for f in files {
            let sz = (try? f.resourceValues(forKeys: [.fileSizeKey]))?.fileSize ?? 0
            log.append("  \(f.lastPathComponent)  \(RunLog.human(Int64(sz)))")
        }
        log.append("results:")
        var ok = 0, skipped = 0, failed = 0
        for f in files {
            var res = Embedder.embed(src: f, intoFolder: folder)
            if case .embedded(let n, let d) = res {           // post-embed verify
                let v = Embedder.verifyEmbedded(d, expected: n)
                if !v.ok { try? FileManager.default.removeItem(at: d); res = .failed("failed verify (\(v.detail))") }
            }
            let line: String
            switch res {
            case .embedded(let n, let d): ok += 1; line = "✓ \(f.lastPathComponent): \(n) mark(s), verified → \(d.path)"
            case .skippedNoMarks:         skipped += 1; line = "– \(f.lastPathComponent): no Shot Marks"
            case .notSony:                skipped += 1; line = "– \(f.lastPathComponent): not Sony"
            case .failed(let e):          failed += 1; line = "✗ \(f.lastPathComponent): \(e)"
            case .cancelled:              line = "⏹ \(f.lastPathComponent): cancelled"
            }
            print(line); log.append("  " + line)
        }
        var tallyParts = ["✓\(ok)"]
        if skipped > 0 { tallyParts.append("–\(skipped)") }
        if failed > 0 { tallyParts.append("✗\(failed)") }
        let tally = tallyParts.joined(separator: " · ")
        log.append("summary: \(ok)/\(files.count) embedded   [\(tally)]")
        let logURL = RunLog.write(lines: log)
        print("\(tally)  →  \(folder.path)")
        if let logURL { print("log: \(logURL.path)") }
    }
}

struct ContentView: View {
    @State private var files: [URL] = []
    @State private var outputDir: URL?
    @State private var isTargeted = false
    @State private var running = false
    @State private var progress: Double = 0
    @State private var statusLine = ""
    @State private var cancelToken = CancelToken()
    @State private var cancelRequested = false

    private let brandingPath = ProcessInfo.processInfo.environment["BRANDING_PNG"]
        ?? "/Users/LelandDutcher/Developer/SonyShotMarker/branding/cat sticking tongue out.png"

    private var canRun: Bool { !files.isEmpty && outputDir != nil && !running }

    var body: some View {
        ZStack(alignment: .bottomLeading) {
            LinearGradient(colors: [.white, Color(white: 0.90)], startPoint: .top, endPoint: .bottom)
                .overlay(RadialGradient(colors: [tonguePink.opacity(0.18), .clear],
                                        center: .topTrailing, startRadius: 4, endRadius: 460))
                .ignoresSafeArea()
            catView
            content
        }
        .frame(width: 640, height: 580)
        .preferredColorScheme(.light)        // the design is light; don't invert in Dark Mode
        .tint(tonguePink)
    }

    private func catImage() -> NSImage? {
        if let u = Bundle.main.url(forResource: "cat", withExtension: "png"), let img = NSImage(contentsOf: u) { return img }
        return NSImage(contentsOfFile: brandingPath)
    }

    private var catView: some View {
        Group {
            if let img = catImage() {
                Image(nsImage: img)
                    .resizable().scaledToFit()
                    .frame(width: 144)
                    .opacity(running ? 1.0 : 0.95)
                    .shadow(color: .black.opacity(0.12), radius: 8, y: 3)
                    .padding(.leading, 14).padding(.bottom, 14)
                    .allowsHitTesting(false)
            }
        }
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 4) {
                Text("SHOT MARK EMBEDDER")
                    .font(.system(size: 27, weight: .black, design: .rounded)).tracking(1.5)
                    .foregroundStyle(ink)
                Text("Drop already-offloaded Sony clips → a “footage embedded markers” folder for Premiere.")
                    .font(.callout).foregroundStyle(inkSoft)
            }

            dropZone

            HStack(spacing: 10) {
                Button { chooseOutput() } label: { Label("Output…", systemImage: "folder.fill") }
                    .buttonStyle(.borderedProminent)
                Text(outputDir?.path ?? "no output folder chosen")
                    .font(.caption).foregroundStyle(inkSoft).lineLimit(1).truncationMode(.middle)
            }

            if running || progress > 0 {
                VStack(alignment: .leading, spacing: 7) {
                    ProgressView(value: progress).tint(tonguePink)
                    Text(statusLine.isEmpty ? "…" : statusLine)
                        .font(.caption.monospaced()).foregroundStyle(ink)
                        .lineLimit(1).truncationMode(.middle)
                }
            }

            Spacer(minLength: 0)

            HStack {
                Spacer().frame(width: 150)
                Spacer()
                if running {
                    Button {
                        cancelRequested = true
                        cancelToken.cancel()
                        statusLine = "Cancelling…"
                    } label: {
                        Text("Cancel").font(.headline).padding(.horizontal, 18).padding(.vertical, 11)
                    }
                    .buttonStyle(.plain)
                    .background(Color(white: 0.86)).foregroundStyle(ink).clipShape(Capsule())
                    .disabled(cancelRequested)
                    .padding(.trailing, 8)
                }
                Button { run() } label: {
                    Text(running ? "Embedding…" : "Embed Markers")
                        .font(.headline).padding(.horizontal, 22).padding(.vertical, 11)
                }
                .buttonStyle(.plain)
                .background(canRun ? tonguePink : Color(white: 0.7))
                .foregroundStyle(.white).clipShape(Capsule())
                .shadow(color: canRun ? tonguePink.opacity(0.35) : .clear, radius: 6, y: 2)
                .disabled(!canRun).keyboardShortcut(.defaultAction)
            }
        }
        .padding(28)
    }

    private var dropZone: some View {
        RoundedRectangle(cornerRadius: 16)
            .fill(isTargeted ? tonguePink.opacity(0.10) : Color.white.opacity(0.6))
            .overlay(RoundedRectangle(cornerRadius: 16)
                .strokeBorder(style: StrokeStyle(lineWidth: 2, dash: [10]))
                .foregroundStyle(isTargeted ? tonguePink : hairline))
            .overlay(
                VStack(spacing: 8) {
                    Image(systemName: "tray.and.arrow.down.fill")
                        .font(.system(size: 30)).foregroundStyle(isTargeted ? tonguePink : inkSoft)
                    Text(files.isEmpty ? "Drag your Sony footage here"
                                       : "\(files.count) file\(files.count == 1 ? "" : "s") · " + fileSummary)
                        .font(.callout).foregroundStyle(isTargeted ? ink : inkSoft)
                        .multilineTextAlignment(.center).padding(.horizontal)
                })
            .frame(height: 148)
            .onDrop(of: [.fileURL], isTargeted: $isTargeted) { providers in add(providers); return true }
    }

    private var fileSummary: String {
        files.prefix(3).map(\.lastPathComponent).joined(separator: ", ") + (files.count > 3 ? " +\(files.count - 3)" : "")
    }

    // MARK: actions
    private func add(_ providers: [NSItemProvider]) {
        for p in providers {
            p.loadItem(forTypeIdentifier: UTType.fileURL.identifier) { item, _ in
                guard let data = item as? Data, let url = URL(dataRepresentation: data, relativeTo: nil) else { return }
                DispatchQueue.main.async { if !files.contains(url) { files.append(url) } }
            }
        }
    }

    private func chooseOutput() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.prompt = "Choose Output"
        if panel.runModal() == .OK { outputDir = panel.url }
    }

    private func human(_ n: Int64) -> String {
        n < 0 ? "an unknown amount" : ByteCountFormatter.string(fromByteCount: n, countStyle: .file)
    }

    private func run() {
        guard let out = outputDir else { return }
        let folder = out.appendingPathComponent("footage embedded markers")

        // Preflight gate (CoW-aware): verify sources readable, output writable + not the
        // original, and enough free space — before copying anything.
        let problems = Embedder.preflightProblems(src: files, dest: folder)
        if !problems.isEmpty {
            let a = NSAlert()
            a.alertStyle = .warning
            a.messageText = "Some checks failed before starting"
            a.informativeText = problems.map { "•  \($0)" }.joined(separator: "\n")
                + "\n\nFix these and try again, or embed anyway."
            a.addButton(withTitle: "Cancel")
            a.addButton(withTitle: "Embed Anyway")
            if a.runModal() == .alertFirstButtonReturn { return }   // Cancel
        }

        // Overwrite warning: copies from a previous run that would be replaced.
        let clobber = Embedder.existingOutputs(src: files, dest: folder)
        if !clobber.isEmpty {
            let a = NSAlert()
            a.alertStyle = .warning
            a.messageText = "Overwrite \(clobber.count) existing cop\(clobber.count == 1 ? "y" : "ies")?"
            a.informativeText = "“\(folder.lastPathComponent)” already contains: "
                + clobber.prefix(6).map(\.lastPathComponent).joined(separator: ", ")
                + (clobber.count > 6 ? " +\(clobber.count - 6)" : "")
                + ".\nEmbedding will replace them."
            a.addButton(withTitle: "Cancel")
            a.addButton(withTitle: "Overwrite")
            if a.runModal() == .alertFirstButtonReturn { return }   // Cancel
        }

        let space = Embedder.enoughSpace(src: files, dest: out)
        let queue = files
        let token = CancelToken()
        cancelToken = token
        cancelRequested = false
        running = true; progress = 0; statusLine = "Starting…"

        DispatchQueue.global(qos: .userInitiated).async {
            let total = queue.count
            var done = 0, embedded = 0, skipped = 0, failed = 0
            var log = RunLog.header(output: out, freeBytes: space.free, sameVolume: space.sameVolume)
            log.append("inputs (\(total)):")
            for f in queue {
                let sz = (try? f.resourceValues(forKeys: [.fileSizeKey]))?.fileSize ?? 0
                log.append("  \(f.lastPathComponent)  \(RunLog.human(Int64(sz)))")
            }
            log.append("results:")
            // size-weighted progress: advance the bar by bytes copied, not file count.
            let sizes: [Int64] = queue.map { (try? $0.resourceValues(forKeys: [.fileSizeKey]).fileSize).map(Int64.init) ?? 0 }
            let totalBytes = max(Int64(1), sizes.reduce(0, +))
            var bytesBefore: Int64 = 0
            let runStart = Date()

            // stall watchdog: if cross-volume bytes stop flowing for >30s, say so out loud.
            let mon = ProgressMonitor()
            let watchdog = DispatchSource.makeTimerSource(queue: DispatchQueue.global())
            watchdog.schedule(deadline: .now() + 5, repeating: 5)
            watchdog.setEventHandler {
                if let name = mon.stalledFile(after: 30) {
                    DispatchQueue.main.async { statusLine = "Still working on \(name) — the drive may be slow or disconnected…" }
                }
            }
            watchdog.resume()

            var cancelled = false
            for (idx, f) in queue.enumerated() {
                if token.isCancelled { cancelled = true; break }   // cancel between files
                let fileSize = sizes[idx]
                mon.begin(name: f.lastPathComponent)
                DispatchQueue.main.async { statusLine = "Embedding \(f.lastPathComponent)…  (\(done + 1)/\(total))" }
                var res = Embedder.embed(src: f, intoFolder: folder, onBytes: { copied, _ in
                    mon.touch()
                    let doneBytes = bytesBefore + copied
                    let elapsed = Date().timeIntervalSince(runStart)
                    let bps = elapsed > 0.5 ? Double(doneBytes) / elapsed : 0
                    let eta = bps > 0 ? Double(totalBytes - doneBytes) / bps : 0
                    let frac = min(1.0, Double(doneBytes) / Double(totalBytes))
                    DispatchQueue.main.async {
                        progress = frac
                        statusLine = throughputLine(name: f.lastPathComponent, doneBytes: doneBytes,
                                                    totalBytes: totalBytes, bps: bps, eta: eta)
                    }
                }, cancel: token)
                mon.end()
                if case .cancelled = res { cancelled = true; break }   // cancel mid-file
                // post-embed verify: re-open the copy; a copy that fails is deleted + failed.
                if case .embedded(let n, let dest) = res {
                    let v = Embedder.verifyEmbedded(dest, expected: n)
                    if !v.ok {
                        try? FileManager.default.removeItem(at: dest)
                        res = .failed("embedded but failed verify (\(v.detail))")
                    }
                }
                done += 1
                bytesBefore += fileSize
                let line: String
                switch res {
                case .embedded(let n, _): embedded += 1; line = "✓ \(f.lastPathComponent) — \(n) mark(s), verified  (\(done)/\(total))"
                case .skippedNoMarks:     skipped += 1; line = "– \(f.lastPathComponent): no Shot Marks  (\(done)/\(total))"
                case .notSony:            skipped += 1; line = "– \(f.lastPathComponent): not Sony  (\(done)/\(total))"
                case .failed(let e):      failed += 1; line = "✗ \(f.lastPathComponent): \(e)  (\(done)/\(total))"
                case .cancelled:          line = "⏹ \(f.lastPathComponent): cancelled  (\(done)/\(total))"
                }
                log.append("  " + line)
                let frac = Double(bytesBefore) / Double(totalBytes)
                DispatchQueue.main.async { progress = frac; statusLine = line }
            }
            watchdog.cancel()
            let tally = summaryTally(embedded: embedded, skipped: skipped, failed: failed)
            let leftover = total - done
            if cancelled {
                log.append("summary: CANCELLED — \(embedded)/\(total) embedded, \(leftover) not started   [\(tally)]")
            } else {
                log.append("summary: \(embedded)/\(total) embedded   [\(tally)]")
            }
            RunLog.write(lines: log)
            DispatchQueue.main.async {
                progress = 1; running = false; cancelRequested = false
                statusLine = cancelled
                    ? "Cancelled — \(embedded) embedded, \(leftover) not started."
                    : "Done — \(tally) into “footage embedded markers”."
            }
        }
    }

    private func summaryTally(embedded: Int, skipped: Int, failed: Int) -> String {
        var parts = ["✓\(embedded)"]
        if skipped > 0 { parts.append("–\(skipped)") }
        if failed > 0 { parts.append("✗\(failed)") }
        return parts.joined(separator: " · ")
    }
}

/// Thread-safe progress heartbeat: the worker stamps byte activity; the watchdog timer reads
/// it to decide whether a cross-volume copy has stalled.
final class ProgressMonitor {
    private let lock = NSLock()
    private var lastByteAt = Date()
    private var name = ""
    private var copying = false
    func begin(name: String) { lock.lock(); self.name = name; copying = true; lastByteAt = Date(); lock.unlock() }
    func touch() { lock.lock(); lastByteAt = Date(); lock.unlock() }
    func end() { lock.lock(); copying = false; lock.unlock() }
    func stalledFile(after seconds: TimeInterval) -> String? {
        lock.lock(); defer { lock.unlock() }
        return (copying && Date().timeIntervalSince(lastByteAt) > seconds) ? name : nil
    }
}

func throughputLine(name: String, doneBytes: Int64, totalBytes: Int64, bps: Double, eta: Double) -> String {
    let f = ByteCountFormatter(); f.countStyle = .file
    var s = "\(name)  ·  \(f.string(fromByteCount: doneBytes)) / \(f.string(fromByteCount: totalBytes))"
    if bps > 0 { s += "  ·  \(f.string(fromByteCount: Int64(bps)))/s" }
    if eta > 1 { s += "  ·  ~\(formatETA(eta))" }
    return s
}

func formatETA(_ s: Double) -> String {
    let sec = Int(s.rounded())
    if sec < 60 { return "\(sec)s" }
    let m = sec / 60, r = sec % 60
    return r == 0 ? "\(m)m" : "\(m)m \(r)s"
}
