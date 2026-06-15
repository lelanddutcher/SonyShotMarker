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
        let folder = URL(fileURLWithPath: outArg).appendingPathComponent("footage embedded markers")
        let files = args.dropFirst().map { URL(fileURLWithPath: $0) }
        var ok = 0
        for f in files {
            switch Embedder.embed(src: f, intoFolder: folder) {
            case .embedded(let n, let d): ok += 1; print("✓ \(f.lastPathComponent): \(n) mark(s) → \(d.path)")
            case .skippedNoMarks:         print("– \(f.lastPathComponent): no Shot Marks")
            case .notSony:                print("– \(f.lastPathComponent): not Sony")
            case .failed(let e):          print("✗ \(f.lastPathComponent): \(e)")
            }
        }
        print("\(ok)/\(files.count) embedded → \(folder.path)")
    }
}

struct ContentView: View {
    @State private var files: [URL] = []
    @State private var outputDir: URL?
    @State private var isTargeted = false
    @State private var running = false
    @State private var progress: Double = 0
    @State private var statusLine = ""

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

    private func run() {
        guard let out = outputDir else { return }
        let folder = out.appendingPathComponent("footage embedded markers")
        let queue = files
        running = true; progress = 0; statusLine = "Starting…"
        DispatchQueue.global(qos: .userInitiated).async {
            let total = queue.count
            var done = 0, embedded = 0
            for f in queue {
                DispatchQueue.main.async { statusLine = "Embedding \(f.lastPathComponent)…  (\(done + 1)/\(total))" }
                let res = Embedder.embed(src: f, intoFolder: folder)
                done += 1
                let frac = Double(done) / Double(total)
                let line: String
                switch res {
                case .embedded(let n, _): embedded += 1; line = "✓ \(f.lastPathComponent) — \(n) mark(s)  (\(done)/\(total))"
                case .skippedNoMarks:     line = "– \(f.lastPathComponent): no Shot Marks  (\(done)/\(total))"
                case .notSony:            line = "– \(f.lastPathComponent): not Sony  (\(done)/\(total))"
                case .failed(let e):      line = "✗ \(f.lastPathComponent): \(e)  (\(done)/\(total))"
                }
                DispatchQueue.main.async { progress = frac; statusLine = line }
            }
            DispatchQueue.main.async {
                progress = 1; running = false
                statusLine = "Done — \(embedded)/\(total) embedded into “footage embedded markers”."
            }
        }
    }
}
