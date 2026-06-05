// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "EmbedMarkers",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(name: "EmbedMarkers", path: "Sources/EmbedMarkers")
    ]
)
