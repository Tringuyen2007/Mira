import XCTest
@testable import MiraNetworking

// Live tests, skipped unless configured:
//   Tri's real server:  MIRA_SERVER_URL=http://127.0.0.1:8000 MIRA_API_TOKEN=<token>
//     started with MIRA_SOURCE=mock DB_DISABLED=1 DEMO_TOOLS_ENABLED=1 MIRA_API_TOKEN=<token> make run
//   Outage/recovery:    MIRA_MOCK_URL=http://127.0.0.1:8001 MIRA_API_TOKEN=<token>
//     against python3 tools/mira_network_mock.py --port 8001 (it has /test/drop to cut connections)
// The token is never printed.
@MainActor
final class IntegrationTests: XCTestCase {
    private func config(_ urlKey: String) throws -> (ServerAddress, String) {
        let env = ProcessInfo.processInfo.environment
        guard let url = env[urlKey], let token = env["MIRA_API_TOKEN"], !token.isEmpty else {
            throw XCTSkip("Set \(urlKey) and MIRA_API_TOKEN to run against a live server.")
        }
        return (try ServerAddress(url), token)
    }

    func testAgainstRealServer() async throws {
        let (address, token) = try config("MIRA_SERVER_URL")
        let api = APIClient(address: address, token: token)

        // Auth: public health; 401 for a wrong token and for no header at all; OK with the token.
        let health = try await api.health()
        XCTAssertEqual(health.status, "ok")
        do {
            _ = try await APIClient(address: address, token: "wrong-token").calibrationStatus()
            XCTFail("wrong token must be rejected")
        } catch let error as APIError { XCTAssertEqual(error, .unauthorized) }
        let (_, bare) = try await URLSession.shared.data(from: address.endpoint(["calibration", "status"]))
        XCTAssertEqual((bare as? HTTPURLResponse)?.statusCode, 401)
        let calibration = try await api.calibrationStatus()
        let count = try await api.notificationCount()
        print("REST ok: calibration.status=\(calibration.status) notifications.unread=\(count.unread)")

        // A refused socket stops in `.rejected` instead of reconnecting forever.
        let refused = LiveSocket()
        refused.connect(to: address.webSocket(["ws", "events"], token: "wrong-token"))
        try await waitUntil { refused.state == .rejected }
        refused.disconnect()
        print("WS with wrong token: rejected")
        let unpaired = LiveSessionStore()
        unpaired.connect(to: address, token: nil)
        XCTAssertEqual(unpaired.connectionState, .notPaired)

        // Streams.
        let store = LiveSessionStore()
        var seen = Set<String>()  // every event type decoded on /ws/events
        let decodeEvent = store.eventSocket.onMessage
        store.eventSocket.onMessage = { data in
            decodeEvent?(data)
            if let event = try? WireJSON.decoder().decode(SocketEvent.self, from: data) { seen.insert(event.type) }
        }
        store.connect(to: address, token: token)
        defer { store.disconnect() }
        try await waitUntil { store.connectionState == .connected && store.live != nil && store.pose != nil }
        let started = try await api.createSession()  // restarts the mock program: arrived → greeting → set 1
        print("session started: exercise=\(started.exerciseId)")
        try await waitUntil(timeout: 60) { seen.isSuperset(of: ["arrived", "greeting", "countdown", "cue", "rep"]) }
        XCTAssertFalse(seen.contains { $0.hasPrefix("unknown") })
        print("streams: live phase=\(store.live?.phase ?? "-") pose joints=\(store.pose?.joints.count ?? 0) messages=\(store.receivedMessages)")
        print("event types decoded: \(seen.sorted().joined(separator: ","))")

        // Commands: the server echoes what passed its allow-list.
        try await store.send(.pause)
        try await waitUntil { store.lastCommand?.name == .pause }
        print("command accepted: pause (echoed on /ws/events)")

        // Safety path: a demo fall produces typed `alert` events; I'm OK closes the incident.
        var fall = URLRequest(url: address.endpoint(["demo", "trigger_fall"]))
        fall.httpMethod = "POST"
        fall.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let (_, fallResponse) = try await URLSession.shared.data(for: fall)
        guard (fallResponse as? HTTPURLResponse)?.statusCode == 200 else {
            throw XCTSkip("Start the server with DEMO_TOOLS_ENABLED=1 for the fall check.")
        }
        try await waitUntil(timeout: 30) { store.alert?.kind == "fall" }
        let alert = try XCTUnwrap(store.alert)
        print("fall alert decoded: step=\(alert.step) message=\(alert.message ?? "-")")
        try await store.send(.imOK)
        try await waitUntil(timeout: 30) { store.alert == nil }
        print("im_ok accepted: incident closed; event types decoded: \(seen.sorted().joined(separator: ","))")

        _ = try await api.stopSession(id: started.sessionId)
        XCTAssertNil(store.decodingError, store.decodingError ?? "")
        store.disconnect()
        try await Task.sleep(for: .seconds(2))
        XCTAssertEqual(store.connectionState, .disconnected, "Explicit disconnect must stop retries")
    }

    func testOutageAndRecoveryAgainstMock() async throws {
        let (address, token) = try config("MIRA_MOCK_URL")
        let api = APIClient(address: address, token: token)
        _ = try await api.calibrationStatus()
        do {
            _ = try await APIClient(address: address, token: "wrong-token").calibrationStatus()
            XCTFail("wrong token must be rejected")
        } catch let error as APIError { XCTAssertEqual(error, .unauthorized) }
        do {
            let _: JSONValue = try await api.request(path: ["not-a-route"])
            XCTFail("404 should fail")
        } catch APIError.http(let code, _) { XCTAssertEqual(code, 404) }

        let store = LiveSessionStore()
        store.connect(to: address, token: token)
        defer { store.disconnect() }
        try await waitUntil { store.connectionState == .connected && store.live != nil && store.pose != nil && store.latestEvent != nil }
        try await store.send(.pause)
        try await waitUntil { store.live?.paused == true }
        let rep = store.live?.rep
        _ = try await api.perform("POST", path: ["test", "drop"], authenticated: false)
        try await waitUntil { store.connectionState != .connected }
        XCTAssertEqual(store.live?.rep, rep)
        try await waitUntil { store.connectionState == .connected }
        try await store.send(.start)
        try await waitUntil { store.live?.paused == false }
        XCTAssertNil(store.decodingError)
        store.disconnect()
        try await Task.sleep(for: .seconds(2))
        XCTAssertEqual(store.connectionState, .disconnected, "Explicit disconnect must stop retries")
        print("mock outage: disconnected, retained rep=\(rep ?? -1), reconnected, resumed")
    }

    private func waitUntil(timeout: TimeInterval = 15, _ predicate: () -> Bool) async throws {
        let deadline = Date().addingTimeInterval(timeout)
        while !predicate() {
            if Date() > deadline { XCTFail("Timed out waiting for networking state"); throw URLError(.timedOut) }
            try await Task.sleep(for: .milliseconds(50))
        }
    }
}
