import SwiftUI

struct SettingsView: View {
    @Environment(AppState.self) private var state

    var body: some View {
        Form {
            Section("About") { LabeledContent("App", value: "Mira") }
            Section {
                Button("Show onboarding") { state.hasCompletedOnboarding = false }
            }
        }
        .navigationTitle("Settings")
    }
}
