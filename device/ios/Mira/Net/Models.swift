import Foundation

// Wire contract of Tri's server (README "Try it" + server/samples/*.json, which the
// tests decode as fixtures). Every server message is a flat JSON object; /ws/events
// messages carry a `type` discriminator. Snake-case keys map to camelCase properties.
// The server adds `t`, `seq` and `session_id` to most events; fields the app does not
// need are ignored rather than modelled.
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

    /// The hub sends `{"type":"ping"}` on every channel (also /ws/live and /ws/pose)
    /// after 10 s without traffic. It is a keep-alive, not data.
    static func isPing(_ data: Data) -> Bool {
        struct Probe: Decodable { let type: String? }
        guard data.count <= 64 else { return false }
        return (try? JSONDecoder().decode(Probe.self, from: data))?.type == "ping"
    }
}

// Dates in the wire contract are ISO-8601 strings, with or without fractional seconds
// (`created_at`/`read_at` on messages have fractional seconds; `time` on milestones and
// `day` on progress rows do not). One shared parser avoids every call site repeating
// the two-formatter fallback.
enum WireDates {
    private static let withFractionalSeconds: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()
    private static let whole: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    static func parse(_ string: String) -> Date? {
        withFractionalSeconds.date(from: string) ?? whole.date(from: string)
    }
}

// Lossless JSON for genuinely free-form server data: command payloads, prescription
// targets/limits, proposal evidence/changes, Copilot charts, coach_adapt details and
// event types this app version does not know yet.
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

// MARK: - Streams

/// /ws/pose: the server's subset of an engine frame, ~25 Hz (server/samples/ws_pose.json).
struct PoseMessage: Codable, Sendable {
    let t: Double
    let joints: [[Double]]
    let confidence: [Double]
    let loadLPct: Double?
    let flags: [String]

    var hasValidDimensions: Bool {
        joints.count == 33 && joints.allSatisfy { $0.count == 3 } && confidence.count == 33
    }
}

/// /ws/live: session state, ~10 Hz (server/samples/ws_live.json). Most fields are null
/// while idle, before the first frame or between exercises.
struct LiveMessage: Codable, Sendable {
    var t: Double?
    let phase: String  // idle | countdown | active | rest | paused | incident
    let exerciseId: String?
    var exerciseName: String?
    let rep: Int
    let repsTarget: Int?
    var set: Int?
    var setsTarget: Int?
    let countdown: Int?
    let mainAngle: Double?  // knee flexion in degrees, 0 = straight
    let loadLPct: Double?
    var loadConf: Double?
    var hr: Int?
    let flags: [String]
    let cueId: String?
    let paused: Bool
    // W1: `caption` mirrors the server's actual `{text, source, t}` shape (server/engine_bridge.py
    // `EngineBridge.caption`) — the watch only ever sees `caption.text`, relayed as the flat
    // `ToWatch.caption(text:)`. Both fields optional since the server may omit them.
    var caption: LiveCaption? = nil
    var restRemaining: Int? = nil
}

/// What Mira last said on `/ws/live` (late joiners repaint it): `source` ∈ cue | greeting | summary.
struct LiveCaption: Codable, Sendable { let text: String; let source: String?; let t: Double? }

// MARK: - /ws/events, server → app

