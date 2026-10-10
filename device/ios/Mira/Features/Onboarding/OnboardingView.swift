import SwiftUI

struct OnboardingView: View {
    @Environment(AppState.self) private var state

    var body: some View {
        VStack(spacing: Theme.Mira.spacing) {
            Image(systemName: "sparkles")
                .font(.largeTitle)
                .foregroundStyle(Theme.Mira.accent)
                .accessibilityHidden(true)
            Text("Welcome to Mira").font(.largeTitle.bold())
            Text("Your space to get set up, follow your progress, and stay connected.")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            Button("Get started") { state.hasCompletedOnboarding = true }
                .buttonStyle(.borderedProminent)
        }
        .padding(Theme.Mira.spacing)
    }
}
