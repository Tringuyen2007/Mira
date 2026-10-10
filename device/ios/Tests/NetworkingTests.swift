import XCTest
@testable import MiraNetworking

final class NetworkingTests: XCTestCase {
    func testWireKeysAndNullableLiveFields() throws {
        let data = Data(#"{"phase":"active","exercise_id":"squat","rep":3,"reps_target":10,"countdown":null,"main_angle":72.5,"load_l_pct":52,"flags":[],"cue_id":null,"paused":false}"#.utf8)
        let live = try WireJSON.decoder().decode(LiveMessage.self, from: data)
        XCTAssertEqual(live.exerciseId, "squat")
        XCTAssertEqual(live.loadLPct, 52)
        XCTAssertNil(live.countdown)
        let encoded = try XCTUnwrap(JSONSerialization.jsonObject(with: WireJSON.encoder().encode(live)) as? [String: Any])
        XCTAssertEqual(encoded["load_l_pct"] as? Double, 52)
        XCTAssertNil(encoded["load_lpct"])
    }

    func testAllSection16EventsAndUnknownType() throws {
        let events = [
            #"{"type":"arrived"}"#,
            #"{"type":"greeting","audio_id":"a1","text":"Hello"}"#,
            #"{"type":"awaiting_gesture","gesture":"thumbs_up"}"#,
            #"{"type":"gesture","name":"crossed_arms"}"#,
            #"{"type":"countdown","n":3}"#,
            #"{"type":"cue","id":"balance","text":"Even out your weight"}"#,
            #"{"type":"rep","index":1,"quality":"good","flags":[]}"#,
            #"{"type":"set_done"}"#,
            #"{"type":"paused","reason":"stop_gesture"}"#,
            #"{"type":"alert","kind":"fall","step":"detected","patient_id":"p1"}"#,
            #"{"type":"haptic","pattern":"good_rep"}"#,
            #"{"type":"hr","t":1,"bpm":80}"#,
            #"{"type":"wrist","t":1,"quat":[0,0,0,1],"accel":[0,1,0]}"#,
            #"{"type":"watch_button","name":"ready"}"#,
            #"{"type":"command","name":"im_ok","payload":{}}"#,
            #"{"type":"summary_ready","session_id":"s1"}"#,
            #"{"type":"device_health","online":true}"#,
            #"{"type":"future_event","new_field":42}"#
        ]
        for json in events {
            let data = Data(json.utf8)
            let value = try WireJSON.decoder().decode(SocketEvent.self, from: data)
            let roundTrip = try WireJSON.encoder().encode(value)
            XCTAssertEqual(try JSONDecoder().decode(JSONValue.self, from: roundTrip),
                           try JSONDecoder().decode(JSONValue.self, from: data), json)
        }
        XCTAssertThrowsError(try WireJSON.decoder().decode(SocketEvent.self, from: Data(#"{"type":"cue","id":"missing-text"}"#.utf8)))
    }

    func testPoseDimensionsAndCommandPayload() throws {
        let pose = PoseMessage(t: 1, joints: Array(repeating: [0, 1, 2], count: 33), confidence: Array(repeating: 1, count: 33), loadLPct: 50, flags: [])
        let decoded = try WireJSON.decoder().decode(PoseMessage.self, from: WireJSON.encoder().encode(pose))
        XCTAssertTrue(decoded.hasValidDimensions)
        XCTAssertFalse(PoseMessage(t: 1, joints: [], confidence: [], loadLPct: 50, flags: []).hasValidDimensions)
        let command = SocketEvent.command(CommandMessage(name: .pain, payload: .object(["score": .number(4)])))
        let object = try JSONDecoder().decode(JSONValue.self, from: WireJSON.encoder().encode(command))
        XCTAssertEqual(object, .object(["type": .string("command"), "name": .string("pain"), "payload": .object(["score": .number(4)])]))
    }

    func testServerAddressValidationAndWebSocketURL() throws {
        XCTAssertEqual(try ServerAddress(" https://mira.local:8000/ ").endpoint(["ws", "events"], webSocket: true).absoluteString, "wss://mira.local:8000/ws/events")
        for invalid in ["laptop.local:8000", "ftp://host", "http://host/path", "http://u:p@host", "http://host?q=1", "http://host:0"] {
            XCTAssertThrowsError(try ServerAddress(invalid), invalid)
        }
    }

    @MainActor
    func testDisconnectRetainsStateAndRejectsCommands() async throws {
        let store = LiveSessionStore()
        store.liveSocket.onMessage?(Data(#"{"phase":"active","exercise_id":"squat","rep":7,"reps_target":10,"load_l_pct":50,"flags":[],"paused":false}"#.utf8))
        store.liveSocket.onMessage?(Data("broken json".utf8))
        XCTAssertNotNil(store.decodingError)
        XCTAssertEqual(store.live?.rep, 7)
        store.disconnect()
        XCTAssertEqual(store.live?.rep, 7)
        XCTAssertEqual(store.connectionState, .disconnected)
        do {
            try await store.send(.start)
            XCTFail("Offline command should fail")
        } catch { XCTAssertTrue(error is APIError) }
    }

    @MainActor
    func testAgainstRunningMock() async throws {
        guard let url = ProcessInfo.processInfo.environment["MIRA_MOCK_URL"] else {
            throw XCTSkip("Run with MIRA_MOCK_URL=http://127.0.0.1:8000 and the development mock running.")
        }
        let address = try ServerAddress(url)
        let api = APIClient(address: address)
        _ = try await api.calibrationStatus()
        _ = try await api.createSession(payload: .object(["patient_id": .string("demo")]))
        try await api.stopSession(id: "mock-session") // Empty 204 must succeed.
        let answer = try await api.askCopilot(question: "How am I doing?")
        XCTAssertEqual(answer.answer, "Mock answer")
        do {
            let _: JSONValue = try await api.request(path: ["not-a-route"])
            XCTFail("404 should fail")
        } catch APIError.http(let code, _) { XCTAssertEqual(code, 404) }

        let store = LiveSessionStore()
        store.connect(to: address)
        defer { store.disconnect() }
        try await waitUntil { store.connectionState == .connected && store.live != nil && store.pose != nil && store.latestEvent != nil }
        try await store.send(.pause)
        try await waitUntil { store.live?.paused == true }
        let rep = store.live?.rep
        _ = try await api.perform("POST", path: ["test", "drop"])
        try await waitUntil { store.connectionState != .connected }
        XCTAssertEqual(store.live?.rep, rep)
        try await waitUntil { store.connectionState == .connected }
        try await store.send(.start)
        try await waitUntil { store.live?.paused == false }
        XCTAssertNil(store.decodingError)
        store.disconnect()
        try await Task.sleep(for: .seconds(2))
        XCTAssertEqual(store.connectionState, .disconnected, "Explicit disconnect must stop retries")
    }

    @MainActor
    private func waitUntil(_ predicate: () -> Bool) async throws {
        let deadline = Date().addingTimeInterval(15)
        while !predicate() {
            if Date() > deadline { XCTFail("Timed out waiting for networking state"); throw URLError(.timedOut) }
            try await Task.sleep(for: .milliseconds(50))
        }
    }
}