struct ArrivedMessage: Codable, Sendable { let patientId: String? }
struct GreetingMessage: Codable, Sendable { let text: String; let audioUrl: String? }
struct AwaitingGestureMessage: Codable, Sendable { let gesture: String }
struct GestureMessage: Codable, Sendable { let name: String }
struct CountdownMessage: Codable, Sendable {
    let n: Int
    let set: Int?, repsTarget: Int?, setsTarget: Int?, exerciseId: String?
}
/// `audio_url` is a server path such as `/audio/set_start.mp3` (resolve against the server address).
struct CueMessage: Codable, Sendable { let id: String; let text: String; let audioUrl: String? }
struct RepMessage: Codable, Sendable {
    let index: Int
    let set: Int?, exerciseId: String?
    let quality: String  // good | fix_form
    let flags: [String]
}
struct SetDoneMessage: Codable, Sendable { let set: Int?; let reps: Int?; let exerciseId: String? }
struct PausedMessage: Codable, Sendable { let reason: String? }
struct HapticMessage: Codable, Sendable { let pattern: String }
struct FallMessage: Codable, Sendable {
    let stage: String
    let dropM: Double?, peakDownSpeedMps: Double?, posture: String?, onMat: Bool?
}
struct FallStatusMessage: Codable, Sendable {
    let posture: String?, moving: Bool?, stillS: Double?, onMat: Bool?
}
struct FatigueMessage: Codable, Sendable { let level: String; let exerciseId: String?; let set: Int?; let rep: Int? }
/// Every Safety Guardian step (detected, checked_in, escalated, call_*, closed, resolved…).
struct AlertMessage: Codable, Sendable {
    let alertId: String
    let kind: String  // fall | pain | hr
    let step: String
    let patientId: String
    let sessionId: String?
    let message: String?
    let showCall911: Bool?  // wire key `show_call_911`

    var isOpen: Bool { !["closed", "resolved", "resolved_by_therapist"].contains(step) }
}
struct SummaryReadyMessage: Codable, Sendable {
    let sessionId: String?
    let set: Int?
    let spoken: String
    let therapistNote: String?
    let audioUrl: String?
}
/// A whole-session AI analysis, voiced once at session end (`SessionAnalyst`).
struct Highlight: Codable, Sendable { let kind: String?; let text: String }
struct FlaggedMoment: Codable, Sendable {
    let set: Int?
    let rep: Int?
    let ruleId: String
    let label: String?
    let severity: String?
    let t: Double?
}
struct SessionSummaryMessage: Codable, Sendable {
    let sessionId: String?
    let patientId: String?
    let exerciseId: String?
    let exerciseName: String?
    let formScore: Int?
    let repsTotal: Int?
    let goodReps: Int?
    let setsDone: Int?
    let spoken: String
    let note: String?
    let highlights: [Highlight]
    let nextFocus: String?
    let flaggedMoments: [FlaggedMoment]
    let comparison: JSONValue?
    let audioUrl: String?
    let source: String?
}
/// device_status: a device (lcd, watch…) reporting online/offline on /ws/events.
struct DeviceStatusMessage: Codable, Sendable { let device: String?; let online: Bool }
struct CoachAdaptMessage: Codable, Sendable { let action: String; let detail: JSONValue? }
struct ProposalCreatedMessage: Codable, Sendable { let proposalId: Int; let patientId: String; let agent: String? }
struct ProposalDecidedMessage: Codable, Sendable {
    let proposalId: Int
    let patientId: String
    let status: String
    let decidedBy: String?
    let exerciseId: String?
    let prescriptionVersion: Int?
}
/// A care message (therapist note, therapist message, or the patient's own message),
/// pushed live on /ws/events when a therapist posts via `POST /patients/{id}/messages`
/// (server/samples/ws_events_message.json, rest_get_me_care.json "notes").
struct CareMessage: Codable, Sendable, Identifiable {
    let id: Int
    let sender: String  // patient | therapist | mira
    let kind: String     // message | note | visit_highlight
    let body: String
    let createdAt: String
    let readAt: String?
}
struct MessageEvent: Codable, Sendable { let patientId: String?; let message: CareMessage }

// MARK: - Apple Watch events (server → clients; server/samples/ws_events_{breather,hr_recovery,
// rotation_rep,rotation_set,sync_progress,limits_changed,watch_features_changed}.json)

struct BreatherMessage: Codable, Sendable { let state: String; let bpm: Int?; let limit: Int?; let resolution: String? }
struct HrRecoveryMessage: Codable, Sendable {
    let setIndex: Int; let peakBpm: Int; let bpm60s: Int; let dropBpm: Int

