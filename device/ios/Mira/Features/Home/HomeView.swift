import SwiftUI

struct HomeView: View {
    @Environment(AppState.self) private var state
    var body: some View {
        List {
            if state.connectionEnabled && state.liveSession.connectionState != .connected {
                Section {
                    Text("Mira is unavailable right now.")
                        .foregroundStyle(.secondary)
                }
            }
            Section("Get started") {
                NavigationLink("Set up your space") { SetupView() }
                NavigationLink("Live session") { LiveView() }
            }
            Section("Reflect") {
                NavigationLink("Session summary") { SummaryView() }
            }
        }
        .navigationTitle("Mira")
    }
}
