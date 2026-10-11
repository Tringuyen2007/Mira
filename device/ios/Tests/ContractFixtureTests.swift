import XCTest
@testable import MiraNetworking

// Decodes the server's own sample payloads (Tests/Fixtures = copy of server/samples).
// A server change that breaks the app fails here once the samples are refreshed.
final class ContractFixtureTests: XCTestCase {
    private func fixture(_ name: String) throws -> Data {
        let url = try XCTUnwrap(Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Fixtures"),
                                "Missing fixture \(name).json")
        return try Data(contentsOf: url)
    }

    private func decode<T: Decodable>(_ type: T.Type, _ name: String) throws -> T {
        try WireJSON.decoder().decode(type, from: fixture(name))
    }

    func testFixturesMatchServerSamples() throws {
        // Tests/ → ios/ → device/ → repo root.
        let samples = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .appendingPathComponent("../../../server/samples").standardizedFileURL
        guard FileManager.default.fileExists(atPath: samples.path) else {
            throw XCTSkip("server/samples not found next to the iOS package")
        }
        let names = try FileManager.default.contentsOfDirectory(atPath: samples.path).filter { $0.hasSuffix(".json") }
        XCTAssertFalse(names.isEmpty)
        for name in names {
            let server = try Data(contentsOf: samples.appendingPathComponent(name))
            let copy = try fixture(String(name.dropLast(5)))
            XCTAssertEqual(server, copy, "\(name) drifted: cp server/samples/*.json device/ios/Tests/Fixtures/")
        }
    }

    func testStreams() throws {
        let live = try decode(LiveMessage.self, "ws_live")
        XCTAssertEqual(live.exerciseId, "squat")
        XCTAssertEqual(live.repsTarget, 10)
        XCTAssertEqual(live.setsTarget, 3)
        XCTAssertNotNil(live.mainAngle)
        let pose = try decode(PoseMessage.self, "ws_pose")
        XCTAssertTrue(pose.hasValidDimensions)
        XCTAssertNotNil(pose.loadLPct)
        XCTAssertTrue(WireJSON.isPing(try fixture("ws_ping")))
        XCTAssertFalse(WireJSON.isPing(try fixture("ws_events_cue")))
    }

    func testEveryServerEvent() throws {
        func event(_ name: String) throws -> SocketEvent { try decode(SocketEvent.self, name) }
        guard case .arrived(let arrived) = try event("ws_events_arrived") else { return XCTFail("arrived") }
        XCTAssertEqual(arrived.patientId, "maya")
        guard case .greeting(let greeting) = try event("ws_events_greeting") else { return XCTFail("greeting") }
        XCTAssertNotNil(greeting.audioUrl)
        XCTAssertFalse(greeting.text.isEmpty)
        guard case .cue(let cue) = try event("ws_events_cue") else { return XCTFail("cue") }
        XCTAssertEqual(cue.audioUrl, "/audio/set_start.mp3")
        guard case .alert(let alert) = try event("ws_events_alert") else { return XCTFail("alert") }
        XCTAssertEqual(alert.kind, "fall")
        XCTAssertEqual(alert.step, "escalated")
        XCTAssertEqual(alert.showCall911, true)
        XCTAssertNotNil(alert.message)
        XCTAssertTrue(alert.isOpen)
        guard case .fall(let fall) = try event("ws_events_fall") else { return XCTFail("fall") }
        XCTAssertEqual(fall.stage, "confirmed")
        guard case .fallStatus(let status) = try event("ws_events_fall_status") else { return XCTFail("fall_status") }
        XCTAssertEqual(status.moving, false)
        guard case .summaryReady(let summary) = try event("ws_events_summary_ready") else { return XCTFail("summary") }
        XCTAssertEqual(summary.set, 1)
        guard case .coachAdapt(let adapt) = try event("ws_events_coach_adapt") else { return XCTFail("coach_adapt") }
        XCTAssertEqual(adapt.action, "adjust_next_set")
        guard case .proposalCreated(let created) = try event("ws_events_proposal_created") else { return XCTFail("proposal") }
        XCTAssertEqual(created.proposalId, 1)
        guard case .proposalDecided(let decided) = try event("ws_events_proposal_decided") else { return XCTFail("decided") }
        XCTAssertEqual(decided.prescriptionVersion, 2)
        guard case .countdown(let countdown) = try event("ws_events_countdown") else { return XCTFail("countdown") }
        XCTAssertEqual(countdown.n, 1)
        guard case .rep(let rep) = try event("ws_events_rep") else { return XCTFail("rep") }
        XCTAssertEqual(rep.quality, "fix_form")
        guard case .setDone(let done) = try event("ws_events_set_done") else { return XCTFail("set_done") }
        XCTAssertEqual(done.reps, 10)
        guard case .resumed = try event("ws_events_resumed") else { return XCTFail("resumed") }
        guard case .paused = try event("ws_events_paused") else { return XCTFail("paused") }
        guard case .haptic = try event("ws_events_haptic") else { return XCTFail("haptic") }
        guard case .gesture = try event("ws_events_gesture") else { return XCTFail("gesture") }
        guard case .awaitingGesture = try event("ws_events_awaiting_gesture") else { return XCTFail("awaiting") }
        guard case .fatigue = try event("ws_events_fatigue") else { return XCTFail("fatigue") }
        guard case .hr = try event("ws_events_hr") else { return XCTFail("hr") }
        guard case .deviceHealth(let health) = try event("ws_events_device_health") else { return XCTFail("health") }
        XCTAssertEqual(health.cameras, 2)
        guard case .ping = try event("ws_ping") else { return XCTFail("ping") }
    }

