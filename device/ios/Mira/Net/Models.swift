import Foundation

// Section 16 specifies fields but not the event envelope. The development mock
// uses flat, `type`-tagged events. Confirm this contract with Tri's samples.
enum WireJSON {
    static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }

    static func encoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        return encoder
    }
}

// Retains fields for REST responses whose schemas are not defined in the spec,
// arbitrary command payloads, and future event types without losing their data.
enum JSONValue: Codable, Equatable, Sendable {
    case object([String: JSONValue]), array([JSONValue]), string(String)
    case number(Double), bool(Bool), null

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let v = try? c.decode(Bool.self) { self = .bool(v) }
        else if let v = try? c.decode(Double.self) { self = .number(v) }
        else if let v = try? c.decode(String.self) { self = .string(v) }
        else if let v = try? c.decode([JSONValue].self) { self = .array(v) }
        else { self = .object(try c.decode([String: JSONValue].self)) }
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .object(let v): try c.encode(v)
        case .array(let v): try c.encode(v)
        case .string(let v): try c.encode(v)
        case .number(let v): try c.encode(v)
        case .bool(let v): try c.encode(v)
        case .null: try c.encodeNil()
        }
    }
}

struct PoseMessage: Codable, Sendable {
    let t: Double
    let joints: [[Double]]
    let confidence: [Double]
    let loadLPct: Double
    let flags: [String]

    var hasValidDimensions: Bool {
        joints.count == 33 && joints.allSatisfy { $0.count == 3 } && confidence.count == 33
    }
}

struct LiveMessage: Codable, Sendable {
    let phase: String
    let exerciseId: String
    let rep: Int
    let repsTarget: Int
    let countdown: Int?
    let mainAngle: Double?
    let loadLPct: Double
    let flags: [String]
    let cueId: String?
    let paused: Bool
}

struct GreetingMessage: Codable, Sendable { let audioId: String?; let text: String }
struct AwaitingGestureMessage: Codable, Sendable { let gesture: String }
struct GestureMessage: Codable, Sendable { let name: String }
struct CountdownMessage: Codable, Sendable { let n: Int }
struct CueMessage: Codable, Sendable { let id: String; let text: String }
struct RepMessage: Codable, Sendable { let index: Int; let quality: String; let flags: [String] }
struct PausedMessage: Codable, Sendable { let reason: String }
struct AlertMessage: Codable, Sendable { let kind: String; let step: String; let patientId: String }
struct HapticMessage: Codable, Sendable { let pattern: String }
struct HeartRateMessage: Codable, Sendable { let t: Double; let bpm: Double }
struct WristMessage: Codable, Sendable { let t: Double; let quat: [Double]; let accel: [Double] }
struct WatchButtonMessage: Codable, Sendable { let name: String }

enum CommandName: String, Codable, Sendable {
    case start, pause, skip, stop, pain
    case imOK = "im_ok"
}

struct CommandMessage: Codable, Sendable {
    let name: CommandName
    var payload: JSONValue = .object([:])
}

// Both directions share the same envelope. Unknown types survive decoding so a
// server rollout does not break the stream; malformed known events still fail.
enum SocketEvent: Codable, Sendable {
    case arrived, setDone
    case greeting(GreetingMessage), awaitingGesture(AwaitingGestureMessage)
    case gesture(GestureMessage), countdown(CountdownMessage), cue(CueMessage)
    case rep(RepMessage), paused(PausedMessage), alert(AlertMessage), haptic(HapticMessage)
    case command(CommandMessage), hr(HeartRateMessage), wrist(WristMessage)
    case watchButton(WatchButtonMessage)
    case summaryReady(JSONValue), deviceHealth(JSONValue)
    case unknown(type: String, body: JSONValue)

    var type: String {
        switch self {
        case .arrived: return "arrived"
        case .setDone: return "set_done"
        case .greeting: return "greeting"
        case .awaitingGesture: return "awaiting_gesture"
        case .gesture: return "gesture"
        case .countdown: return "countdown"
        case .cue: return "cue"
        case .rep: return "rep"
        case .paused: return "paused"
        case .alert: return "alert"
        case .haptic: return "haptic"
        case .command: return "command"
        case .hr: return "hr"
        case .wrist: return "wrist"
        case .watchButton: return "watch_button"
        case .summaryReady: return "summary_ready"
        case .deviceHealth: return "device_health"
        case .unknown(let type, _): return type
        }
    }

    private enum CodingKeys: String, CodingKey { case type }

    init(from decoder: Decoder) throws {
        let type = try decoder.container(keyedBy: CodingKeys.self).decode(String.self, forKey: .type)
        switch type {
        case "arrived": self = .arrived
        case "set_done": self = .setDone
        case "greeting": self = .greeting(try GreetingMessage(from: decoder))
        case "awaiting_gesture": self = .awaitingGesture(try AwaitingGestureMessage(from: decoder))
        case "gesture": self = .gesture(try GestureMessage(from: decoder))
        case "countdown": self = .countdown(try CountdownMessage(from: decoder))
        case "cue": self = .cue(try CueMessage(from: decoder))
        case "rep": self = .rep(try RepMessage(from: decoder))
        case "paused": self = .paused(try PausedMessage(from: decoder))
        case "alert": self = .alert(try AlertMessage(from: decoder))
        case "haptic": self = .haptic(try HapticMessage(from: decoder))
        case "command": self = .command(try CommandMessage(from: decoder))
        case "hr": self = .hr(try HeartRateMessage(from: decoder))
        case "wrist": self = .wrist(try WristMessage(from: decoder))
        case "watch_button": self = .watchButton(try WatchButtonMessage(from: decoder))
        case "summary_ready": self = .summaryReady(try JSONValue(from: decoder))
        case "device_health": self = .deviceHealth(try JSONValue(from: decoder))
        default: self = .unknown(type: type, body: try JSONValue(from: decoder))
        }
    }

    func encode(to encoder: Encoder) throws {
        switch self {
        case .arrived, .setDone: break
        case .greeting(let v): try v.encode(to: encoder)
        case .awaitingGesture(let v): try v.encode(to: encoder)
        case .gesture(let v): try v.encode(to: encoder)
        case .countdown(let v): try v.encode(to: encoder)
        case .cue(let v): try v.encode(to: encoder)
        case .rep(let v): try v.encode(to: encoder)
        case .paused(let v): try v.encode(to: encoder)
        case .alert(let v): try v.encode(to: encoder)
        case .haptic(let v): try v.encode(to: encoder)
        case .command(let v): try v.encode(to: encoder)
        case .hr(let v): try v.encode(to: encoder)
        case .wrist(let v): try v.encode(to: encoder)
        case .watchButton(let v): try v.encode(to: encoder)
        case .summaryReady(let v), .deviceHealth(let v), .unknown(_, let v): try v.encode(to: encoder)
        }
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(type, forKey: .type)
    }
}

struct CopilotRequest: Codable, Sendable { let question: String }
struct CopilotResponse: Codable, Sendable { let answer: String; let charts: [JSONValue]; let sql: [String] }
struct TherapistCueRequest: Codable, Sendable { let text: String }
enum ProposalAction: String { case approve, edit, dismiss }
