import Foundation
import Observation

enum OnboardingStep: Int, Codable, CaseIterable {
    case welcome, privacy, profile, therapist, device, emergencyContact, review

    // Keep persisted raw values stable; welcome and privacy now share a screen.
    // Presentation order is independent of the persisted enum values.
    static let flow: [OnboardingStep] = [.welcome, .privacy, .profile, .emergencyContact, .therapist, .device, .review]
    var next: OnboardingStep? { Self.flow.drop(while: { $0 != self }).dropFirst().first }
    var previous: OnboardingStep? {
        guard let index = Self.flow.firstIndex(of: self), index > 0 else { return nil }
        return Self.flow[index - 1]
    }
    var presentedNumber: Int {
        switch self {
        case .welcome, .privacy: return 1
        case .profile: return 2
        case .emergencyContact: return 3
        case .therapist: return 4
        case .device, .review: return 5
        }
    }
    static var presentedCount: Int { 5 }

    var title: String {
        switch self {
        case .welcome: return "Welcome to Mira"
        case .privacy: return "Your privacy"
        case .profile: return "About you"
        case .therapist: return "Your therapist"
        case .device: return "Connect Mira"
        case .emergencyContact: return "Your emergency contact"
        case .review: return "Review your setup"
        }
    }
}

enum AffectedSide: String, Codable, CaseIterable, Identifiable {
    case notSelected, left, right, both, notApplicable
    var id: String { rawValue }
    var label: String {
        switch self {
        case .notSelected: return "Choose a side"
        case .left: return "Left"
        case .right: return "Right"
        case .both: return "Both"
        case .notApplicable: return "Not applicable"
        }
    }
}

// Local draft only. A therapist code is not a patient ID, and saving the draft
// must never imply a successful server link, device pairing, or uploaded profile.
struct OnboardingDraft: Codable, Equatable {
    var patientName: String? = nil // Optional decoding preserves drafts saved before the name field existed.
    var step: OnboardingStep = .welcome
    var privacyAcknowledged = false
    var heightCM = ""
    var bodyMassKG = ""
    var affectedSide: AffectedSide = .notSelected
    var condition = ""
    var therapistCode = ""
    var emergencyContactName = ""
    var emergencyContactPhone = ""

    private func decimal(_ value: String) -> Double? {
        Double(value.trimmingCharacters(in: .whitespacesAndNewlines).replacingOccurrences(of: ",", with: "."))
    }