    // `convertFromSnakeCase` turns the wire's "bpm_60s" into "bpm60S" (capitalizing the
    // letter after a digit run), not "bpm60s" — spell that out explicitly rather than
    // rename the property away from the exact name later tasks depend on.
    enum CodingKeys: String, CodingKey {
        case setIndex, peakBpm
        case bpm60s = "bpm60S"
        case dropBpm
    }
}
struct RotationRepMessage: Codable, Sendable {
    let exerciseId: String; let setIndex: Int?; let rep: Int; let repsTarget: Int
    let angleDeg: Double; let goalDeg: Double; let reached: Bool
}
struct RotationSetMessage: Codable, Sendable {
    let exerciseId: String; let setIndex: Int; let angles: [Double]
    let bestDeg: Double; let avgDeg: Double; let goalDeg: Double
}
/// Watch⇄server clock phase-lock progress (derived from the client's `watch_sync` windows).
struct SyncProgressMessage: Codable, Sendable { let progress: Double; let locked: Bool; let offsetMs: Int?; let method: String? }
struct LimitsChangedMessage: Codable, Sendable { let patientId: String?; let hrLimit: Int; let setByName: String? }
struct WatchFeaturesChangedMessage: Codable, Sendable { let patientId: String?; let tremorMonitoring: Bool }

// MARK: - /ws/events, app → server (server allow-list: command, hr, wrist, watch_button, device_health)

struct HeartRateMessage: Codable, Sendable { let t: Double; let bpm: Double }
/// `rotationDeg` (optional, `rotation_deg` on the wire) is the watch's measured twist;
/// omitted from the encoded JSON entirely when nil (Plan-4 wrist messages carry none).
struct WristMessage: Codable, Sendable { let t: Double; let quat: [Double]; let accel: [Double]; var rotationDeg: Double? = nil }
struct WatchButtonMessage: Codable, Sendable { let name: String }
/// Client → server: one window/second of user-acceleration magnitude, used only to
/// phase-lock the watch's clock to the server's (never broadcast back; see `sync_progress`).
struct WatchSyncMessage: Codable, Sendable { let t0: Double; let dtMs: Int; let accelMag: [Double] }
/// Sent by the app/watch (`t`, `battery_pct`, `app`) and by the engine (`cameras`, `fps`).
struct DeviceHealthMessage: Codable, Sendable {
    var t: Double?
    var batteryPct: Double?
    var app: String?
    var cameras: Int?
    var fps: Double?
}

enum CommandName: String, Codable, Sendable {
    case start, pause, skip, stop, pain
    case imOK = "im_ok"
}

struct CommandMessage: Codable, Sendable {
    let name: CommandName
    var payload: JSONValue = .object([:])

    /// The Safety Guardian opens an incident for `pain` with `payload.level >= 7` (0–10).
    static func pain(level: Int) -> CommandMessage {
        CommandMessage(name: .pain, payload: .object(["level": .number(Double(level))]))
    }
}

// Both directions share the same envelope. Unknown types survive decoding so a
// server rollout does not break the stream; malformed known events still fail.
enum SocketEvent: Codable, Sendable {
    case arrived(ArrivedMessage), setDone(SetDoneMessage), resumed, ping
    case greeting(GreetingMessage), awaitingGesture(AwaitingGestureMessage)
    case gesture(GestureMessage), countdown(CountdownMessage), cue(CueMessage)
    case rep(RepMessage), paused(PausedMessage), alert(AlertMessage), haptic(HapticMessage)
    case fall(FallMessage), fallStatus(FallStatusMessage), fatigue(FatigueMessage)
    case summaryReady(SummaryReadyMessage), coachAdapt(CoachAdaptMessage)
    case proposalCreated(ProposalCreatedMessage), proposalDecided(ProposalDecidedMessage)
    case command(CommandMessage), hr(HeartRateMessage), wrist(WristMessage)
    case watchButton(WatchButtonMessage), deviceHealth(DeviceHealthMessage)
    case sessionSummary(SessionSummaryMessage), deviceStatus(DeviceStatusMessage)
    case message(MessageEvent)
    case watchSync(WatchSyncMessage), breather(BreatherMessage), hrRecovery(HrRecoveryMessage)
    case rotationRep(RotationRepMessage), rotationSet(RotationSetMessage), syncProgress(SyncProgressMessage)
    case limitsChanged(LimitsChangedMessage), watchFeaturesChanged(WatchFeaturesChangedMessage)
    case unknown(type: String, body: JSONValue)

