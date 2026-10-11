import XCTest
@testable import MiraNetworking

final class OnboardingTests: XCTestCase {
    @MainActor
    func testPresentationPreferencesPersistWithoutChangingTheProfile() throws {
        let suite = "MiraPreferencesTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let draft = validDraft()
        defaults.set(try JSONEncoder().encode(draft), forKey: "onboardingDraft.v1")
        let state = AppState(defaults: defaults)
        XCTAssertEqual(state.onboarding, draft)
        XCTAssertEqual(state.preferences.voice, "Warm")
        state.preferences.voice = "Calm"
        state.preferences.volume = 0.4
        state.preferences.showCaptions = false
        state.preferences.contactRelationship = "Daughter"
        state.preferences.alertTherapist = true
        let restored = AppState(defaults: defaults)
        XCTAssertEqual(restored.preferences, state.preferences)
        XCTAssertEqual(restored.onboarding, draft)
        defaults.set(Data("corrupt".utf8), forKey: "miraPreferences.v1")
        let recovered = AppState(defaults: defaults)
        XCTAssertEqual(recovered.preferences, MiraPreferences())
        XCTAssertEqual(recovered.onboarding, draft)
    }

    @MainActor
    func testCombinedWelcomeAcknowledgesPrivacyAndPreservesDraft() throws {
        let suite = "MiraOnboardingTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let state = AppState(defaults: defaults)
        state.onboarding.heightCM = "170"
        state.acceptWelcomeAndPrivacy()
        XCTAssertTrue(state.onboarding.privacyAcknowledged)
        XCTAssertEqual(state.onboarding.step, .profile)
        XCTAssertFalse(state.hasCompletedOnboarding)
        XCTAssertEqual(state.onboarding.heightCM, "170")
        let restored = AppState(defaults: defaults)
        XCTAssertTrue(restored.onboarding.privacyAcknowledged)
        XCTAssertEqual(restored.onboarding.step, .profile)
        // A draft saved on the former standalone privacy step can also proceed.
        restored.onboarding.step = .privacy
        restored.acceptWelcomeAndPrivacy()
        XCTAssertEqual(restored.onboarding.step, .profile)
    }

    func testLegacyDraftWithoutNameStillDecodesAndKeepsStepIDs() throws {
        let encoded = try JSONEncoder().encode(validDraft())
        var json = try XCTUnwrap(JSONSerialization.jsonObject(with: encoded) as? [String: Any])
        json.removeValue(forKey: "patientName")
        let draft = try JSONDecoder().decode(OnboardingDraft.self, from: JSONSerialization.data(withJSONObject: json))
        XCTAssertNil(draft.patientName)
        XCTAssertEqual(draft.heightCM, "170.5")
        XCTAssertEqual(draft.affectedSide, .right)
        XCTAssertEqual(OnboardingStep.therapist.rawValue, 3)
        XCTAssertEqual(OnboardingStep.emergencyContact.rawValue, 5)
        XCTAssertEqual(OnboardingStep.profile.next, .emergencyContact)
        XCTAssertEqual(OnboardingStep.therapist.previous, .emergencyContact)
    }

    private func validDraft() -> OnboardingDraft {
        var draft = OnboardingDraft()
        draft.patientName = "Maya"
        draft.privacyAcknowledged = true
        draft.heightCM = "170.5"
        draft.bodyMassKG = "65,5"
        draft.affectedSide = .right
        draft.condition = "Knee recovery"
        draft.emergencyContactName = "Sample contact"
        draft.emergencyContactPhone = "+1 (312) 555-0100"
        return draft
    }

    func testValidationAndDecimalInput() {
        var draft = validDraft()
        XCTAssertTrue(draft.canFinish)
        XCTAssertEqual(draft.bodyMass, 65.5)
        for invalid in ["", "0", "-10", "nan", "inf", "301", "abc"] {
            draft.heightCM = invalid
            XCTAssertFalse(draft.profileIsValid, invalid)
        }
        draft = validDraft()
        draft.patientName = "  "
        XCTAssertFalse(draft.profileIsValid)
        draft = validDraft()
        draft.condition = "  \n "
        XCTAssertFalse(draft.profileIsValid)
        draft = validDraft()
        for invalid in ["", "555", "1234567890123456", "312CALLNOW", "312+5550100", "++13125550100"] {
            draft.emergencyContactPhone = invalid
            XCTAssertFalse(draft.contactIsValid, invalid)
        }
        draft = validDraft()
        draft.privacyAcknowledged = false
        XCTAssertFalse(draft.canFinish)
    }

    @MainActor
    func testResumeCompletionAndRevisitWithoutDataLoss() throws {
        let suite = "MiraOnboardingTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        defaults.set(true, forKey: "hasCompletedOnboarding")
        let state = AppState(defaults: defaults)
        XCTAssertFalse(state.hasCompletedOnboarding, "The old welcome-only completion must not bypass setup")
        state.advanceOnboarding()
        XCTAssertEqual(state.onboarding.step, .privacy)
        state.advanceOnboarding()
        XCTAssertEqual(state.onboarding.step, .privacy, "Privacy must be acknowledged")
        state.onboarding = validDraft()
        state.onboarding.step = .emergencyContact
        state.onboarding.therapistCode = "DRAFT-CODE"
        let resumed = AppState(defaults: defaults)
        XCTAssertEqual(resumed.onboarding, state.onboarding)
        resumed.advanceOnboarding()
        XCTAssertEqual(resumed.onboarding.step, .therapist)
        resumed.advanceOnboarding()
        XCTAssertEqual(resumed.onboarding.step, .device)
        resumed.advanceOnboarding()
        XCTAssertEqual(resumed.onboarding.step, .review)
        XCTAssertFalse(resumed.hasCompletedOnboarding)
        resumed.advanceOnboarding()
        XCTAssertTrue(resumed.hasCompletedOnboarding)
        let completed = AppState(defaults: defaults)
        XCTAssertTrue(completed.hasCompletedOnboarding)
        completed.revisitOnboarding()
        XCTAssertFalse(completed.hasCompletedOnboarding)
        XCTAssertEqual(completed.onboarding.step, .profile)
        XCTAssertEqual(completed.onboarding.emergencyContactName, "Sample contact")
        XCTAssertEqual(completed.onboarding.therapistCode, "DRAFT-CODE")
    }

    @MainActor
    func testInvalidReviewCannotComplete() throws {
        let suite = "MiraOnboardingTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let state = AppState(defaults: defaults)
        state.onboarding.step = .review
        state.advanceOnboarding()
        XCTAssertFalse(state.hasCompletedOnboarding)
        defaults.set(Data("invalid".utf8), forKey: "onboardingDraft.v1")
        defaults.set(true, forKey: "hasCompletedOnboarding.v2")
        let restored = AppState(defaults: defaults)
        XCTAssertEqual(restored.onboarding.step, .welcome)
        XCTAssertFalse(restored.hasCompletedOnboarding)
    }
}
