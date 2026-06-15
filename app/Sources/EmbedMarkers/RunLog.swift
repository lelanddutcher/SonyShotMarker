// RunLog.swift — per-run diagnostic log + "Report a Problem". Mac counterpart of tools/run_log.py.
import Foundation
import AppKit

enum RunLog {
    static let appVersion = "0.2.0"

    static var dir: URL {
        if let override = ProcessInfo.processInfo.environment["SHOTMARK_LOG_DIR"], !override.isEmpty {
            return URL(fileURLWithPath: override, isDirectory: true)
        }
        let lib = FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library")
        return lib.appendingPathComponent("Logs/ShotMarkEmbedder", isDirectory: true)
    }

    static func human(_ n: Int64) -> String {
        n < 0 ? "unknown" : ByteCountFormatter.string(fromByteCount: n, countStyle: .file)
    }

    static func header(output: URL, freeBytes: Int64, sameVolume: Bool) -> [String] {
        let v = ProcessInfo.processInfo.operatingSystemVersion
        return [
            "Shot Mark Embedder — run log",
            "when: \(ISO8601DateFormatter().string(from: Date()))",
            "app: \(appVersion)  os: macOS \(v.majorVersion).\(v.minorVersion).\(v.patchVersion)",
            "output: \(output.path)",
            "dest free: \(human(freeBytes))  same-volume clone: \(sameVolume)",
        ]
    }

    @discardableResult
    static func write(lines: [String], keep: Int = 30) -> URL? {
        let d = dir
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        let fmt = DateFormatter()
        fmt.locale = Locale(identifier: "en_US_POSIX")
        fmt.dateFormat = "yyyy-MM-dd_HH-mm-ss"
        let base = "run-\(fmt.string(from: Date()))"
        var url = d.appendingPathComponent(base + ".log")
        var n = 2
        while FileManager.default.fileExists(atPath: url.path) {
            url = d.appendingPathComponent("\(base)-\(n).log"); n += 1
        }
        do { try (lines.joined(separator: "\n") + "\n").write(to: url, atomically: true, encoding: .utf8) }
        catch { return nil }
        prune(keep: keep)
        return url
    }

    static func latest() -> URL? { logFiles().max { mtime($0) < mtime($1) } }

    private static func logFiles() -> [URL] {
        ((try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.contentModificationDateKey])) ?? [])
            .filter { $0.lastPathComponent.hasPrefix("run-") && $0.pathExtension == "log" }
    }
    private static func mtime(_ u: URL) -> Date {
        (try? u.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
    }
    private static func prune(keep: Int) {
        let files = logFiles().sorted { mtime($0) < mtime($1) }
        guard files.count > keep else { return }
        for u in files.prefix(files.count - keep) { try? FileManager.default.removeItem(at: u) }
    }

    /// Open a pre-filled problem-report email with the latest log attached; fall back to revealing it.
    static func reportProblem() {
        let log = latest()
        if let svc = NSSharingService(named: .composeEmail) {
            svc.recipients = ["leland@lelanddutcher.com"]
            svc.subject = "Shot Mark Embedder — problem report (v\(appVersion))"
            var items: [Any] = ["Describe what happened:\n\n\n\n— The most recent run log is attached. —\n"]
            if let log { items.append(log) }
            if svc.canPerform(withItems: items) { svc.perform(withItems: items); return }
        }
        NSWorkspace.shared.activateFileViewerSelecting([log ?? dir])
    }
}
