import XCTest
@testable import MiraNetworking

// Session end: session_summary / device_status on /ws/events, the analysis on
// GET /sessions/{id}, the patient session list, and the check-in response.
final class SessionFlowTests: XCTestCase {
    private func fixture(_ name: String) throws -> Data {
        let url = try XCTUnwrap(Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Fixtures"),
                                "Missing fixture \(name).json")
        return try Data(contentsOf: url)
    }

    private func decode<T: Decodable>(_ type: T.Type, _ name: String) throws -> T {
        try WireJSON.decoder().decode(type, from: fixture(name))
    }

    func testSessionSummaryEventDecodes() throws {
        guard case .sessionSummary(let summary) = try decode(SocketEvent.self, "ws_events_session_summary") else {
            return XCTFail("expected session_summary")
        }
        XCTAssertEqual(summary.sessionId, "54bdaf2b-4097-483e-b429-998d6a1af011")
        XCTAssertEqual(summary.exerciseId, "squat")
        XCTAssertEqual(summary.exerciseName, "Mini squats")
        XCTAssertEqual(summary.formScore, 100)
        XCTAssertEqual(summary.repsTotal, 4)
        XCTAssertEqual(summary.goodReps, 4)
        XCTAssertEqual(summary.setsDone, 1)
        XCTAssertFalse(summary.spoken.isEmpty)
        XCTAssertNotNil(summary.note)
        XCTAssertFalse(summary.highlights.isEmpty)
        XCTAssertEqual(summary.highlights.first?.kind, "consistency")
        XCTAssertNil(summary.nextFocus)
        XCTAssertTrue(summary.flaggedMoments.isEmpty)
        XCTAssertNotNil(summary.comparison)
        XCTAssertEqual(summary.source, "ai")
    }

    func testDeviceStatusEventDecodes() throws {
        guard case .deviceStatus(let status) = try decode(SocketEvent.self, "ws_events_device_status") else {
            return XCTFail("expected device_status")
        }
        XCTAssertEqual(status.device, "lcd")
        XCTAssertTrue(status.online)
    }

    func testSessionDetailHasAnalysisAndExerciseName() throws {
        let detail = try decode(SessionDetail.self, "rest_get_session")
        XCTAssertEqual(detail.exerciseName, "Mini squats")
        XCTAssertNotNil(detail.analysis)
        XCTAssertFalse(detail.analysis?.highlights.isEmpty ?? true)
        XCTAssertEqual(detail.analysis?.formScore, 100)
        XCTAssertEqual(detail.analysis?.spoken.isEmpty, false)
    }

    func testPatientSessionListDecodes() throws {
        let list = try decode(SessionList.self, "rest_get_patient_sessions")
        XCTAssertEqual(list.patientId, "maya")
        XCTAssertEqual(list.sessions.first?.formScore, 100)
        XCTAssertEqual(list.sessions.first?.goodReps, 4)
        XCTAssertEqual(list.sessions.first?.repsTotal, 4)
        XCTAssertEqual(list.sessions.first?.spokenPreview?.isEmpty, false)
    }

    func testCheckinResponseDecodes() throws {
        let response = try decode(CheckinResponse.self, "rest_post_session_checkin")
        XCTAssertEqual(response.sessionId, "54bdaf2b-4097-483e-b429-998d6a1af011")
        XCTAssertEqual(response.painPost, 3)
        XCTAssertEqual(response.effort, 6)
        XCTAssertTrue(response.noteSaved)
    }

    @MainActor
    func testLiveSessionStoreTracksSessionSummaryAndCurrentSessionID() throws {
        let store = LiveSessionStore()
        store.eventSocket.onMessage?(try fixture("ws_events_session_summary"))
        XCTAssertNotNil(store.sessionSummary)
        XCTAssertEqual(store.currentSessionID, "54bdaf2b-4097-483e-b429-998d6a1af011")
        XCTAssertEqual(store.cue, store.sessionSummary?.spoken)
    }

    @MainActor
    func testLiveSessionStoreTracksDeviceStatus() throws {
        let store = LiveSessionStore()
        store.eventSocket.onMessage?(try fixture("ws_events_device_status"))
        XCTAssertEqual(store.lcdOnline, true)
    }

    @MainActor
    func testResetClearsSessionSummaryAndCurrentSessionID() throws {
        let store = LiveSessionStore()
        store.eventSocket.onMessage?(try fixture("ws_events_session_summary"))
        XCTAssertNotNil(store.sessionSummary)
        store.connect(to: try ServerAddress("http://mira.local:8000"), token: nil, reset: true)
        XCTAssertNil(store.sessionSummary)
        XCTAssertNil(store.currentSessionID)
    }
}
