import SwiftUI

@main
struct MiraApp: App {
    @State private var state = AppState()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            Group {
                #if DEBUG
                if let route = DesignPreviewHost.launchRoute {
                    DesignPreviewHost(route: route)
                } else { userInterface }
                #else
                userInterface
                #endif
            }
            .environment(state)
            .tint(Theme.Mira.accent)
            .task {
                // Wires local-notification syncing in at launch — Settings also sets
                // this (first open) via the same `== nil` guard, but a reminder that
                // was already configured must still fire without the patient ever
                // opening Settings again.
                if state.onPreferencesChanged == nil {
                    state.onPreferencesChanged = { prefs in Task { await Reminders.sync(prefs) } }
                }
                if !isDesignPreview { state.setForeground(scenePhase == .active) }
            }
            .onChange(of: scenePhase) { _, phase in
                guard !isDesignPreview else { return }
                if phase == .active { state.setForeground(true) }
                else if phase == .background { state.setForeground(false) }
            }
        }
    }

    private var isDesignPreview: Bool {
        #if DEBUG
        DesignPreviewHost.launchRoute != nil
        #else
        false
        #endif
    }

    private var userInterface: some View {
        Group {
                if state.hasCompletedOnboarding {
                    TabView {
                        NavigationStack { HomeView() }
                            .tabItem { Label { Text("Home") } icon: { Image(FigmaAssets.name("home", "imgIconHome")).renderingMode(.template) } }
                        NavigationStack { ProgressViewScreen() }
                            .tabItem { Label { Text("Progress") } icon: { Image(FigmaAssets.name("home", "imgIconProgress")).renderingMode(.template) } }
                        NavigationStack { CareView() }
                            .tabItem { Label { Text("Care") } icon: { Image(FigmaAssets.name("home", "imgIconCare")).renderingMode(.template) } }
                        NavigationStack { SettingsView() }
                            .tabItem { Label { Text("Settings") } icon: { Image(FigmaAssets.name("home", "imgIconSettings")).renderingMode(.template) } }
                    }
                } else {
                    OnboardingView()
                }
            }
    }
}
