// swift-tools-version: 5.9
import PackageDescription

// Tests the same networking sources used by the iOS app, without a simulator.
let package = Package(
    name: "MiraNetworking",
    platforms: [.macOS(.v14), .iOS(.v17)],
    products: [.library(name: "MiraNetworking", targets: ["MiraNetworking"])],
    targets: [
        .target(name: "MiraNetworking", path: "Mira", exclude: [
            "App/MiraApp.swift", "Design", "Features", "Watch", "Info.plist", "Assets.xcassets"
        ], sources: ["Net", "App/AppState.swift", "App/PatientStore.swift"]),
        // Fixtures/ is a copy of server/samples/*.json (the server's contract);
        // ContractFixtureTests fails when the copy drifts from the server's files.
        .testTarget(name: "MiraNetworkingTests", dependencies: ["MiraNetworking"], path: "Tests",
                    resources: [.copy("Fixtures")]),
        // Watch state machine + maths (Foundation-only), shared with the MiraWatch Xcode target.
        .target(name: "MiraWatchCore", path: "MiraWatch/Core"),
        .testTarget(name: "MiraWatchCoreTests", dependencies: ["MiraWatchCore"], path: "WatchCoreTests"),
    ]
)