    var type: String {
        switch self {
        case .arrived: return "arrived"
        case .setDone: return "set_done"
        case .resumed: return "resumed"
        case .ping: return "ping"
        case .greeting: return "greeting"
        case .awaitingGesture: return "awaiting_gesture"
        case .gesture: return "gesture"
        case .countdown: return "countdown"
        case .cue: return "cue"
        case .rep: return "rep"
        case .paused: return "paused"
        case .alert: return "alert"
        case .haptic: return "haptic"
        case .fall: return "fall"
        case .fallStatus: return "fall_status"
        case .fatigue: return "fatigue"
        case .summaryReady: return "summary_ready"
        case .coachAdapt: return "coach_adapt"
        case .proposalCreated: return "proposal_created"
        case .proposalDecided: return "proposal_decided"
        case .command: return "command"
        case .hr: return "hr"
        case .wrist: return "wrist"
        case .watchButton: return "watch_button"
        case .deviceHealth: return "device_health"
        case .sessionSummary: return "session_summary"
        case .deviceStatus: return "device_status"
        case .message: return "message"
        case .watchSync: return "watch_sync"
        case .breather: return "breather"
        case .hrRecovery: return "hr_recovery"
        case .rotationRep: return "rotation_rep"
        case .rotationSet: return "rotation_set"
        case .syncProgress: return "sync_progress"
        case .limitsChanged: return "limits_changed"
        case .watchFeaturesChanged: return "watch_features_changed"
        case .unknown(let type, _): return type
        }
    }

    private enum CodingKeys: String, CodingKey { case type }

    init(from decoder: Decoder) throws {
        let type = try decoder.container(keyedBy: CodingKeys.self).decode(String.self, forKey: .type)
        switch type {
        case "arrived": self = .arrived(try ArrivedMessage(from: decoder))
        case "set_done": self = .setDone(try SetDoneMessage(from: decoder))
        case "resumed": self = .resumed
        case "ping": self = .ping
        case "greeting": self = .greeting(try GreetingMessage(from: decoder))
        case "awaiting_gesture": self = .awaitingGesture(try AwaitingGestureMessage(from: decoder))
        case "gesture": self = .gesture(try GestureMessage(from: decoder))
        case "countdown": self = .countdown(try CountdownMessage(from: decoder))
        case "cue": self = .cue(try CueMessage(from: decoder))
        case "rep": self = .rep(try RepMessage(from: decoder))
        case "paused": self = .paused(try PausedMessage(from: decoder))
        case "alert": self = .alert(try AlertMessage(from: decoder))
        case "haptic": self = .haptic(try HapticMessage(from: decoder))
        case "fall": self = .fall(try FallMessage(from: decoder))
        case "fall_status": self = .fallStatus(try FallStatusMessage(from: decoder))
        case "fatigue": self = .fatigue(try FatigueMessage(from: decoder))
        case "summary_ready": self = .summaryReady(try SummaryReadyMessage(from: decoder))
        case "coach_adapt": self = .coachAdapt(try CoachAdaptMessage(from: decoder))
        case "proposal_created": self = .proposalCreated(try ProposalCreatedMessage(from: decoder))
        case "proposal_decided": self = .proposalDecided(try ProposalDecidedMessage(from: decoder))
        case "command": self = .command(try CommandMessage(from: decoder))
        case "hr": self = .hr(try HeartRateMessage(from: decoder))
        case "wrist": self = .wrist(try WristMessage(from: decoder))
        case "watch_button": self = .watchButton(try WatchButtonMessage(from: decoder))
        case "device_health": self = .deviceHealth(try DeviceHealthMessage(from: decoder))
        case "session_summary": self = .sessionSummary(try SessionSummaryMessage(from: decoder))
        case "device_status": self = .deviceStatus(try DeviceStatusMessage(from: decoder))
        case "message": self = .message(try MessageEvent(from: decoder))
        case "watch_sync": self = .watchSync(try WatchSyncMessage(from: decoder))
        case "breather": self = .breather(try BreatherMessage(from: decoder))
        case "hr_recovery": self = .hrRecovery(try HrRecoveryMessage(from: decoder))
        case "rotation_rep": self = .rotationRep(try RotationRepMessage(from: decoder))
        case "rotation_set": self = .rotationSet(try RotationSetMessage(from: decoder))
        case "sync_progress": self = .syncProgress(try SyncProgressMessage(from: decoder))
        case "limits_changed": self = .limitsChanged(try LimitsChangedMessage(from: decoder))
        case "watch_features_changed": self = .watchFeaturesChanged(try WatchFeaturesChangedMessage(from: decoder))
        default: self = .unknown(type: type, body: try JSONValue(from: decoder))
        }
    }

