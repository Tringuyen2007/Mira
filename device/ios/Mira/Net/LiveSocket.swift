import Foundation
import Observation

enum ConnectionState: Equatable {
    case disconnected, connecting, connected, reconnecting(attempt: Int)

    var label: String {
        switch self {
        case .disconnected: return "Mira server offline"
        case .connecting: return "Connecting to Mira…"
        case .connected: return "Mira server online"
        case .reconnecting: return "Mira server offline · reconnecting…"
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
                socket.cancel(with: .goingAway, reason: nil)
                guard !Task.isCancelled, self.generation == generation else { return }
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
    private(set) var decodingError: String?
    private(set) var receivedMessages = 0

    var connectionState: ConnectionState {
        let states = [liveSocket.state, eventSocket.state, poseSocket.state]
        if states.allSatisfy({ $0 == .connected }) { return .connected }
        if let retry = states.first(where: { if case .reconnecting = $0 { return true }; return false }) { return retry }
        if states.contains(.connecting) { return .connecting }
        return .disconnected
    }

    init() {
        liveSocket.onMessage = { [weak self] data in
            self?.decode(data, as: LiveMessage.self) { self?.live = $0 }
        }
        poseSocket.onMessage = { [weak self] data in
            self?.decode(data, as: PoseMessage.self) { pose in
                guard pose.hasValidDimensions else {
                    self?.decodingError = "Invalid pose: expected 33 three-dimensional joints and 33 confidence values."
                    return
                }
                self?.pose = pose
            }
        }
        eventSocket.onMessage = { [weak self] data in
            self?.decode(data, as: SocketEvent.self) { event in
                self?.latestEvent = event
                switch event {
                case .cue(let cue): self?.cue = cue.text
                case .greeting(let greeting): self?.cue = greeting.text
                case .alert(let alert): self?.alert = alert
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

    func connect(to address: ServerAddress, reset: Bool = false) {
        if reset {
            live = nil; pose = nil; cue = nil; latestEvent = nil; alert = nil
            receivedMessages = 0; decodingError = nil
        }
        liveSocket.connect(to: address.endpoint(["ws", "live"], webSocket: true))
        eventSocket.connect(to: address.endpoint(["ws", "events"], webSocket: true))
        poseSocket.connect(to: address.endpoint(["ws", "pose"], webSocket: true))
    }

    func disconnect() {
        liveSocket.disconnect(); eventSocket.disconnect(); poseSocket.disconnect()
    }

    func send(_ command: CommandName, payload: JSONValue = .object([:])) async throws {
        try await eventSocket.send(.command(CommandMessage(name: command, payload: payload)))
    }
}
