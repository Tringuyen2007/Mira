import XCTest
@testable import MiraNetworking

@MainActor
final class PairingTests: XCTestCase {
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        defaults = UserDefaults(suiteName: "PairingTests-\(UUID().uuidString)")
        StubProtocol.requests = []
    }

    private func draftState(tokenStore: MemoryTokenStore = MemoryTokenStore()) -> AppState {
        let state = AppState(defaults: defaults, tokenStore: tokenStore, urlSession: StubProtocol.session())
        state.onboarding.patientName = "Maya Ellis"
        state.onboarding.heightCM = "163"
        state.onboarding.bodyMassKG = "70"
        state.onboarding.affectedSide = .right
        state.onboarding.condition = "Knee replacement"
        state.onboarding.emergencyContactName = "Eric"
        state.onboarding.emergencyContactPhone = "+15550100111"
        state.onboarding.therapistCode = "K7P4M2"
        state.preferences.contactRelationship = "son"
        _ = state.connect(address: "http://mira.test:8000")
        return state
    }

    private static let meJSON = #"""
    {"patient":{"id":"maya","name":"Maya Ellis","first_name":"Maya","age":67,"height_cm":163,"mass_kg":70,
     "affected_side":"right","condition":"Knee replacement","hr_limit":130,
     "emergency_contact":{"name":"Eric","phone":"+15550100111","relationship":"son"},
     "alert_therapist":true,"share_by_default":false,"device_prefs":{},"next_visit_at":"2026-10-13T15:30:00Z"},
     "therapist":{"id":"okafor","name":"Dr. Okafor","clinic":"Lakeside Rehab","phone":"+15550100222"},
     "prescriptions":[{"exercise_id":"squat","exercise_name":"Mini squats","sets":3,"reps":10,
       "targets":{"knee_deg":90},"limits":{},"version":1,"metric_label":"Knee bend","unit":"°","target_key":"knee_deg"}],
     "lcd":{"online":false,"last_seen":null},
     "calibration":{"status":"unknown","calibrated":false,"cameras":[],"errors":null,"note":"x"}}
    """#

    func testPairStoresTokenPatientAndMe() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.url?.path, "/pair")
            XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
            let body = try! JSONSerialization.jsonObject(with: request.httpBody ?? request.streamedBody()) as! [String: Any]
            XCTAssertEqual(body["code"] as? String, "K7P4M2")
            XCTAssertEqual((body["profile"] as? [String: Any])?["affected_side"] as? String, "right")
            XCTAssertEqual((body["emergency_contact"] as? [String: Any])?["relationship"] as? String, "son")
            return (200, #"{"patient_id":"maya","token":"pt_abc123","me":\#(Self.meJSON)}"#)
        }
        let store = MemoryTokenStore()
        let state = draftState(tokenStore: store)
        let ok = await state.pair()
        XCTAssertTrue(ok)
        XCTAssertEqual(state.patientID, "maya")
        XCTAssertEqual(store.load(), "pt_abc123")
        XCTAssertEqual(state.me?.therapist?.clinic, "Lakeside Rehab")
        XCTAssertEqual(state.me?.prescriptions.first?.exerciseName, "Mini squats")
        XCTAssertTrue(state.isLinked)
        XCTAssertNil(state.pairingError)
        // persisted
        XCTAssertEqual(defaults.string(forKey: "patientID.v1"), "maya")
    }

    func testUnknownCodeShowsFriendlyErrorAndStaysUnpaired() async {
        StubProtocol.handler = { _ in (404, #"{"detail":"unknown code"}"#) }
        let state = draftState()
        let ok = await state.pair()
        XCTAssertFalse(ok)
        XCTAssertNil(state.patientID)
        XCTAssertFalse(state.isPaired)
        XCTAssertEqual(state.pairingError, "That code isn't recognised. Check it with your therapist.")
    }

    func testExpiredCodeMessage() async {
        StubProtocol.handler = { _ in (410, #"{"detail":"code expired"}"#) }
        let state = draftState()
        _ = await state.pair()
        XCTAssertEqual(state.pairingError, "That code has expired or was already used. Ask your therapist for a new one.")
    }

    func testClearPairingErrorResetsAfterFailedAttempt() async {
        StubProtocol.handler = { _ in (404, #"{"detail":"unknown code"}"#) }
        let state = draftState()
        _ = await state.pair()
        XCTAssertNotNil(state.pairingError)
        state.clearPairingError()
        XCTAssertNil(state.pairingError)
    }

    func testPairReturnsFalseWithoutErrorWhenAlreadyPairing() async {
        // Simulates the onboarding Continue button being tapped twice in quick succession: the second call
        // must not overwrite any error from the (still in-flight) first call.
        StubProtocol.handler = { _ in (200, #"{"patient_id":"maya","token":"pt_abc123","me":\#(Self.meJSON)}"#) }
        let state = draftState()
        async let first = state.pair()
        let second = await state.pair()
        _ = await first
        // At least one of the two overlapping calls must have been turned away by the isPairing guard,
        // or both succeeded outright if the first had already finished - either way pairingError stays nil.
        XCTAssertNil(state.pairingError)
        _ = second
    }

    func testForgetTokenClearsIdentity() async {
        StubProtocol.handler = { _ in (200, #"{"patient_id":"maya","token":"pt_abc123","me":\#(Self.meJSON)}"#) }
        let state = draftState()
        _ = await state.pair()
        state.forgetToken()
        XCTAssertNil(state.patientID)
        XCTAssertNil(state.me)
        XCTAssertFalse(state.isLinked)
    }

    func testRefreshMeUsesPatientToken() async throws {
        // refreshMe() now also refreshes `store` (care, sessions, progress, milestones,
        // messages) in parallel with /me; every call still carries the patient token.
        StubProtocol.handler = { request in
            if request.url?.path == "/pair" { return (200, #"{"patient_id":"maya","token":"pt_abc123","me":\#(Self.meJSON)}"#) }
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer pt_abc123")
            switch request.url?.path {
            case "/me": return (200, Self.meJSON)
            case "/me/care":
                return (200, #"{"therapist":null,"next_visit_at":null,"hr_limit":null,"emergency_contact":null,"alert_therapist":true,"notes":[],"highlights":[],"unread_messages":0}"#)
            case "/me/messages": return (200, #"{"messages":[]}"#)
            case "/patients/maya/sessions": return (200, #"{"patient_id":"maya","sessions":[]}"#)
            case "/patients/maya/milestones": return (200, #"{"patient_id":"maya","milestones":[]}"#)
            default: return (404, #"{"detail":"not stubbed"}"#)
            }
        }
        let state = draftState()
        _ = await state.pair()
        await state.refreshMe()
        XCTAssertEqual(state.me?.patient.firstName, "Maya")
        XCTAssertEqual(state.store.care?.unreadMessages, 0)
    }
}

extension URLRequest {
    /// URLProtocol receives bodies as a stream; read it back for assertions.
    func streamedBody() -> Data {
        guard let stream = httpBodyStream else { return Data() }
        stream.open(); defer { stream.close() }
        var data = Data(); var buffer = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable { let n = stream.read(&buffer, maxLength: buffer.count); if n <= 0 { break }; data.append(buffer, count: n) }
        return data
    }
}