    /// What the app sends must be in the server's allow-list and match its samples.
    func testClientMessagesMatchServerSamples() throws {
        let cases: [(String, SocketEvent)] = [
            ("ws_events_client_command", .command(CommandMessage(name: .pause))),
            ("ws_events_client_hr", .hr(HeartRateMessage(t: 1791662259.706, bpm: 97))),
            ("ws_events_client_wrist", .wrist(WristMessage(t: 1791662259.706, quat: [0.99, 0.01, 0.05, 0.02], accel: [0.02, -9.79, 0.15]))),
            ("ws_events_client_watch_button", .watchButton(WatchButtonMessage(name: "ready"))),
            ("ws_events_client_device_health", .deviceHealth(DeviceHealthMessage(t: 1791662259.706, batteryPct: 81, app: "watch"))),
        ]
        for (name, event) in cases {
            XCTAssertTrue(SocketEvent.clientTypes.contains(event.type), name)
            let sent = try JSONDecoder().decode(JSONValue.self, from: WireJSON.encoder().encode(event))
            XCTAssertEqual(sent, try JSONDecoder().decode(JSONValue.self, from: fixture(name)), name)
        }
    }

    func testRestResponses() throws {
        XCTAssertEqual(try decode(HealthStatus.self, "rest_get_health").status, "ok")
        XCTAssertFalse(try decode(CalibrationStatus.self, "rest_get_calibration_status").calibrated)
        XCTAssertEqual(try decode(SessionStarted.self, "rest_post_sessions").firstName, "Maya")
        XCTAssertTrue(try decode(SessionStopped.self, "rest_post_session_stop").stopped)
        XCTAssertTrue(try decode(SessionShared.self, "rest_post_session_share").shared)
        let detail = try decode(SessionDetail.self, "rest_get_session")
        XCTAssertFalse(detail.reps.isEmpty)
        XCTAssertEqual(detail.summary?.repsTotal, 4)
        XCTAssertEqual(try decode(SessionFrames.self, "rest_get_session_frames_lttb").points?.isEmpty, false)
        let full = try decode(SessionFrames.self, "rest_get_session_frames_full")
        XCTAssertEqual(full.mode, "full")
        XCTAssertEqual(full.frames?.first?.joints?.count, 33)
        XCTAssertFalse(try decode(PatientProgress.self, "rest_get_patient_progress").series.isEmpty)
        XCTAssertFalse(try decode(PrescriptionList.self, "rest_get_prescriptions").prescriptions.isEmpty)
        XCTAssertNotNil(try decode(ProposalDecision.self, "rest_post_proposal_approve").prescription)
        XCTAssertEqual(try decode(ProposalList.self, "rest_get_proposals").proposals.first?.status, "pending")
        let alerts = try decode(AlertList.self, "rest_get_alerts").alerts
        XCTAssertEqual(alerts.first?.steps.count, 3)
        XCTAssertEqual(try decode(AlertResolution.self, "rest_post_alert_resolve").step, "resolved_by_therapist")
        XCTAssertEqual(try decode(NotificationCount.self, "rest_get_notifications_count").urgentUnacked, 1)
        let answer = try decode(CopilotResponse.self, "rest_post_copilot_ask")
        XCTAssertFalse(answer.sql.isEmpty)
        XCTAssertFalse(answer.charts.isEmpty)
        let paired = try decode(PairResponse.self, "rest_post_pair")
        XCTAssertEqual(paired.patientId, "maya")
        XCTAssertTrue(paired.token.hasPrefix("pt_"))
        let me = try decode(MeResponse.self, "rest_get_me")
        XCTAssertEqual(me.therapist?.name, "Dr. Okafor")
        XCTAssertEqual(me.prescriptions.first?.exerciseName.isEmpty, false)
        XCTAssertNotNil(me.lcd)
    }
}
