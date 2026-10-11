#if DEBUG
import SwiftUI

// Opt-in launch routes use a separate defaults suite, never the user's profile
// or server settings. These Figma fixtures are not compiled into Release.
struct DesignPreviewHost: View {
    let route: String
    @State private var state: AppState
    @State private var tab = 0
    static let routes = ["welcome", "profile", "therapist", "device", "findmat", "tripods", "fixes", "body", "save", "home", "week", "live", "paused", "pain", "stop", "results", "flags", "send", "trends", "balance", "goals", "calendar", "care", "visit", "notes", "messages", "videos", "contact", "safety", "review", "settings", "rerun", "voice", "captions", "reminders", "privacy", "delete"]
    static var launchRoute: String? {
        let arguments = ProcessInfo.processInfo.arguments
        guard let index = arguments.firstIndex(of: "--design-preview"), arguments.indices.contains(index+1), routes.contains(arguments[index+1]) else { return nil }
        return arguments[index+1]
    }
    init(route: String) {
        self.route = route
        let defaults = UserDefaults(suiteName: "com.mira.design-preview")!
        defaults.removePersistentDomain(forName: "com.mira.design-preview")
        let state = AppState(defaults: defaults)
        state.onboarding.patientName = "Maya"
        state.onboarding.heightCM = "162.56"
        state.onboarding.bodyMassKG = "68.0389"
        state.onboarding.affectedSide = .right
        state.onboarding.condition = "Knee replacement"
        state.onboarding.therapistCode = "K7P4M2"
        state.onboarding.privacyAcknowledged = true
        state.onboarding.emergencyContactName = "Anna Ruiz"
        state.onboarding.emergencyContactPhone = "(555) 012-3456"
        state.preferences.contactRelationship = "Daughter"
        state.preferences.alertTherapist = true
        _tab = State(initialValue: ["trends", "balance", "goals", "calendar"].contains(route) ? 1 : ["care", "visit", "notes", "messages", "videos", "contact"].contains(route) ? 2 : ["settings", "rerun", "voice", "captions", "reminders", "privacy", "delete"].contains(route) ? 3 : 0)
        state.onboarding.step = ["profile": .profile, "therapist": .therapist, "device": .device, "review": .review, "safety": .emergencyContact][route] ?? .welcome
        state.hasCompletedOnboarding = !["welcome", "profile", "therapist", "device", "safety", "review"].contains(route)
        _state = State(initialValue: state)
    }
    var body: some View {
        Group {
            if !state.hasCompletedOnboarding { OnboardingView() }
            else if let step = ["findmat", "tripods", "fixes", "body", "save"].firstIndex(of: route) {
                NavigationStack { SetupView(initialStep: step) }
            } else if let screen = LiveView.Screen(rawValue: route) {
                NavigationStack { LiveView(initialScreen: screen) }
            } else if let screen = SummaryView.Screen(rawValue: route) {
                NavigationStack { SummaryView(initialScreen: screen) }
            } else {
                TabView(selection: $tab) {
                    NavigationStack {
                        if route == "week" { WeekView() } else { HomeView() }
                    }.tag(0).tabItem { Label { Text("Home") } icon: { Image(FigmaAssets.name("home", "imgIconHome")).renderingMode(.template) } }
                    NavigationStack { ProgressViewScreen(initialSection: ["balance": "Balance", "goals": "Goals", "calendar": "Calendar"][route] ?? "Exercises") }.tag(1).tabItem { Label { Text("Progress") } icon: { Image(FigmaAssets.name("home", "imgIconProgress")).renderingMode(.template) } }
                    NavigationStack { if let screen = CareDetailView.Screen(rawValue: route) { CareDetailView(screen: screen) } else { CareView() } }.tag(2).tabItem { Label { Text("Care") } icon: { Image(FigmaAssets.name("home", "imgIconCare")).renderingMode(.template) } }
                    NavigationStack { if let screen = SettingsDetailView.Screen(rawValue: route) { SettingsDetailView(screen: screen) } else { SettingsView() } }.tag(3).tabItem { Label { Text("Settings") } icon: { Image(FigmaAssets.name("home", "imgIconSettings")).renderingMode(.template) } }
                }
            }
        }.environment(state).environment(\.miraDesignPreview, true).tint(MiraUI.charcoal).preferredColorScheme(.light)
    }
}
#endif
