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
