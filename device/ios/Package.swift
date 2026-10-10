// swift-tools-version: 5.9
import PackageDescription

// Tests the same networking sources used by the iOS app, without a simulator.
let package = Package(
    name: "MiraNetworking",
    platforms: [.macOS(.v14), .iOS(.v17)],
    products: [.library(name: "MiraNetworking", targets: ["MiraNetworking"])],
    targets: [
        .target(name: "MiraNetworking", path: "Mira", exclude: [
            "App/MiraApp.swift", "Design", "Features", "Watch", "Info.plist"
        ], sources: ["Net", "App/AppState.swift"]),
        .testTarget(name: "MiraNetworkingTests", dependencies: ["MiraNetworking"], path: "Tests")
    ]
)
