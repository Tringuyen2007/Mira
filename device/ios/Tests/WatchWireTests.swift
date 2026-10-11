import XCTest
@testable import MiraNetworking

final class WatchWireTests: XCTestCase {
    // MARK: ToWatch round-trips

    func testSnapshotRoundTrip() throws {
        let snapshot = WatchWire.LiveSnapshot(
            phase: "active", rep: 3, repsTarget: 10, set: 1, setsTarget: 3,
            exerciseName: "Squat", hr: 112, paused: false
        )
        let encoded = WatchWire.encode(.snapshot(snapshot))
        XCTAssertEqual(encoded["type"] as? String, "snapshot")
        let decoded = try XCTUnwrap(WatchWire.decodeToWatch(encoded))
        XCTAssertEqual(decoded, .snapshot(snapshot))
    }

    func testSnapshotRoundTripWithNilOptionals() throws {
        let snapshot = WatchWire.LiveSnapshot(
            phase: "idle", rep: 0, repsTarget: nil, set: nil, setsTarget: nil,
            exerciseName: nil, hr: nil, paused: true
        )
        let encoded = WatchWire.encode(.snapshot(snapshot))
        XCTAssertNil(encoded["repsTarget"])
        XCTAssertNil(encoded["exerciseName"])
        let decoded = try XCTUnwrap(WatchWire.decodeToWatch(encoded))
        XCTAssertEqual(decoded, .snapshot(snapshot))
    }

    func testHapticRoundTrip() throws {
        let message = WatchWire.ToWatch.haptic(pattern: "good_rep")
        let decoded = try XCTUnwrap(WatchWire.decodeToWatch(WatchWire.encode(message)))
        XCTAssertEqual(decoded, message)
    }

    func testSummaryRoundTrip() throws {
        let message = WatchWire.ToWatch.summary(formScore: 82, goodReps: 9, repsTotal: 10)
        let decoded = try XCTUnwrap(WatchWire.decodeToWatch(WatchWire.encode(message)))
        XCTAssertEqual(decoded, message)
    }

    func testSummaryRoundTripWithNilFields() throws {
        let message = WatchWire.ToWatch.summary(formScore: nil, goodReps: nil, repsTotal: nil)
        let encoded = WatchWire.encode(message)
        XCTAssertNil(encoded["formScore"])
        let decoded = try XCTUnwrap(WatchWire.decodeToWatch(encoded))
        XCTAssertEqual(decoded, message)
    }

    // MARK: ToPhone round-trips

    func testHeartRateRoundTrip() throws {
        let message = WatchWire.ToPhone.heartRate(bpm: 118.5, t: 42.0)
        let decoded = try XCTUnwrap(WatchWire.decodeToPhone(WatchWire.encode(message)))
        XCTAssertEqual(decoded, message)
    }

    func testButtonRoundTrip() throws {
        let message = WatchWire.ToPhone.button(name: "ready")
        let decoded = try XCTUnwrap(WatchWire.decodeToPhone(WatchWire.encode(message)))
        XCTAssertEqual(decoded, message)
    }

    func testStatusRoundTrip() throws {
        let message = WatchWire.ToPhone.status(workoutActive: true)
        let decoded = try XCTUnwrap(WatchWire.decodeToPhone(WatchWire.encode(message)))
        XCTAssertEqual(decoded, message)
    }

    // MARK: Unknown / malformed

    func testUnknownTypeDecodesToNilForWatch() {
        XCTAssertNil(WatchWire.decodeToWatch(["type": "mystery"]))
        XCTAssertNil(WatchWire.decodeToWatch([:]))
    }

    func testUnknownTypeDecodesToNilForPhone() {
        XCTAssertNil(WatchWire.decodeToPhone(["type": "mystery"]))
        XCTAssertNil(WatchWire.decodeToPhone([:]))
    }

    func testMalformedSnapshotMissingRequiredFieldIsRejected() {
        // Missing "rep" (required) — must not crash or silently default.
        XCTAssertNil(WatchWire.decodeToWatch(["type": "snapshot", "phase": "active", "paused": false]))
    }

    /// A value that is not a JSON-safe scalar (here, a nested dictionary is a stand-in
    /// for "anything JSONSerialization couldn't also carry across a WCSession message")
    /// must fail the typed cast rather than decode as something else.
    func testNonJSONSafeValueIsRejected() {
        let dict: [String: Any] = ["type": "heartRate", "bpm": ["nested": "object"], "t": 1.0]
        XCTAssertNil(WatchWire.decodeToPhone(dict))
    }

    func testNonJSONSafeButtonNameIsRejected() {
        let dict: [String: Any] = ["type": "button", "name": 42]
        XCTAssertNil(WatchWire.decodeToPhone(dict))
    }

    // MARK: W1 — new snapshot keys, setup, new ToWatch/ToPhone kinds

    func testSnapshotNewKeysRoundTrip() throws {
        var s = WatchWire.LiveSnapshot(phase: "rest", rep: 10, repsTarget: 10, set: 2, setsTarget: 3,
                                       exerciseName: "Forearm turns", hr: 104, paused: false)
        s.exerciseId = "forearm_rotation"; s.goodReps = 8; s.restRemaining = 42; s.countdown = nil; s.isWatchExercise = true
        let d = WatchWire.encode(.snapshot(s))
        XCTAssertEqual(d["restRemaining"] as? Int, 42); XCTAssertNil(d["countdown"])
        XCTAssertEqual(WatchWire.decodeToWatch(d), .snapshot(s))
    }

