import SwiftUI

@main
struct MiraApp: App {
    @State private var state = AppState()

    var body: some Scene {
        WindowGroup {
            Group {
                if state.hasCompletedOnboarding {
                    TabView {
                        NavigationStack { HomeView() }
                            .tabItem { Label("Home", systemImage: "house") }
                        NavigationStack { ProgressViewScreen() }
                            .tabItem { Label("Progress", systemImage: "chart.xyaxis.line") }
                        NavigationStack { CareView() }
                            .tabItem { Label("Care", systemImage: "heart") }
                        NavigationStack { SettingsView() }
                            .tabItem { Label("Settings", systemImage: "gearshape") }
                    }
                } else {
                    OnboardingView()
                }
            }
            .environment(state)
            .tint(Theme.Mira.accent)
        }
    }
}