    func encode(to encoder: Encoder) throws {
        switch self {
        case .resumed, .ping: break
        case .arrived(let v): try v.encode(to: encoder)
        case .setDone(let v): try v.encode(to: encoder)
        case .greeting(let v): try v.encode(to: encoder)
        case .awaitingGesture(let v): try v.encode(to: encoder)
        case .gesture(let v): try v.encode(to: encoder)
        case .countdown(let v): try v.encode(to: encoder)
        case .cue(let v): try v.encode(to: encoder)
        case .rep(let v): try v.encode(to: encoder)
        case .paused(let v): try v.encode(to: encoder)
        case .alert(let v): try v.encode(to: encoder)
        case .haptic(let v): try v.encode(to: encoder)
        case .fall(let v): try v.encode(to: encoder)
        case .fallStatus(let v): try v.encode(to: encoder)
        case .fatigue(let v): try v.encode(to: encoder)
        case .summaryReady(let v): try v.encode(to: encoder)
        case .coachAdapt(let v): try v.encode(to: encoder)
        case .proposalCreated(let v): try v.encode(to: encoder)
        case .proposalDecided(let v): try v.encode(to: encoder)
        case .command(let v): try v.encode(to: encoder)
        case .hr(let v): try v.encode(to: encoder)
        case .wrist(let v): try v.encode(to: encoder)
        case .watchButton(let v): try v.encode(to: encoder)
        case .deviceHealth(let v): try v.encode(to: encoder)
        case .sessionSummary(let v): try v.encode(to: encoder)
        case .deviceStatus(let v): try v.encode(to: encoder)
        case .message(let v): try v.encode(to: encoder)
        case .watchSync(let v): try v.encode(to: encoder)
        case .breather(let v): try v.encode(to: encoder)
        case .hrRecovery(let v): try v.encode(to: encoder)
        case .rotationRep(let v): try v.encode(to: encoder)
        case .rotationSet(let v): try v.encode(to: encoder)
        case .syncProgress(let v): try v.encode(to: encoder)
        case .limitsChanged(let v): try v.encode(to: encoder)
        case .watchFeaturesChanged(let v): try v.encode(to: encoder)
        case .unknown(_, let v): try v.encode(to: encoder)
        }
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(type, forKey: .type)
    }

    /// Types the server accepts from a client on /ws/events (server/main.py
    /// CLIENT_EVENT_TYPES); anything else is silently dropped by the server.
    static let clientTypes: Set<String> = ["command", "hr", "wrist", "watch_button", "device_health", "watch_sync"]
}

// MARK: - REST (server/samples/rest_*.json)

struct HealthStatus: Codable, Sendable { let status: String; let time: Double? }

/// Placeholder until the vision engine reports calibration (fields fixed, contents vary).
struct CalibrationStatus: Codable, Sendable {
    let status: String
    let calibrated: Bool
    let cameras: [JSONValue]
    let errors: JSONValue?
    let note: String?
}

