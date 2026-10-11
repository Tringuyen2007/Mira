import SwiftUI

/// The Mira Watch App: setup, Ready/Stop, live coaching and rotation screens from the Figma "AppleWatch Hi-FI"
/// board. It talks only to the paired iPhone over WatchConnectivity (`WatchWire` dictionaries); the phone relays
/// everything to and from the Mira server. `RootView` activates the link (or a DEBUG preview scenario).
@main
struct MiraWatchApp: App {
    @State private var model = WatchSessionModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
        }
    }
}
