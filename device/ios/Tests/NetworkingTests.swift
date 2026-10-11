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
            #"{"type":"arrived","patient_id":"maya"}"#,
            #"{"type":"greeting","text":"Hello","audio_url":"/audio/a1.mp3"}"#,
            #"{"type":"awaiting_gesture","gesture":"raised_hand"}"#,
            #"{"type":"gesture","name":"crossed_arms"}"#,
            #"{"type":"countdown","n":3}"#,
            #"{"type":"cue","id":"balance","text":"Even out your weight","audio_url":"/audio/balance.mp3"}"#,
            #"{"type":"rep","index":1,"quality":"good","flags":[]}"#,
            #"{"type":"set_done","set":1,"reps":10}"#,
            #"{"type":"paused","reason":"stop_gesture"}"#,
            #"{"type":"resumed"}"#,
            #"{"type":"alert","alert_id":"a1","kind":"fall","step":"detected","patient_id":"p1","message":"Checking in"}"#,
            #"{"type":"fall","stage":"confirmed","drop_m":0.9}"#,
            #"{"type":"fall_status","posture":"lying","moving":false}"#,
            #"{"type":"coach_adapt","action":"extend_rest","detail":{"rest_s":90}}"#,
            #"{"type":"proposal_created","proposal_id":1,"patient_id":"maya","agent":"copilot"}"#,
            #"{"type":"haptic","pattern":"good_rep"}"#,
            #"{"type":"hr","t":1,"bpm":80}"#,
            #"{"type":"wrist","t":1,"quat":[0,0,0,1],"accel":[0,1,0]}"#,
            #"{"type":"watch_button","name":"ready"}"#,
            #"{"type":"command","name":"im_ok","payload":{}}"#,
            #"{"type":"summary_ready","session_id":"s1","spoken":"Nice work"}"#,
            #"{"type":"device_health","battery_pct":81,"app":"watch"}"#,
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
        let command = SocketEvent.command(.pain(level: 7))
        let object = try JSONDecoder().decode(JSONValue.self, from: WireJSON.encoder().encode(command))
        XCTAssertEqual(object, .object(["type": .string("command"), "name": .string("pain"), "payload": .object(["level": .number(7)])]))
    }

    func testServerAddressValidationAndWebSocketURL() throws {
        XCTAssertEqual(try ServerAddress(" https://mira.local:8000/ ").endpoint(["ws", "events"], webSocket: true).absoluteString, "wss://mira.local:8000/ws/events")
        // Every socket carries the token, URL-encoded ("+", "/", "=" and "&" must survive).
        XCTAssertEqual(try ServerAddress("http://mira.local:8000").webSocket(["ws", "pose"], token: "a+b/c=d&e").absoluteString,
                       "ws://mira.local:8000/ws/pose?token=a%2Bb%2Fc%3Dd%26e")
        let url = try ServerAddress("http://h:8000").webSocket(["ws", "events"], token: "t-_~1")
        XCTAssertEqual(URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first?.value, "t-_~1")
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
}
