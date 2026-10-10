import SwiftUI

@main
struct MiraApp: App {
    @State private var state = AppState()
    @Environment(\.scenePhase) private var scenePhase

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
            .task { state.setForeground(scenePhase == .active) }
            .onChange(of: scenePhase) { _, phase in
                if phase == .active { state.setForeground(true) }
                else if phase == .background { state.setForeground(false) }
            }
        }
    }
}
