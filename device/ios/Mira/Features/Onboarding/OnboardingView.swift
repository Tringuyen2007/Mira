import SwiftUI

struct OnboardingView: View {
    @Environment(AppState.self) private var state
    @Environment(\.miraDesignPreview) private var preview

    @State private var returningToReview = false

    var body: some View {
        NavigationStack {
            Group {
                if isWelcome {
                    OnboardingWelcomeView(onContinue: state.acceptWelcomeAndPrivacy)
                } else if state.onboarding.step == .review {
                    OnboardingReviewView { step in returningToReview = true; state.onboarding.step = step }
                } else if state.onboarding.step == .emergencyContact {
                    EmergencyContactView(onboarding: true)
                } else if isDesignedStep {
                    OnboardingDesignedSteps()
                } else { EmptyView() }
            }
            .scrollDismissesKeyboard(.interactively)
            .navigationTitle(isWelcome || isDesignedStep ? "" : state.onboarding.step.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar(isWelcome ? .hidden : .visible, for: .navigationBar)
            .toolbar {
                if !isWelcome {
                    ToolbarItem(placement: .topBarLeading) {
                        Button("Back", systemImage: "chevron.left") {
                            if returningToReview { returningToReview = false; state.onboarding.step = .review }
                            else if let previous = state.onboarding.step.previous {
                                state.onboarding.step = previous
                            }
                        }
                    }
                }
                if isDesignedStep {
                    ToolbarItem(placement: .topBarTrailing) {
                        Text(headerLabel)
                            .font(MiraUI.font(17, "Medium")).foregroundStyle(MiraUI.soft)
                            .padding(.horizontal, 18).padding(.vertical, 10).background(.white.opacity(0.8), in: Capsule())
                    }
                }

            }
            .safeAreaInset(edge: .bottom) {
                if !isWelcome && state.onboarding.step != .review {
                    MiraButton(title: continueTitle, action: continueTapped)
                    .disabled(!state.onboarding.canContinue || state.isPairing)
                    .accessibilityIdentifier("onboardingContinue")
                    .padding(.horizontal, 32).padding(.vertical, 16)

                }
            }
        }
    }

    private var canPairNow: Bool { state.onboarding.therapistCode.count == 6 && !state.isLinked }

    private func continueTapped() {
        if returningToReview { returningToReview = false; state.onboarding.step = .review; return }
        // Once a pairing attempt has failed, the button reads "Set up later": tapping it again must advance
        // without retrying the same pair() call, not trap the user on this step.
        if state.onboarding.step == .therapist && canPairNow && state.pairingError == nil {
            Task {
                if await state.pair() { state.advanceOnboarding() }
                // on failure the step shows state.pairingError and the button reads "Set up later"
            }
            return
        }
        state.advanceOnboarding()
    }

    private var isDesignedStep: Bool { [.profile, .therapist, .device, .emergencyContact, .review].contains(state.onboarding.step) }

    private var isWelcome: Bool {
        state.onboarding.step == .welcome || state.onboarding.step == .privacy
    }

    private var headerLabel: String {
        switch state.onboarding.step {
        case .review: return "Review"
        default: return "Step \(state.onboarding.step.presentedNumber) of \(OnboardingStep.presentedCount)"
        }
    }

    private var continueTitle: String {
        if returningToReview { return "Save changes" }
        switch state.onboarding.step {
        case .welcome: return "Get started"
        case .therapist:
            if state.isLinked { return "Continue" }
            return canPairNow && state.pairingError == nil ? "Link therapist" : "Set up later"
        case .device: return state.isLinked ? "Continue" : (preview ? "Connect Mira" : "Set up later")
        case .review: return "Yes, this is right"
        default: return "Continue"
        }
    }

}