struct SessionRequest: Codable, Sendable { var patientId: String?; var exerciseId: String? }
struct SessionStarted: Codable, Sendable {
    let sessionId: String
    let patientId: String
    let exerciseId: String
    let startedAt: Double
    let firstName: String?
}
struct SessionStopped: Codable, Sendable { let sessionId: String; let stopped: Bool }
struct SessionShared: Codable, Sendable { let sessionId: String; let shared: Bool }

struct SessionRecord: Codable, Sendable {
    let id: String
    let patientId: String
    let exerciseId: String
    let startedAt: String
    let endedAt: String?
    let repsDone: Int?
    let painPre: Int?, painPost: Int?, effort: Int?
    let shared: Bool
    let aiSummary: String?, aiNote: String?
}
struct RepRecord: Codable, Sendable {
    let rep: Int
    let setNo: Int?
    let peakAngle: Double?
    let quality: Double?
    let flags: [String]
}
struct SessionSummary: Codable, Sendable {
    let repsTotal: Int
    let goodReps: Int
    let flagCounts: [String: Int]
    let aiSummary: String?, aiNote: String?, aiNoteLabel: String?
}
struct SetSummary: Codable, Sendable { let setNo: Int; let twLoadLeftPct: Double?; let reps: Int; let goodReps: Int }

/// The whole-session AI analysis as stored (same shape as `SessionSummaryMessage`
/// minus `t`, plus `createdAt`).
struct SessionAnalysis: Codable, Sendable {
    let sessionId: String?
    let createdAt: String?
    let formScore: Int?
    let repsTotal: Int?
    let goodReps: Int?
    let setsDone: Int?
    let spoken: String
    let note: String?
    let highlights: [Highlight]
    let nextFocus: String?
    let flaggedMoments: [FlaggedMoment]
    let comparison: JSONValue?
    let audioUrl: String?
    let source: String?
}
struct SessionDetail: Codable, Sendable {
    let session: SessionRecord
    let exerciseName: String?
    let analysis: SessionAnalysis?
    let reps: [RepRecord]
    let summary: SessionSummary?
    let twLoadLeftPct: Double?
    let perSet: [SetSummary]?
}

/// GET /patients/{id}/sessions — a lightweight session list for the Progress/Care screens.
struct SessionListItem: Codable, Sendable {
    let id: String
    let exerciseId: String
    let exerciseName: String?
    let startedAt: String
    let endedAt: String?
    let repsDone: Int?
    let painPost: Int?
    let effort: Int?
    let shared: Bool
    let formScore: Int?
    let goodReps: Int?
    let repsTotal: Int?
    let spokenPreview: String?
}
struct SessionList: Codable, Sendable { let patientId: String; let sessions: [SessionListItem] }

/// POST /sessions/{id}/checkin — pain/effort/note collected on the Summary screen.
struct CheckinRequest: Codable, Sendable { var painPost: Int?; var effort: Int?; var note: String? }
struct CheckinResponse: Codable, Sendable { let sessionId: String; let painPost: Int?; let effort: Int?; let noteSaved: Bool }

/// `downsample > 0`: `mode = "lttb"` with `points`; `downsample = 0`: `mode = "full"`
/// with `frames` and `next_t0` (request again with `t0 = next_t0` until it is null).
struct SessionFrames: Codable, Sendable {
    struct Point: Codable, Sendable { let time: String; let value: Double? }
    struct Frame: Codable, Sendable {
        let time: String
        let loadLPct: Double?, kneeL: Double?, kneeR: Double?
        let setNo: Int?
        let joints: [[Double]]?
    }
    let sessionId: String
    let mode: String
    let series: String?
    let points: [Point]?
    let count: Int?
    let nextT0: String?
    let frames: [Frame]?
}

struct ProgressDay: Codable, Sendable {
    let day: String
    let exerciseId: String?
    let maxKnee: Double?, p95Knee: Double?
    let avgLoadLeft: Double?, twLoadLeft: Double?, affectedLoadPct: Double?
    let avgAsymmetry: Double?, avgTrunkLean: Double?, avgConfidence: Double?
    let frames: Int?
}
struct PatientProgress: Codable, Sendable {
    let patientId: String
    let exerciseId: String?
    let days: Int
    let source: String?
    let series: [ProgressDay]
}

