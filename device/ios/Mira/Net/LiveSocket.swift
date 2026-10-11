import Foundation
import Observation

enum ConnectionState: Equatable {
    case disconnected, connecting, connected, reconnecting(attempt: Int)
    /// No token configured: nothing is attempted (the server would refuse every socket).
    case notPaired
    /// The server refused the handshake (wrong token → close 1008 / HTTP 403). Retrying
    /// with the same token cannot succeed, so the socket stops until the token changes.
    case rejected

    var label: String {
        switch self {
        case .disconnected: return "Mira server offline"
        case .connecting: return "Connecting to Mira…"
        case .connected: return "Mira server online"
        case .reconnecting: return "Mira server offline · reconnecting…"
        case .notPaired: return "Not paired · add the Mira server token"
        case .rejected: return "Token rejected by the Mira server"
        }
    }
}

enum SocketChannel: String, CaseIterable { case live, events, pose }

@MainActor
@Observable
final class LiveSocket {
    private(set) var state: ConnectionState = .disconnected
    private(set) var lastError: String?
    @ObservationIgnored var onMessage: ((Data) -> Void)?
    @ObservationIgnored private var runner: Task<Void, Never>?
    @ObservationIgnored private var socket: URLSessionWebSocketTask?
    @ObservationIgnored private var generation = UUID()
    @ObservationIgnored private let session: URLSession

    init(session: URLSession = .shared) { self.session = session }

    func connect(to url: URL) {
        disconnect()
        let generation = self.generation
        state = .connecting
        runner = Task { [weak self] in
            guard let self else { return }
            var attempt = 0
            while !Task.isCancelled && self.generation == generation {
                let socket = self.session.webSocketTask(with: url)
                self.socket = socket
                socket.resume()
                let openedAt = Date()
                let heartbeat = Task { [weak self] in
                    while !Task.isCancelled {
                        // Also times out handshakes and silent half-open connections.
                        let deadline = Task {
                            do {
                                try await Task.sleep(for: .seconds(10))
                                socket.cancel(with: .goingAway, reason: nil)
                            } catch {}
                        }
                        do {
                            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                                socket.sendPing { error in
                                    if let error { continuation.resume(throwing: error) }
                                    else { continuation.resume() }
                                }
                            }
                            deadline.cancel()
                            guard !Task.isCancelled, self?.generation == generation else { return }
                            self?.state = .connected
                            self?.lastError = nil
                            try await Task.sleep(for: .seconds(5))
                        } catch {
                            deadline.cancel()
                            socket.cancel(with: .goingAway, reason: nil)
                            return
                        }
                    }
                }
                do {
                    while !Task.isCancelled {
                        let message = try await socket.receive()
                        guard self.generation == generation, !Task.isCancelled else { break }
                        switch message {
                        case .data(let data): self.onMessage?(data)
                        case .string(let string): self.onMessage?(Data(string.utf8))
                        @unknown default: break
                        }
                    }
                } catch {
                    if self.generation == generation && !Task.isCancelled {
                        self.lastError = error.localizedDescription
                    }
                }
                heartbeat.cancel()
                let refused = Self.wasRefused(socket)
                socket.cancel(with: .goingAway, reason: nil)
                guard !Task.isCancelled, self.generation == generation else { return }
                if refused {
                    self.state = .rejected
                    self.lastError = APIError.unauthorized.localizedDescription
                    return
                }
                // A flapping connection must not reset the backoff on every handshake.
                if Date().timeIntervalSince(openedAt) > 30 { attempt = 0 }
                attempt += 1
                self.state = .reconnecting(attempt: attempt)
                let delay = min(pow(2, Double(min(attempt - 1, 5))), 30)
                do { try await Task.sleep(for: .seconds(delay + Double.random(in: 0...0.25))) }
                catch { return }
            }
        }
    }

    /// The server checks `?token=` before accepting; a bad token closes with 1008,
    /// which a client sees as an HTTP 403 (or 401) handshake response.
    private static func wasRefused(_ socket: URLSessionWebSocketTask) -> Bool {
        if socket.closeCode == .policyViolation { return true }
        let status = (socket.response as? HTTPURLResponse)?.statusCode
        return status == 401 || status == 403
    }

    /// Shows the unpaired state without opening a connection.
    func markNotPaired() {
        disconnect()
        state = .notPaired
    }

    func disconnect() {
        generation = UUID()
        runner?.cancel()
        runner = nil
        socket?.cancel(with: .goingAway, reason: nil)
        socket = nil
        state = .disconnected
        lastError = nil
    }

    func send(_ event: SocketEvent) async throws {
        guard state == .connected, let socket else { throw APIError.notConnected }
        let data = try WireJSON.encoder().encode(event)
        do {
            try await socket.send(.string(String(decoding: data, as: UTF8.self)))
        } catch {
            // Never replay commands on reconnect; they may already have executed.
            socket.cancel(with: .goingAway, reason: nil)
            throw error
        }
    }
}

/// Every /ws/events message carries `session_id`; decoded alongside the typed
/// `SocketEvent` so `currentSessionID` tracks it even for event types this app
/// version does not model.
private struct SessionStamp: Codable { let sessionId: String? }

