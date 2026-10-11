import SwiftUI

/// The Mira Watch App: a wrist remote for a live Mira session (Plan 4). It talks
/// only to the paired iPhone over WatchConnectivity (`WatchWire` dictionaries);
/// the phone relays everything to and from the Mira server.
@main
struct MiraWatchApp: App {
    @State private var model = WatchSessionModel()

    var body: some Scene {
        WindowGroup {
            WatchLiveView()
                .environment(model)
                .task { model.activate() }
        }
    }
}
