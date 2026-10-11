import Foundation

enum APIError: LocalizedError, Equatable {
    case invalidAddress, invalidResponse, http(Int, String), notConnected, notPaired, unauthorized, forbidden

    var errorDescription: String? {
        switch self {
        case .invalidAddress: return "Enter an http:// or https:// server address without a path, query, or password."
        case .invalidResponse: return "The Mira server returned an invalid response."
        case .http(let code, let message): return "Mira server returned HTTP \(code): \(message)"
        case .notConnected: return "Mira server offline. Reconnect before sending a command."
        case .notPaired: return "Not paired. Enter the Mira server token in Settings."
        case .unauthorized: return "Token rejected by the Mira server. Check the token in Settings."
        case .forbidden: return "This isn't your data to see."
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

    func endpoint(_ components: [String], query: [URLQueryItem] = [], webSocket: Bool = false) -> URL {
        var result = url
        for component in components { result.appendPathComponent(component) }
        var parts = URLComponents(url: result, resolvingAgainstBaseURL: false)!
        if webSocket { parts.scheme = url.scheme?.lowercased() == "https" ? "wss" : "ws" }
        if !query.isEmpty {
            // URLComponents leaves "+" unescaped in queries; servers decode it as a space.
            parts.percentEncodedQueryItems = query.map {
                URLQueryItem(name: $0.name, value: $0.value.map(Self.encodeQueryValue))
            }
        }
        return parts.url!
    }

    /// Browsers can't set headers on WebSockets, so the server takes the token as
    /// `?token=` on every socket (README "Try it"); a wrong token closes with 1008.
    func webSocket(_ components: [String], token: String, query: [URLQueryItem] = []) -> URL {
        endpoint(components, query: [URLQueryItem(name: "token", value: token)] + query, webSocket: true)
    }

    private static func encodeQueryValue(_ value: String) -> String {
        var allowed = CharacterSet.urlQueryAllowed
        allowed.remove(charactersIn: "+&=?#/")
        return value.addingPercentEncoding(withAllowedCharacters: allowed) ?? value
    }
}

// Every REST route except GET /health requires `Authorization: Bearer <token>`.
struct APIClient {
    let address: ServerAddress
    let token: String?
    var session: URLSession = .shared

    // Mutations are never automatically retried: replaying a start/share can
    // duplicate side effects when the server processed a lost response.
    func request<Response: Decodable>(
        _ method: String = "GET", path: [String], query: [URLQueryItem] = [], body: Data? = nil,
        as: Response.Type = Response.self
    ) async throws -> Response {
        let data = try await perform(method, path: path, query: query, body: body)
        return try WireJSON.decoder().decode(Response.self, from: data)
    }

    func perform(_ method: String, path: [String], query: [URLQueryItem] = [], body: Data? = nil,
                 authenticated: Bool = true) async throws -> Data {
        var request = URLRequest(url: address.endpoint(path, query: query))
        request.httpMethod = method
        request.httpBody = body
        request.timeoutInterval = 10
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        if authenticated {
            guard let token else { throw APIError.notPaired }
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        if response.statusCode == 401 { throw APIError.unauthorized }
        if response.statusCode == 403 { throw APIError.forbidden }
        guard (200..<300).contains(response.statusCode) else {
            throw APIError.http(response.statusCode, String(decoding: data.prefix(512), as: UTF8.self))
        }
        return data
    }

    private func json<Body: Encodable>(_ body: Body) throws -> Data { try WireJSON.encoder().encode(body) }

    /// Public liveness check (no token).
    func health() async throws -> HealthStatus {
        let data = try await perform("GET", path: ["health"], authenticated: false)
        return try WireJSON.decoder().decode(HealthStatus.self, from: data)
    }
    func calibrationStatus() async throws -> CalibrationStatus { try await request(path: ["calibration", "status"]) }
    func progress(patientID: String, exerciseID: String? = nil, days: Int = 90) async throws -> PatientProgress {
        var query = [URLQueryItem(name: "days", value: String(days))]
        if let exerciseID { query.append(URLQueryItem(name: "exercise_id", value: exerciseID)) }
        return try await request(path: ["patients", patientID, "progress"], query: query)
    }
    func prescriptions(patientID: String) async throws -> PrescriptionList { try await request(path: ["prescriptions", patientID]) }
    func session(id: String) async throws -> SessionDetail { try await request(path: ["sessions", id]) }
    /// `downsample: 0` returns one page of full-rate frames; continue with `t0: next_t0`.
    func frames(sessionID: String, downsample: Int = 300, t0: String? = nil) async throws -> SessionFrames {
        var query = [URLQueryItem(name: "downsample", value: String(downsample))]
        if let t0 { query.append(URLQueryItem(name: "t0", value: t0)) }
        return try await request(path: ["sessions", sessionID, "frames"], query: query)
    }
    /// Starts a session (stopping any current one). Omitted fields use the server's defaults.
    func createSession(_ body: SessionRequest = SessionRequest()) async throws -> SessionStarted {
        try await request("POST", path: ["sessions"], body: json(body))
    }
    func stopSession(id: String) async throws -> SessionStopped { try await request("POST", path: ["sessions", id, "stop"]) }
    func shareSession(id: String) async throws -> SessionShared { try await request("POST", path: ["sessions", id, "share"]) }
    /// A patient's session history for the Progress/Care screens.
    func sessions(patientID: String, limit: Int? = nil) async throws -> SessionList {
        var query: [URLQueryItem] = []
        if let limit { query.append(URLQueryItem(name: "limit", value: String(limit))) }
        return try await request(path: ["patients", patientID, "sessions"], query: query)
    }
    /// Records pain/effort/note after a session ends (the Summary screen's check-in).
    func checkin(sessionID: String, _ body: CheckinRequest) async throws -> CheckinResponse {
        try await request("POST", path: ["sessions", sessionID, "checkin"], body: json(body))
    }
    func askCopilot(question: String, patientID: String? = nil) async throws -> CopilotResponse {
        try await request("POST", path: ["copilot", "ask"], body: json(CopilotRequest(question: question, patientId: patientID)))
    }
    func proposals(status: String = "pending", patientID: String? = nil) async throws -> ProposalList {
        var query = [URLQueryItem(name: "status", value: status)]
        if let patientID { query.append(URLQueryItem(name: "patient_id", value: patientID)) }
        return try await request(path: ["proposals"], query: query)
    }
    func decideProposal(id: Int, action: ProposalAction, _ body: ProposalDecisionRequest) async throws -> ProposalDecision {
        try await request("POST", path: ["proposals", String(id), action.rawValue], body: json(body))
    }
    func alerts(openOnly: Bool = true) async throws -> AlertList {
        try await request(path: ["alerts"], query: [URLQueryItem(name: "open", value: String(openOnly))])
    }
    func resolveAlert(id: String, _ body: AlertResolveRequest = AlertResolveRequest()) async throws -> AlertResolution {
        try await request("POST", path: ["alerts", id, "resolve"], body: json(body))
    }
    func notificationCount() async throws -> NotificationCount { try await request(path: ["notifications", "count"]) }

    // MARK: Pairing and /me (Plan 1)

    /// Public: a therapist code plus the onboarding profile → a patient token. Never retried.
    func pair(_ body: PairRequest) async throws -> PairResponse {
        let data = try await perform("POST", path: ["pair"], body: json(body), authenticated: false)
        return try WireJSON.decoder().decode(PairResponse.self, from: data)
    }
    func me() async throws -> MeResponse { try await request(path: ["me"]) }
    func updateMe(_ body: MeUpdate) async throws -> MeResponse { try await request("PUT", path: ["me"], body: json(body)) }
    func exportMe() async throws -> Data { try await perform("GET", path: ["me", "export"]) }
    func deleteMe() async throws { _ = try await perform("DELETE", path: ["me"]) }
}