@MainActor
@Observable
final class LiveSessionStore {
    let liveSocket = LiveSocket()
    let eventSocket = LiveSocket()
    let poseSocket = LiveSocket()
    private(set) var live: LiveMessage?
    private(set) var pose: PoseMessage?
    private(set) var cue: String?
    private(set) var latestEvent: SocketEvent?
    private(set) var alert: AlertMessage?
    private(set) var fallStatus: FallStatusMessage?
    /// The session currently in progress, from any /ws/events message's `session_id`.
    private(set) var currentSessionID: String?
    /// The whole-session AI analysis, delivered once at session end.
    private(set) var sessionSummary: SessionSummaryMessage?
    /// Whether the LCD kiosk is currently online (from `device_status`, `device == "lcd"`).
    private(set) var lcdOnline: Bool?
    /// The server echoes every accepted client message on /ws/events; a command
    /// appearing here proves it passed the allow-list (and the rate limit).
    private(set) var lastCommand: CommandMessage?
    private(set) var decodingError: String?
    private(set) var receivedMessages = 0

    var connectionState: ConnectionState {
        let states = [liveSocket.state, eventSocket.state, poseSocket.state]
        if states.allSatisfy({ $0 == .connected }) { return .connected }
        if states.contains(.rejected) { return .rejected }
        if states.contains(.notPaired) { return .notPaired }
        if let retry = states.first(where: { if case .reconnecting = $0 { return true }; return false }) { return retry }
        if states.contains(.connecting) { return .connecting }
        return .disconnected
    }

    init() {
        liveSocket.onMessage = { [weak self] data in
            guard !WireJSON.isPing(data) else { return }
            self?.decode(data, as: LiveMessage.self) { self?.live = $0 }
        }
        poseSocket.onMessage = { [weak self] data in
            guard !WireJSON.isPing(data) else { return }
            self?.decode(data, as: PoseMessage.self) { pose in
                guard pose.hasValidDimensions else {
                    self?.decodingError = "Invalid pose: expected 33 three-dimensional joints and 33 confidence values."
                    return
                }
                self?.pose = pose
            }
        }
        eventSocket.onMessage = { [weak self] data in
            guard !WireJSON.isPing(data) else { return }
            self?.decode(data, as: SocketEvent.self) { event in
                self?.latestEvent = event
                if case .sessionSummary(let summary) = event {
                    // session_summary arrives late, for the session that just ended, after the
                    // next session's events may already have stamped currentSessionID — never
                    // let it clobber that boundary.
                    self?.sessionSummary = summary
                    self?.cue = summary.spoken
                    return
                }
                // Every other /ws/events message carries the current session's stamp.
                if let stamp = try? WireJSON.decoder().decode(SessionStamp.self, from: data), let sessionId = stamp.sessionId {
                    self?.currentSessionID = sessionId
                }
                switch event {
                case .arrived: self?.sessionSummary = nil
                case .cue(let cue): self?.cue = cue.text
                case .greeting(let greeting): self?.cue = greeting.text
                case .summaryReady(let summary): self?.cue = summary.spoken
                case .deviceStatus(let status):
                    if status.device == nil || status.device == "lcd" { self?.lcdOnline = status.online }
                case .alert(let alert):
                    self?.alert = alert.isOpen ? alert : nil
                    if !alert.isOpen { self?.fallStatus = nil }
                case .fallStatus(let status): self?.fallStatus = status
                case .command(let command): self?.lastCommand = command
                default: break
                }
            }
        }
    }

    private func decode<T: Decodable>(_ data: Data, as type: T.Type, apply: (T) -> Void) {
        do {
            let value = try WireJSON.decoder().decode(type, from: data)
            receivedMessages += 1
            apply(value)
        } catch {
            decodingError = "Message did not match the Mira contract: \(error)"
        }
    }

    func connect(to address: ServerAddress, token: String?, reset: Bool = false) {
        if reset {
            live = nil; pose = nil; cue = nil; latestEvent = nil; alert = nil; fallStatus = nil; lastCommand = nil
            receivedMessages = 0; decodingError = nil
            currentSessionID = nil; sessionSummary = nil; lcdOnline = nil
        }
        guard let token else {
            liveSocket.markNotPaired(); eventSocket.markNotPaired(); poseSocket.markNotPaired()
            return
        }
        liveSocket.connect(to: address.webSocket(["ws", "live"], token: token))
        eventSocket.connect(to: address.webSocket(["ws", "events"], token: token))
        poseSocket.connect(to: address.webSocket(["ws", "pose"], token: token))
    }

    func disconnect() {
        liveSocket.disconnect(); eventSocket.disconnect(); poseSocket.disconnect()
    }

    // The server accepts at most 20 client messages per second per socket and drops
    // the rest silently, so button-driven commands are well within budget.
    func send(_ command: CommandName, payload: JSONValue = .object([:])) async throws {
        try await send(CommandMessage(name: command, payload: payload))
    }

    func send(_ command: CommandMessage) async throws {
        try await eventSocket.send(.command(command))
    }
}
