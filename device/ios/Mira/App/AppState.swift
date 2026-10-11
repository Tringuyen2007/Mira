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

#if !canImport(WatchConnectivity)
/// Stand-in for the macOS `swift test` build (`MiraNetworking` excludes `Mira/Watch`,
/// and plain macOS has no `WatchConnectivity`), with the same shape `AppState` calls.
@MainActor
final class WatchBridge {
    private(set) var isPaired = false
    private(set) var isWatchAppInstalled = false

    init(liveSession: LiveSessionStore) {}
    func activate() {}
    func push(_ live: LiveMessage?) {}
    func forward(haptic pattern: String) {}
    func forward(summary: SessionSummaryMessage) {}
}
#endif

@MainActor
@Observable
final class AppState {
    var preferences: MiraPreferences {
        didSet {
            if let data = try? JSONEncoder().encode(preferences) { defaults.set(data, forKey: "miraPreferences.v1") }
            notifyPreferencesChanged()
        }
    }

    /// Set by `MiraApp` on iOS to `{ prefs in Task { await Reminders.sync(prefs) } }`.
    /// Kept as a closure (rather than importing `Reminders` here) so this file keeps
    /// building in the macOS `swift test` package, which has no UserNotifications.
    @ObservationIgnored var onPreferencesChanged: ((MiraPreferences) -> Void)?
    @ObservationIgnored private var preferencesChangeTask: Task<Void, Never>?

