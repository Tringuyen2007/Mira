import Foundation

/// The phone ↔ watch wire contract (Plan 4). Both directions are small, flat
/// dictionaries of JSON-safe scalars (String/Int/Double/Bool) with a `"type"`
/// discriminator, matching how `WCSession.updateApplicationContext`/`sendMessage`
/// carry payloads — no Codable round-trip through `Data` is needed on either
/// side. Foundation-only so it compiles into the macOS `MiraNetworking` package
/// as well as the iOS app target; `WatchBridge` (which imports WatchConnectivity)
/// is the only consumer that touches `WCSession` itself.
enum WatchWire {
    /// Phone → watch: the current `/ws/live` state, trimmed to what the watch face shows.
    struct LiveSnapshot: Codable, Equatable {
        var phase: String
        var rep: Int
        var repsTarget: Int?
        var set: Int?
        var setsTarget: Int?
        var exerciseName: String?
        var hr: Int?
        var paused: Bool
    }

    /// Phone → watch.
    enum ToWatch: Equatable {
        case snapshot(LiveSnapshot)
        case haptic(pattern: String)
        case summary(formScore: Int?, goodReps: Int?, repsTotal: Int?)
    }

    /// Watch → phone.
    enum ToPhone: Equatable {
        case heartRate(bpm: Double, t: Double)
        case button(name: String)
        case status(workoutActive: Bool)
    }

    static func encode(_ m: ToWatch) -> [String: Any] {
        switch m {
        case .snapshot(let s):
            var d: [String: Any] = ["type": "snapshot", "phase": s.phase, "rep": s.rep, "paused": s.paused]
            if let v = s.repsTarget { d["repsTarget"] = v }
            if let v = s.set { d["set"] = v }
            if let v = s.setsTarget { d["setsTarget"] = v }
            if let v = s.exerciseName { d["exerciseName"] = v }
            if let v = s.hr { d["hr"] = v }
            return d
        case .haptic(let pattern):
            return ["type": "haptic", "pattern": pattern]
        case .summary(let formScore, let goodReps, let repsTotal):
            var d: [String: Any] = ["type": "summary"]
            if let v = formScore { d["formScore"] = v }
            if let v = goodReps { d["goodReps"] = v }
            if let v = repsTotal { d["repsTotal"] = v }
            return d
        }
    }

    static func decodeToWatch(_ d: [String: Any]) -> ToWatch? {
        guard let type = d["type"] as? String else { return nil }
        switch type {
        case "snapshot":
            guard let phase = d["phase"] as? String,
                  let rep = d["rep"] as? Int,
                  let paused = d["paused"] as? Bool else { return nil }
            let snapshot = LiveSnapshot(
                phase: phase, rep: rep,
                repsTarget: d["repsTarget"] as? Int,
                set: d["set"] as? Int,
                setsTarget: d["setsTarget"] as? Int,
                exerciseName: d["exerciseName"] as? String,
                hr: d["hr"] as? Int,
                paused: paused
            )
            return .snapshot(snapshot)
        case "haptic":
            guard let pattern = d["pattern"] as? String else { return nil }
            return .haptic(pattern: pattern)
        case "summary":
            return .summary(formScore: d["formScore"] as? Int, goodReps: d["goodReps"] as? Int, repsTotal: d["repsTotal"] as? Int)
        default:
            return nil
        }
    }

    static func encode(_ m: ToPhone) -> [String: Any] {
        switch m {
        case .heartRate(let bpm, let t):
            return ["type": "heartRate", "bpm": bpm, "t": t]
        case .button(let name):
            return ["type": "button", "name": name]
        case .status(let workoutActive):
            return ["type": "status", "workoutActive": workoutActive]
        }
    }

    static func decodeToPhone(_ d: [String: Any]) -> ToPhone? {
        guard let type = d["type"] as? String else { return nil }
        switch type {
        case "heartRate":
            guard let bpm = d["bpm"] as? Double, let t = d["t"] as? Double else { return nil }
            return .heartRate(bpm: bpm, t: t)
        case "button":
            guard let name = d["name"] as? String else { return nil }
            return .button(name: name)
        case "status":
            guard let workoutActive = d["workoutActive"] as? Bool else { return nil }
            return .status(workoutActive: workoutActive)
        default:
            return nil
        }
    }
}
