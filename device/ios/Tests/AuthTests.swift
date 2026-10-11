import XCTest
@testable import MiraNetworking

/// Answers every request from `handler` and records it.
final class StubProtocol: URLProtocol {
    nonisolated(unsafe) static var requests: [URLRequest] = []
    nonisolated(unsafe) static var handler: (URLRequest) -> (Int, String) = { _ in (200, "{}") }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.requests.append(request)
        let (status, body) = Self.handler(request)
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}

    static func session() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubProtocol.self]
        return URLSession(configuration: config)
    }
}

final class AuthTests: XCTestCase {
    override func setUp() {
        StubProtocol.requests = []
        StubProtocol.handler = { request in
            request.value(forHTTPHeaderField: "Authorization") == "Bearer secret-token"
                ? (200, #"{"status":"unknown","calibrated":false,"cameras":[],"errors":null,"note":"x"}"#)
                : (401, #"{"detail":"unauthorized"}"#)
        }
    }

    private func client(token: String?) throws -> APIClient {
        APIClient(address: try ServerAddress("http://mira.test:8000"), token: token, session: StubProtocol.session())
    }

    func testRestSendsBearerToken() async throws {
        let status = try await client(token: "secret-token").calibrationStatus()
        XCTAssertFalse(status.calibrated)
        XCTAssertEqual(StubProtocol.requests.first?.value(forHTTPHeaderField: "Authorization"), "Bearer secret-token")
    }

    func testWrongTokenIsUnauthorized() async throws {
        do {
            _ = try await client(token: "wrong").calibrationStatus()
            XCTFail("401 expected")
        } catch let error as APIError { XCTAssertEqual(error, .unauthorized) }
    }

    func testMissingTokenNeverCallsTheServer() async throws {
        do {
            _ = try await client(token: nil).calibrationStatus()
            XCTFail("notPaired expected")
        } catch let error as APIError { XCTAssertEqual(error, .notPaired) }
        XCTAssertTrue(StubProtocol.requests.isEmpty)
    }

    func testHealthIsPublic() async throws {
        StubProtocol.handler = { _ in (200, #"{"status":"ok","time":1}"#) }
        let health = try await client(token: nil).health()
        XCTAssertEqual(health.status, "ok")
        XCTAssertNil(StubProtocol.requests.first?.value(forHTTPHeaderField: "Authorization"))
    }

    func testTokenMasking() {
        XCTAssertEqual(PairingToken.masked("abcdefghijklmnop"), "••••mnop")
        XCTAssertEqual(PairingToken.masked("short"), "••••")
        XCTAssertNil(PairingToken.normalized("  \n"))
        XCTAssertEqual(PairingToken.normalized(" tok "), "tok")
    }

    @MainActor
    func testUnpairedAppShowsNotPairedInsteadOfConnecting() throws {
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "mira-test-\(UUID())"))
        let store = MemoryTokenStore()
        let state = AppState(defaults: defaults, tokenStore: store)
        XCTAssertFalse(state.isPaired)
        XCTAssertTrue(state.connect(address: "http://127.0.0.1:9"))
        XCTAssertEqual(state.liveSession.connectionState, .notPaired)
        XCTAssertNotNil(state.configurationError)
        XCTAssertEqual(state.apiClient?.token, nil)
        state.disconnect()
    }

    @MainActor
    func testTokenGoesToTokenStoreNotUserDefaults() throws {
        let suite = "mira-test-\(UUID())"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        let store = MemoryTokenStore()
        let state = AppState(defaults: defaults, tokenStore: store)
        XCTAssertTrue(state.connect(address: "http://127.0.0.1:9", token: "  secret-token-1234  "))
        state.disconnect()
        XCTAssertEqual(store.load(), "secret-token-1234")
        XCTAssertEqual(state.tokenHint, "••••1234")
        XCTAssertFalse(defaults.dictionaryRepresentation().values.contains { "\($0)".contains("secret-token") })
        // An empty field keeps the saved token; forgetting removes it.
        XCTAssertTrue(state.connect(address: "http://127.0.0.1:9"))
        XCTAssertEqual(store.load(), "secret-token-1234")
        state.forgetToken()
        XCTAssertNil(store.load())
        XCTAssertFalse(state.isPaired)
        state.disconnect()
        UserDefaults.standard.removePersistentDomain(forName: suite)
    }

    @MainActor
    func testPingsAreNotData() {
        let store = LiveSessionStore()
        store.liveSocket.onMessage?(Data(#"{"type":"ping"}"#.utf8))
        store.poseSocket.onMessage?(Data(#"{"type":"ping"}"#.utf8))
        store.eventSocket.onMessage?(Data(#"{"type":"ping"}"#.utf8))
        XCTAssertNil(store.decodingError)
        XCTAssertEqual(store.receivedMessages, 0)
    }

    @MainActor
    func testClosedAlertClearsTheSafetyPrompt() {
        let store = LiveSessionStore()
        store.eventSocket.onMessage?(Data(#"{"type":"alert","alert_id":"a","kind":"fall","step":"detected","patient_id":"maya"}"#.utf8))
        XCTAssertEqual(store.alert?.step, "detected")
        store.eventSocket.onMessage?(Data(#"{"type":"alert","alert_id":"a","kind":"fall","step":"closed","patient_id":"maya"}"#.utf8))
        XCTAssertNil(store.alert)
    }
}
