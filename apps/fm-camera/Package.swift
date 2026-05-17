// swift-tools-version:6.0
//
// Tiny macOS binary that grabs one JPEG from a named camera device and
// writes it to disk. Wrapped into a code-signed .app bundle by build.sh
// so it gets its own TCC entry — Python can then subprocess-call it to
// snapshot without inheriting the parent process's camera sandbox.
//
// Build:
//   $ ./apps/fm-camera/build.sh
//
// Usage:
//   $ apps/fm-camera/FruitMarketCamera.app/Contents/MacOS/fm-camera \
//       "HD Pro Webcam C920" /tmp/snapshot.jpg
//
// First invocation will prompt for camera access; the grant is stored
// against the .app bundle, not against the calling process.
import PackageDescription

let package = Package(
    name: "fm-camera",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "fm-camera",
            path: "Sources/fm-camera",
            swiftSettings: [
                // Swift 6 strict concurrency would force us into
                // MainActor.assumeIsolated dances around the top-level
                // entry point. Pinning to the Swift 5 language mode
                // keeps the UI bootstrap idiomatic.
                .swiftLanguageMode(.v5)
            ]
        )
    ]
)
