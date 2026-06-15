// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "EmbedMarkers",
    platforms: [.macOS(.v13)],
    dependencies: [
        // Sparkle 2.x — free, open-source macOS auto-update (EdDSA-signed appcast on GitHub).
        .package(url: "https://github.com/sparkle-project/Sparkle", from: "2.6.0"),
    ],
    targets: [
        .executableTarget(
            name: "EmbedMarkers",
            dependencies: [.product(name: "Sparkle", package: "Sparkle")],
            path: "Sources/EmbedMarkers"
        )
    ]
)