    /// Debounces rapid preference edits (e.g. dragging the volume slider or tapping
    /// several reminder days) before syncing to local notifications.
    private func notifyPreferencesChanged() {
        guard let onPreferencesChanged else { return }
        preferencesChangeTask?.cancel()
        let prefs = preferences
        preferencesChangeTask = Task {
            try? await Task.sleep(for: .milliseconds(600))
            guard !Task.isCancelled else { return }
            onPreferencesChanged(prefs)
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
    let store = PatientStore()
    let watch: WatchBridge
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
    @ObservationIgnored private let tokenStore: TokenStore
    /// The server's MIRA_API_TOKEN, from the Keychain. Never logged or shown in full.
    @ObservationIgnored private var token: String?
    private(set) var tokenHint: String?

    /// The server's patient id for this phone, set by a successful /pair. UserDefaults `patientID.v1`.
    private(set) var patientID: String? { didSet { defaults.set(patientID, forKey: "patientID.v1") } }
    private(set) var me: MeResponse?
    private(set) var pairingError: String?
    private(set) var isPairing = false
    @ObservationIgnored private let urlSession: URLSession

    var isPaired: Bool { token != nil }
    var isLinked: Bool { patientID != nil && isPaired }
    /// Whether an Apple Watch is paired with this iPhone and has the Mira watch app
    /// installed (not whether it's currently reachable — the toggle should stay on
    /// even while the watch is locked or out of Bluetooth range).
    var watchPaired: Bool { watch.isPaired && watch.isWatchAppInstalled }

    static let demoServerAddress = "http://45.76.255.253:8000"
    private static var defaultServerAddress: String {
        #if MIRA_DEMO
        return demoServerAddress
        #else
        return "http://localhost:8000"
        #endif
    }

    init(defaults: UserDefaults = .standard, tokenStore: TokenStore = KeychainTokenStore(), urlSession: URLSession = .shared) {
        self.defaults = defaults
        self.tokenStore = tokenStore
        self.urlSession = urlSession
        preferences = defaults.data(forKey: "miraPreferences.v1")
            .flatMap { try? JSONDecoder().decode(MiraPreferences.self, from: $0) } ?? MiraPreferences()
        let draft = defaults.data(forKey: "onboardingDraft.v1")
            .flatMap { try? JSONDecoder().decode(OnboardingDraft.self, from: $0) } ?? OnboardingDraft()
        onboarding = draft
        // The old welcome-only screen must not count as a completed profile.
        hasCompletedOnboarding = defaults.bool(forKey: "hasCompletedOnboarding.v2") && draft.canFinish
        serverAddress = defaults.string(forKey: "serverAddress") ?? Self.defaultServerAddress
        connectionEnabled = defaults.bool(forKey: "connectionEnabled")
        token = tokenStore.load()
        tokenHint = token.map(PairingToken.masked)
        // Note: patientID's didSet isn't called from init, which is what we want.
        patientID = defaults.string(forKey: "patientID.v1")
        watch = WatchBridge(liveSession: liveSession)
        liveSession.onMessageEvent = { [store] event in store.appendIncoming(event) }
        liveSession.onSessionSummary = { [weak self] _ in Task { await self?.refreshMe() } }
        #if canImport(WatchConnectivity)
        watch.activate()
        #endif
        trackLiveForWatch()
        trackHapticForWatch()
        trackSummaryForWatch()
    }

    // MARK: Watch relay (Plan 4)

    /// Re-arming `withObservationTracking` loops: each tracks exactly one
    /// `liveSession` property, so a `live` update never re-sends the last haptic
    /// (and vice versa). `AppState` itself stays the single place that knows both
    /// "what changed" and "the watch cares" — `LiveSessionStore` doesn't know
    /// `WatchBridge` exists, and `WatchBridge` doesn't know when to look.
    private func trackLiveForWatch() {
        withObservationTracking({ _ = liveSession.live }, onChange: { [weak self] in
            Task { @MainActor in
                guard let self else { return }
                self.watch.push(self.liveSession.live)
                self.trackLiveForWatch()
            }
        })
    }

    private func trackHapticForWatch() {
        withObservationTracking({ _ = liveSession.hapticSeq }, onChange: { [weak self] in
            Task { @MainActor in
                guard let self else { return }
                if let haptic = self.liveSession.haptic { self.watch.forward(haptic: haptic.pattern) }
                self.trackHapticForWatch()
            }
        })
    }

    private func trackSummaryForWatch() {
        withObservationTracking({ _ = liveSession.sessionSummary }, onChange: { [weak self] in
            Task { @MainActor in
                guard let self else { return }
                if let summary = self.liveSession.sessionSummary { self.watch.forward(summary: summary) }
                self.trackSummaryForWatch()
            }
        })
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
        return APIClient(address: address, token: token, session: urlSession)
    }

    /// Saves the address and, when one is entered, a new token (an empty token field
    /// keeps the saved one). Without any token the app shows "not paired" instead of
    /// opening sockets the server would refuse.
    @discardableResult
    func connect(address text: String, token tokenText: String = "") -> Bool {
        do {
            let address = try ServerAddress(text)
            let newToken = PairingToken.normalized(tokenText)
            if let newToken {
                try tokenStore.save(newToken)
                token = newToken
                tokenHint = PairingToken.masked(newToken)
            }
            let changed = address.url.absoluteString != serverAddress || newToken != nil
            serverAddress = address.url.absoluteString
            defaults.set(serverAddress, forKey: "serverAddress")
            connectionEnabled = true
            defaults.set(true, forKey: "connectionEnabled")
            configurationVersion = UUID()
            restStatus = nil
            configurationError = token == nil ? APIError.notPaired.localizedDescription : nil
            if foreground { liveSession.connect(to: address, token: token, reset: changed) }
            return true
        } catch {
            configurationError = error.localizedDescription
            return false
        }
    }

    func forgetToken() {
        tokenStore.delete()
        token = nil
        tokenHint = nil
        patientID = nil
        me = nil
        pairingError = nil
        configurationVersion = UUID()
        restStatus = nil
        if connectionEnabled, let address = try? ServerAddress(serverAddress) {
            liveSession.connect(to: address, token: nil, reset: true)
        }
    }

    /// After a confirmed `DELETE /me`: forgets the pairing token and every local
    /// record of this patient, so the app falls back to onboarding. `serverAddress`
    /// is kept — deleting your data doesn't mean re-typing the clinic's server.
    func wipeLocalData() {
        forgetToken()
        onboarding = OnboardingDraft()
        preferences = MiraPreferences()
        hasCompletedOnboarding = false
        me = nil
        store.reset()
        // The property `didSet`s above re-persist the fresh defaults; remove the
        // keys afterward so a deleted account truly leaves nothing behind.
        defaults.removeObject(forKey: "onboardingDraft.v1")
        defaults.removeObject(forKey: "miraPreferences.v1")
        defaults.removeObject(forKey: "hasCompletedOnboarding.v2")
        defaults.removeObject(forKey: "patientID.v1")
    }

    /// Builds the /pair request from the onboarding draft and preferences.
    func pairRequest(deviceName: String) -> PairRequest? {
        let d = onboarding
        guard d.therapistCode.count == 6, let height = d.height, let mass = d.bodyMass,
              let name = d.patientName?.trimmingCharacters(in: .whitespacesAndNewlines), !name.isEmpty else { return nil }
        let side: String? = switch d.affectedSide {
        case .left: "left"
        case .right: "right"
        case .notApplicable: "none"
        case .both, .notSelected: nil
        }
        let relationship = preferences.contactRelationship.trimmingCharacters(in: .whitespacesAndNewlines)
        let contact = d.contactIsValid ? PairContact(name: d.emergencyContactName, phone: d.emergencyContactPhone,
                                                     relationship: relationship.isEmpty ? nil : relationship) : nil
        return PairRequest(code: d.therapistCode, deviceName: deviceName,
                           profile: PairProfile(name: name, heightCm: height, massKg: mass, affectedSide: side,
                                                condition: d.condition.isEmpty ? nil : d.condition),
                           emergencyContact: contact, alertTherapist: preferences.alertTherapist)
    }

    /// Clears a stale pairing error, e.g. once the user edits the therapist code after a failed attempt.
    func clearPairingError() {
        pairingError = nil
    }

    /// POST /pair with the draft; on success the patient token replaces any saved token and sockets reconnect.
    @discardableResult
    func pair(deviceName: String = "iPhone") async -> Bool {
        guard !isPairing else { return false }
        guard let body = pairRequest(deviceName: deviceName) else {
            pairingError = "Enter your name, height, weight and the 6-character code first."
            return false
        }
        guard let address = try? ServerAddress(serverAddress) else {
            pairingError = APIError.invalidAddress.localizedDescription
            return false
        }
        isPairing = true
        pairingError = nil
        defer { isPairing = false }
        do {
            let response = try await APIClient(address: address, token: nil, session: urlSession).pair(body)
            try tokenStore.save(response.token)
            token = response.token
            tokenHint = PairingToken.masked(response.token)
            patientID = response.patientId
            me = response.me
            configurationVersion = UUID()
            configurationError = nil
            connectionEnabled = true
            defaults.set(true, forKey: "connectionEnabled")
            if foreground { liveSession.connect(to: address, token: token, reset: true) }
            return true
        } catch {
            pairingError = PairingError.message(for: error)
            return false
        }
    }

    /// Refreshes `me` plus everything `store` holds (care, sessions, progress,
    /// milestones, messages), in parallel. Kept as `refreshMe` so existing call
    /// sites (foreground, after pairing, Settings' "Refresh" button) don't change.
    func refreshMe() async {
        guard let patientID, let apiClient else { return }
        await store.refreshAll(api: apiClient, patientID: patientID)
        if let fresh = store.me {
            me = fresh
            configurationError = nil
        } else {
            // `store`'s own /me call failed; a single direct retry tells us whether
            // it's an auth problem (token rejected) worth surfacing, or just offline
            // (where we keep the cached `me`; Home shows the offline banner from the
            // socket state).
            do { _ = try await apiClient.me() } catch APIError.unauthorized {
                configurationError = APIError.unauthorized.localizedDescription
            } catch {}
        }
    }

    /// PUT /me (profile/emergency-contact/alert-therapist patch), then refreshes
    /// `me` and `store.me` so the Care/Settings screens show the saved values.
    func updateMe(_ body: MeUpdate) async throws {
        _ = try await requireClient().updateMe(body)
        await refreshMe()
    }

    /// POST /me/test-alert.
    func sendTestAlert() async throws -> TestAlertResponse {
        try await requireClient().testAlert()
    }

    // MARK: Sessions (Plan 2)

    /// The session this phone started over REST; covers the gap before /ws/events
    /// stamps `liveSession.currentSessionID`.
    private(set) var startedSessionID: String?

    private func requireClient() throws -> APIClient {
        guard let apiClient else { throw APIError.invalidAddress }
        return apiClient
    }

    /// POST /sessions; the exercise defaults to the first prescription, else "squat".
    @discardableResult
    func startSession(exerciseID: String?) async throws -> SessionStarted {
        let exercise = exerciseID ?? me?.prescriptions.first?.exerciseId ?? "squat"
        let started = try await requireClient().createSession(SessionRequest(patientId: patientID, exerciseId: exercise))
        startedSessionID = started.sessionId
        return started
    }

    func stopSession(_ id: String) async throws {
        _ = try await requireClient().stopSession(id: id)
        if startedSessionID == id { startedSessionID = nil }
    }

    func loadSession(_ id: String) async throws -> SessionDetail {
        try await requireClient().session(id: id)
    }

    /// The most recent ended session for this patient, if any.
    func latestEndedSessionID() async throws -> String? {
        guard let patientID else { return nil }
        let list = try await requireClient().sessions(patientID: patientID, limit: 10)
        return list.sessions.filter { $0.endedAt != nil }.max { $0.startedAt < $1.startedAt }?.id
    }

    func checkin(_ id: String, pain: Int, effort: Int, note: String) async throws {
        let trimmed = note.trimmingCharacters(in: .whitespacesAndNewlines)
        _ = try await requireClient().checkin(sessionID: id, CheckinRequest(painPost: pain, effort: effort, note: trimmed.isEmpty ? nil : trimmed))
    }

    func share(_ id: String) async throws {
        _ = try await requireClient().shareSession(id: id)
    }

    /// Only a server-relative `/audio/<name>.mp3` path is accepted; anything else (an
    /// absolute URL, a path outside /audio/, unexpected characters) returns nil so the
    /// caller falls back to TTS instead of opening an arbitrary or malformed URL.
    private static let audioPathPattern = try! NSRegularExpression(pattern: #"^/audio/[A-Za-z0-9._-]+\.mp3$"#)

    func serverURL(for path: String) -> URL? {
        let range = NSRange(path.startIndex..<path.endIndex, in: path)
        guard Self.audioPathPattern.firstMatch(in: path, range: range) != nil else { return nil }
        guard let address = try? ServerAddress(serverAddress) else { return nil }
        return URL(string: path, relativeTo: address.url)?.absoluteURL
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
            liveSession.connect(to: address, token: token)
        } else if !active {
            liveSession.disconnect()
        }
        if active, patientID != nil { Task { await refreshMe() } }
    }

    func testREST() async {
        guard !isTestingREST, let apiClient else { return }
        let version = configurationVersion
        isTestingREST = true
        restStatus = nil
        defer { isTestingREST = false }
        do {
            // Reachability first (public), then a call that needs the token.
            _ = try await apiClient.health()
            _ = try await apiClient.calibrationStatus()
            guard version == configurationVersion else { return }
            restStatus = "REST OK · server online, token accepted"
        } catch APIError.unauthorized {
            guard version == configurationVersion else { return }
            restStatus = "Token rejected (HTTP 401) · check the token in Settings"
        } catch APIError.notPaired {
            guard version == configurationVersion else { return }
            restStatus = "Server online · not paired (no token saved)"
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

enum PairingError {
    static func message(for error: Error) -> String {
        switch error {
        case APIError.http(404, _): return "That code isn't recognised. Check it with your therapist."
        case APIError.http(410, _): return "That code has expired or was already used. Ask your therapist for a new one."
        case APIError.http(429, _): return "Too many attempts. Wait a minute and try again."
        case APIError.http(422, _): return "Mira couldn't read your details. Check your name, height and weight."
        case APIError.http(503, _), APIError.notConnected: return "Mira's server is offline right now. You can save your code and try again later."
        case let api as APIError: return api.localizedDescription
        default: return "Mira's server is offline right now. You can save your code and try again later."
        }
    }
}