    var height: Double? { decimal(heightCM) }
    var bodyMass: Double? { decimal(bodyMassKG) }
    var profileIsValid: Bool {
        guard let height, height.isFinite, height > 0, height <= 300,
              let bodyMass, bodyMass.isFinite, bodyMass > 0, bodyMass <= 500 else { return false }
        return !(patientName ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !condition.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
    var contactIsValid: Bool {
        let phone = emergencyContactPhone.trimmingCharacters(in: .whitespacesAndNewlines)
        let allowed = CharacterSet(charactersIn: "+0123456789 ()-.")
        let digits = phone.filter { $0.isASCII && $0.isNumber }
        return !emergencyContactName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && (7...15).contains(digits.count)
            && phone.unicodeScalars.allSatisfy { allowed.contains($0) }
            && phone.filter { $0 == "+" }.count <= 1
            && (!phone.contains("+") || phone.hasPrefix("+"))
    }
    var canFinish: Bool { privacyAcknowledged && profileIsValid && contactIsValid }
    var canContinue: Bool {
        switch step {
        case .privacy: return privacyAcknowledged
        case .profile: return profileIsValid
        case .emergencyContact: return contactIsValid
        case .review: return canFinish
        default: return true
        }
    }
}

@MainActor
@Observable
final class AppState {
    var preferences: MiraPreferences {
        didSet {
            if let data = try? JSONEncoder().encode(preferences) { defaults.set(data, forKey: "miraPreferences.v1") }
        }
    }
    var onboarding: OnboardingDraft {
        didSet {
            if let data = try? JSONEncoder().encode(onboarding) {
                defaults.set(data, forKey: "onboardingDraft.v1")
            }
        }
    }
    let liveSession = LiveSessionStore()
    private(set) var serverAddress: String
    private(set) var connectionEnabled: Bool
    private(set) var restStatus: String?
    private(set) var isTestingREST = false
    private(set) var configurationError: String?
    @ObservationIgnored private var foreground = true
    @ObservationIgnored private var configurationVersion = UUID()

    var hasCompletedOnboarding: Bool {
        didSet { defaults.set(hasCompletedOnboarding, forKey: "hasCompletedOnboarding.v2") }
    }

    @ObservationIgnored private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        preferences = defaults.data(forKey: "miraPreferences.v1")
            .flatMap { try? JSONDecoder().decode(MiraPreferences.self, from: $0) } ?? MiraPreferences()
        let draft = defaults.data(forKey: "onboardingDraft.v1")
            .flatMap { try? JSONDecoder().decode(OnboardingDraft.self, from: $0) } ?? OnboardingDraft()
        onboarding = draft
        // The old welcome-only screen must not count as a completed profile.
        hasCompletedOnboarding = defaults.bool(forKey: "hasCompletedOnboarding.v2") && draft.canFinish
        serverAddress = defaults.string(forKey: "serverAddress") ?? "http://localhost:8000"
        connectionEnabled = defaults.bool(forKey: "connectionEnabled")
    }

    func advanceOnboarding() {
        guard onboarding.canContinue else { return }
        if onboarding.step == .review {
            hasCompletedOnboarding = onboarding.canFinish
        } else if let next = onboarding.step.next {
            onboarding.step = next
        }
    }

    func acceptWelcomeAndPrivacy() {
        onboarding.privacyAcknowledged = true
        onboarding.step = .profile
    }

    func revisitOnboarding(at step: OnboardingStep = .profile) {
        onboarding.step = step
        hasCompletedOnboarding = false
    }

    var apiClient: APIClient? {
        guard let address = try? ServerAddress(serverAddress) else { return nil }
        return APIClient(address: address)
    }

    @discardableResult
    func connect(address text: String) -> Bool {
        do {
            let address = try ServerAddress(text)
            let changed = address.url.absoluteString != serverAddress
            serverAddress = address.url.absoluteString
            defaults.set(serverAddress, forKey: "serverAddress")
            connectionEnabled = true
            defaults.set(true, forKey: "connectionEnabled")
            configurationVersion = UUID()
            restStatus = nil
            configurationError = nil
            if foreground { liveSession.connect(to: address, reset: changed) }
            return true
        } catch {
            configurationError = error.localizedDescription
            return false
        }
    }

    func disconnect() {
        connectionEnabled = false
        defaults.set(false, forKey: "connectionEnabled")
        configurationVersion = UUID()
        liveSession.disconnect()
    }

    func setForeground(_ active: Bool) {
        foreground = active
        if active, connectionEnabled, let address = try? ServerAddress(serverAddress) {
            liveSession.connect(to: address)
        } else if !active {
            liveSession.disconnect()
        }
    }

    func testREST() async {
        guard !isTestingREST, let apiClient else { return }
        let version = configurationVersion
        isTestingREST = true
        restStatus = nil
        defer { isTestingREST = false }
        do {
            _ = try await apiClient.calibrationStatus()
            guard version == configurationVersion else { return }
            restStatus = "REST OK · calibration status received"
        } catch {
            guard version == configurationVersion else { return }
            restStatus = "REST failed · \(error.localizedDescription)"
        }
    }
}

// Local presentation preferences. Hardware and server settings require a sync contract.
struct MiraPreferences: Codable, Equatable {
    var voice = "Warm"
    var volume = 0.7
    var slowerSpeech = true
    var showCaptions = true
    var deviceCaptions = true
    var extraLargeCaptions = true
    var contactRelationship = ""
    var alertTherapist = false
    var dailyReminder = false
    var reminderHour = 9
    var reminderMinute = 0
    var reminderDays = [2, 3, 4, 5, 6, 7]
    var phoneReminder = true
    var deviceReminder = false
    var watchReminder = false
}