struct Prescription: Codable, Sendable {
    let id: Int
    let patientId: String
    let exerciseId: String
    let version: Int
    let sets: Int?
    let reps: Int?
    let targets: JSONValue?  // free-form per exercise (knee_deg, hold_s, add…)
    let limits: JSONValue?
    let active: Bool
    let createdBy: String?
    let createdAt: String?
}
struct PrescriptionList: Codable, Sendable { let patientId: String; let prescriptions: [Prescription] }

struct Proposal: Codable, Sendable {
    let id: Int
    let patientId: String
    let createdAt: String
    let agent: String
    let rationale: String
    let evidence: JSONValue?
    let change: JSONValue?
    let status: String  // pending | approved | edited | dismissed
    let decidedBy: String?
    let decidedAt: String?
    let patientName: String?
}
struct ProposalList: Codable, Sendable { let proposals: [Proposal] }
struct ProposalDecisionRequest: Codable, Sendable {
    let therapistId: String
    var change: JSONValue?  // edit only
    var reason: String?     // dismiss only
}
struct ProposalDecision: Codable, Sendable { let proposal: Proposal; let prescription: Prescription? }
enum ProposalAction: String { case approve, edit, dismiss }

struct AlertStep: Codable, Sendable { let time: String; let step: String; let channel: String?; let payload: JSONValue? }
struct AlertRecord: Codable, Sendable {
    let alertId: String
    let patientId: String
    let sessionId: String?
    let kind: String
    let startedAt: String
    let steps: [AlertStep]
    let lastStep: String
    let updatedAt: String?
    let open: Bool
}
struct AlertList: Codable, Sendable { let alerts: [AlertRecord] }
struct AlertResolveRequest: Codable, Sendable { var resolvedBy: String = "therapist"; var note: String? }
struct AlertResolution: Codable, Sendable { let alertId: String; let step: String; let time: Double }

struct NotificationCount: Codable, Sendable { let unread: Int; let urgentUnacked: Int }

struct CopilotRequest: Codable, Sendable { let question: String; var patientId: String? }
struct CopilotQuery: Codable, Sendable { let query: String; let rows: Int?; let ms: Double? }
struct CopilotResponse: Codable, Sendable {
    let answer: String
    let charts: [JSONValue]
    let evidence: [String]?
    let sql: [CopilotQuery]
    let cached: Bool?
    let proposals: [Int]?
    let fallback: String?
}

// MARK: - Pairing and /me (Plan 1; server/samples/rest_post_pair.json, rest_get_me.json)

struct PairProfile: Codable, Sendable {
    let name: String
    let heightCm: Double
    let massKg: Double
    var affectedSide: String?   // "left" | "right" | "none"
    var condition: String?
}
struct PairContact: Codable, Sendable, Equatable {
    let name: String
    let phone: String
    var relationship: String?
}
struct PairRequest: Codable, Sendable {
    let code: String
    var deviceName: String?
    let profile: PairProfile
    var emergencyContact: PairContact?
    var alertTherapist: Bool = true
}
struct PairResponse: Codable, Sendable {
    let patientId: String
    let token: String
    let me: MeResponse
}

struct MePatient: Codable, Sendable {
    let id: String
    let name: String
    let firstName: String
    let age: Int?
    let heightCm: Double?, massKg: Double?
    let affectedSide: String?, condition: String?
    let hrLimit: Int?
    let emergencyContact: PairContact?
    let alertTherapist: Bool
    let shareByDefault: Bool
    let devicePrefs: JSONValue?
    let nextVisitAt: String?
}
struct MeTherapist: Codable, Sendable { let id: String; let name: String; let clinic: String?; let phone: String? }
struct MePrescription: Codable, Sendable, Identifiable {
    var id: String { exerciseId }
    let exerciseId: String
    let exerciseName: String
    let sets: Int?
    let reps: Int?
    let targets: JSONValue?
    let limits: JSONValue?
    let version: Int?
    let metricLabel: String?, unit: String?, targetKey: String?
}
struct LcdStatus: Codable, Sendable { let online: Bool; let lastSeen: Double? }
struct MeResponse: Codable, Sendable {
    let patient: MePatient
    let therapist: MeTherapist?
    let prescriptions: [MePrescription]
    let lcd: LcdStatus?
    let calibration: CalibrationStatus?
}

