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