    func testOldSnapshotStillDecodes() {   // a Plan-4 phone talking to a new watch
        let d: [String: Any] = ["type": "snapshot", "phase": "active", "rep": 3, "paused": false]
        guard case .snapshot(let s)? = WatchWire.decodeToWatch(d) else { return XCTFail() }
        XCTAssertNil(s.goodReps); XCTAssertNil(s.isWatchExercise)
    }

    func testSetupRoundTrip() throws {
        let setup = WatchWire.Setup(firstName: "Maya", hrLimit: 120, hrLimitSetBy: "Dr. Lee", therapistName: "Dr. Lee",
            tremorMonitoring: false, prefs: .init(taps: "strong", arm: "right", doubleTap: true, captions: true),
            plan: [.init(id: "forearm_rotation", name: "Forearm turns", sets: 3, reps: 10, doneSets: 0, watch: true, goalDeg: 80)])
        let d = WatchWire.encode(.setup(setup))
        XCTAssertEqual(d["type"] as? String, "setup")
        XCTAssertEqual(WatchWire.decodeToWatch(d), .setup(setup))
    }

    func testEveryNewToWatchKindRoundTrips() {
        let all: [WatchWire.ToWatch] = [
            .breather(state: "start", bpm: 128, limit: 120), .breather(state: "end", bpm: nil, limit: nil),
            .recovery(setIndex: 2, peakBpm: 126, bpm60: 104, drop: 22),
            .rotationRep(exerciseId: "forearm_rotation", rep: 4, repsTarget: 10, angle: 68, goal: 80, reached: false),
            .rotationSet(exerciseId: "forearm_rotation", setIndex: 1, angles: [62, 70.5, 82], best: 82, avg: 71.5, goal: 80),
            .sync(progress: 0.6, locked: false), .caption(text: "Nice and slow"),
            .formCue(text: "Keep your elbow at your side", rep: 4), .formCue(text: "Knees out", rep: nil),
            .haptic(pattern: "countdown_tick"), .haptic(pattern: "breather")]
        for m in all { XCTAssertEqual(WatchWire.decodeToWatch(WatchWire.encode(m)), m, "\(m)") }
    }

    func testEveryNewToPhoneKindRoundTrips() {
        let all: [WatchWire.ToPhone] = [
            .button(name: "resume"),
            .wrist(t: 1.5, quat: [0.99, 0.01, 0.05, 0.02], accel: [0.02, -9.79, 0.15], deg: 41.5),
            .wrist(t: 1.6, quat: [1, 0, 0, 0], accel: [0, 0, 0], deg: nil),
            .syncWindow(t0: 100, dtMs: 20, mags: [0.1, 0.4]),
            .tremor(days: [.init(day: "2026-10-11", noneMin: 420, slightMin: 190, mildMin: 25, strongMin: 0, unknownMin: 60, dyskinesia: "few", source: "simulated")]),
            .prefs(taps: "strong", arm: nil, doubleTap: nil, captions: false), .setupDone]
        for m in all { XCTAssertEqual(WatchWire.decodeToPhone(WatchWire.encode(m)), m, "\(m)") }
    }

    func testIntegersArrivingAsDoublesStillDecode() {  // WC/NSNumber bridging
        let d: [String: Any] = ["type": "recovery", "setIndex": 2.0, "peakBpm": 126.0, "bpm60": 104, "drop": 22]
        XCTAssertEqual(WatchWire.decodeToWatch(d), .recovery(setIndex: 2, peakBpm: 126, bpm60: 104, drop: 22))
    }
}

final class PushThrottleTests: XCTestCase {
    private let t0 = Date(timeIntervalSince1970: 1_000)

    func testFirstPushGoesOutImmediately() {
        let throttle = PushThrottle(interval: 0.5)
        XCTAssertTrue(throttle.shouldSend(t0))
        XCTAssertNil(throttle.pendingAfter(t0))
    }

    func testPushInsideTheWindowWaitsForTheTrailingEdge() throws {
        var throttle = PushThrottle(interval: 0.5)
        throttle.markSent(t0)
        let now = t0.addingTimeInterval(0.2)
        XCTAssertFalse(throttle.shouldSend(now))
        let delay = try XCTUnwrap(throttle.pendingAfter(now))
        XCTAssertEqual(delay, 0.3, accuracy: 1e-6)
    }

    func testPushAfterTheWindowGoesOutAtOnce() {
        var throttle = PushThrottle(interval: 0.5)
        throttle.markSent(t0)
        let now = t0.addingTimeInterval(0.5)
        XCTAssertTrue(throttle.shouldSend(now))
        XCTAssertNil(throttle.pendingAfter(now))
    }

    func testTrailingPushReopensTheWindow() {
        var throttle = PushThrottle(interval: 0.5)
        throttle.markSent(t0)
        let trailing = t0.addingTimeInterval(0.5)
        throttle.markSent(trailing)
        XCTAssertFalse(throttle.shouldSend(trailing.addingTimeInterval(0.1)))
        XCTAssertEqual(throttle.pendingAfter(trailing.addingTimeInterval(0.1)) ?? -1, 0.4, accuracy: 1e-6)
    }

    func testIdleSnapshotRoundTrips() throws {
        let encoded = WatchWire.encode(.snapshot(.idle))
        XCTAssertEqual(encoded["phase"] as? String, "idle")
        XCTAssertEqual(encoded["rep"] as? Int, 0)
        XCTAssertEqual(try XCTUnwrap(WatchWire.decodeToWatch(encoded)), .snapshot(.idle))
    }
}