struct MeProfilePatch: Codable, Sendable {
    var name: String?
    var heightCm: Double?, massKg: Double?
    var affectedSide: String?, condition: String?
}
struct MeUpdate: Codable, Sendable {
    var profile: MeProfilePatch?
    var emergencyContact: PairContact?
    var alertTherapist: Bool?
    var shareByDefault: Bool?
    var devicePrefs: JSONValue?
}

// MARK: - Care, messaging, milestones (Plan 3; server/samples/rest_get_me_care.json,
// rest_get_me_messages.json, rest_post_me_messages.json, rest_get_patient_milestones.json)

/// A highlight on the Care screen: either a session analysis highlight (`source == nil`)
/// or a memory-event insight (`source == "ai"`).
struct CareHighlight: Codable, Sendable { let kind: String?; let text: String; let time: String?; let source: String? }

/// GET /me/care.
struct CareResponse: Codable, Sendable {
    let therapist: MeTherapist?
    let nextVisitAt: String?
    let hrLimit: Int?
    let emergencyContact: PairContact?
    let alertTherapist: Bool
    let notes: [CareMessage]
    let highlights: [CareHighlight]
    let unreadMessages: Int
}

/// GET /me/messages.
struct MessageList: Codable, Sendable { let messages: [CareMessage] }
/// POST /me/messages and POST /patients/{id}/messages.
struct MessageSendRequest: Codable, Sendable { let body: String }
struct SentMessage: Codable, Sendable { let message: CareMessage }

/// GET /patients/{id}/milestones.
struct Milestone: Codable, Sendable, Identifiable {
    let id: Int
    let time: String
    let message: String
    let evidence: JSONValue?
}
struct MilestoneList: Codable, Sendable { let patientId: String; let milestones: [Milestone] }

/// POST /me/test-alert.
struct TestAlertResponse: Codable, Sendable { let logged: Bool; let called: Bool; let note: String }

// MARK: - Apple Watch setup + tremor (Plan 4/W1; server/samples/rest_get_me_watch.json,
// rest_post_me_tremor_request.json)

/// Defaults when unset: `watchTaps` "normal" (normal|strong), `watchArm` "right" (left|right),
/// `watchDoubleTap`/`watchCaptions` true.
struct MeWatchPrefs: Codable, Sendable { var watchTaps: String?; var watchArm: String?; var watchDoubleTap: Bool?; var watchCaptions: Bool? }
struct MeWatchPlanItem: Codable, Sendable {
    let exerciseId: String; let name: String; let sets: Int; let reps: Int
    let doneSets: Int; let watch: Bool; let goalDeg: Double?
}
/// GET /me/watch.
struct MeWatchResponse: Codable, Sendable {
    let firstName: String
    let hrLimit: Int?
    let hrLimitSetByName: String?
    let therapistName: String?
    let tremorMonitoring: Bool
    let prefs: MeWatchPrefs
    let plan: [MeWatchPlanItem]
}

/// `dyskinesia` ∈ none|few|some|many; `source` ∈ device|simulated.
struct TremorDayBody: Codable, Sendable {
    let day: String
    let noneMin, slightMin, mildMin, strongMin, unknownMin: Int
    let dyskinesia: String
    let source: String
}
/// POST /me/tremor request body (≤ 14 days; 403 when `tremorMonitoring` is off).
struct TremorUpload: Codable, Sendable { let days: [TremorDayBody] }
struct TremorStored: Codable, Sendable { let stored: Int }
