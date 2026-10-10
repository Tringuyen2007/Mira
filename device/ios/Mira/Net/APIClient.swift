import Foundation

enum APIError: LocalizedError {
    case invalidAddress, invalidResponse, http(Int, String), notConnected

    var errorDescription: String? {
        switch self {
        case .invalidAddress: return "Enter an http:// or https:// server address without a path, query, or password."
        case .invalidResponse: return "The Mira server returned an invalid response."
        case .http(let code, let message): return "Mira server returned HTTP \(code): \(message)"
        case .notConnected: return "Mira server offline. Reconnect before sending a command."
        }
    }
}

struct ServerAddress: Equatable, Sendable {
    let url: URL

    init(_ text: String) throws {
        guard let parts = URLComponents(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
              ["http", "https"].contains(parts.scheme?.lowercased() ?? ""),
              let host = parts.host, !host.isEmpty,
              parts.user == nil, parts.password == nil, parts.query == nil, parts.fragment == nil,
              parts.path.isEmpty || parts.path == "/",
              parts.port.map({ (1...65535).contains($0) }) ?? true,
              let url = parts.url else { throw APIError.invalidAddress }
        self.url = url
    }

    func endpoint(_ components: [String], webSocket: Bool = false) -> URL {
        var result = url
        for component in components { result.appendPathComponent(component) }
        if webSocket {
            var parts = URLComponents(url: result, resolvingAgainstBaseURL: false)!
            parts.scheme = url.scheme?.lowercased() == "https" ? "wss" : "ws"
            result = parts.url!
        }
        return result
    }
}

struct APIClient {
    let address: ServerAddress
    var session: URLSession = .shared

    // Mutations are never automatically retried: replaying a start/share can
    // duplicate side effects when the server processed a lost response.
    func request<Response: Decodable>(
        _ method: String = "GET", path: [String], body: Data? = nil,
        as: Response.Type = Response.self
    ) async throws -> Response {
        let data = try await perform(method, path: path, body: body)
        return try WireJSON.decoder().decode(Response.self, from: data)
    }

    func perform(_ method: String, path: [String], body: Data? = nil) async throws -> Data {
        var request = URLRequest(url: address.endpoint(path))
        request.httpMethod = method
        request.httpBody = body
        request.timeoutInterval = 10
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard (200..<300).contains(response.statusCode) else {
            throw APIError.http(response.statusCode, String(decoding: data.prefix(512), as: UTF8.self))
        }
        return data
    }

    func calibrationStatus() async throws -> JSONValue { try await request(path: ["calibration", "status"]) }
    func progress(patientID: String) async throws -> JSONValue { try await request(path: ["patients", patientID, "progress"]) }
    func prescription(patientID: String) async throws -> JSONValue { try await request(path: ["prescriptions", patientID]) }
    func session(id: String) async throws -> JSONValue { try await request(path: ["sessions", id]) }
    func frames(sessionID: String) async throws -> JSONValue { try await request(path: ["sessions", sessionID, "frames"]) }
    func createSession(payload: JSONValue) async throws -> JSONValue {
        try await request("POST", path: ["sessions"], body: WireJSON.encoder().encode(payload))
    }
    func stopSession(id: String) async throws { _ = try await perform("POST", path: ["sessions", id, "stop"]) }
    func shareSession(id: String, payload: JSONValue) async throws {
        _ = try await perform("POST", path: ["sessions", id, "share"], body: WireJSON.encoder().encode(payload))
    }
    func askCopilot(question: String) async throws -> CopilotResponse {
        try await request("POST", path: ["copilot", "ask"], body: WireJSON.encoder().encode(CopilotRequest(question: question)))
    }
    func proposals() async throws -> JSONValue { try await request(path: ["proposals"]) }
    func updateProposal(id: String, action: ProposalAction, payload: JSONValue) async throws {
        _ = try await perform("POST", path: ["proposals", id, action.rawValue], body: WireJSON.encoder().encode(payload))
    }
    func sendCue(sessionID: String, text: String) async throws {
        _ = try await perform("POST", path: ["sessions", sessionID, "cue"], body: WireJSON.encoder().encode(TherapistCueRequest(text: text)))
    }
}
