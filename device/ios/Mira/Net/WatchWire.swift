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
        // W1: rotation/rest extras, all optional so Plan-4 call sites keep compiling.
        var exerciseId: String? = nil
        var goodReps: Int? = nil
        var restRemaining: Int? = nil
        var countdown: Int? = nil
        var isWatchExercise: Bool? = nil
    }

    /// Phone → watch, from `/me/watch` (sent once as the setup application context).
    struct WatchPrefs: Equatable {
        var taps: String
        var arm: String
        var doubleTap: Bool
        var captions: Bool

        static let defaults = WatchPrefs(taps: "normal", arm: "right", doubleTap: true, captions: true)
    }

    struct PlanItem: Equatable {
        var id: String
        var name: String
        var sets: Int
        var reps: Int
        var doneSets: Int
        var watch: Bool
        var goalDeg: Double?
    }

    struct Setup: Equatable {
        var firstName: String
        var hrLimit: Int?
        var hrLimitSetBy: String?
        var therapistName: String?
        var tremorMonitoring: Bool
        var prefs: WatchPrefs
        var plan: [PlanItem]
    }

    /// Watch → phone, mirrors `POST /me/tremor`'s request body (camelCase on the wire here).
    struct TremorDay: Equatable {
        var day: String
        var noneMin, slightMin, mildMin, strongMin, unknownMin: Int
        var dyskinesia: String
        var source: String
    }

    /// Phone → watch.
    enum ToWatch: Equatable {
        case snapshot(LiveSnapshot)
        case haptic(pattern: String)
        case summary(formScore: Int?, goodReps: Int?, repsTotal: Int?)
        case setup(Setup)
        case breather(state: String, bpm: Int?, limit: Int?)
        case recovery(setIndex: Int, peakBpm: Int, bpm60: Int, drop: Int)
        case rotationRep(exerciseId: String, rep: Int, repsTarget: Int, angle: Double, goal: Double, reached: Bool)
        case rotationSet(exerciseId: String, setIndex: Int, angles: [Double], best: Double, avg: Double, goal: Double)
        case sync(progress: Double, locked: Bool)
        case caption(text: String)
        case formCue(text: String, rep: Int?)
    }

    /// Watch → phone.
    enum ToPhone: Equatable {
        case heartRate(bpm: Double, t: Double)
        case button(name: String)
        case status(workoutActive: Bool)
        case wrist(t: Double, quat: [Double], accel: [Double], deg: Double?)
        case syncWindow(t0: Double, dtMs: Int, mags: [Double])
        case tremor(days: [TremorDay])
        case prefs(taps: String?, arm: String?, doubleTap: Bool?, captions: Bool?)
        case setupDone
    }

    // MARK: - Numeric bridging
    // WatchConnectivity dictionaries cross an NSNumber boundary: a value sent as an Int
    // can arrive typed as a Double (and vice versa). These tolerate either.

    private static func int(_ v: Any?) -> Int? {
        if let i = v as? Int { return i }
        if let d = v as? Double { return Int(d) }
        if let n = v as? NSNumber { return n.intValue }
        return nil
    }

    private static func double(_ v: Any?) -> Double? {
        if let d = v as? Double { return d }
        if let i = v as? Int { return Double(i) }
        if let n = v as? NSNumber { return n.doubleValue }
        return nil
    }

    private static func doubleArray(_ v: Any?) -> [Double]? {
        if let arr = v as? [Double] { return arr }
        guard let arr = v as? [Any] else { return nil }
        var out: [Double] = []
        for item in arr {
            guard let d = double(item) else { return nil }
            out.append(d)
        }
        return out
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
            if let v = s.exerciseId { d["exerciseId"] = v }
            if let v = s.goodReps { d["goodReps"] = v }
            if let v = s.restRemaining { d["restRemaining"] = v }
            if let v = s.countdown { d["countdown"] = v }
            if let v = s.isWatchExercise { d["isWatchExercise"] = v }
            return d
        case .haptic(let pattern):
            return ["type": "haptic", "pattern": pattern]
        case .summary(let formScore, let goodReps, let repsTotal):
            var d: [String: Any] = ["type": "summary"]
            if let v = formScore { d["formScore"] = v }
            if let v = goodReps { d["goodReps"] = v }
            if let v = repsTotal { d["repsTotal"] = v }
            return d
        case .setup(let s):
            let plan: [[String: Any]] = s.plan.map { item in
                var pd: [String: Any] = [
                    "id": item.id, "name": item.name, "sets": item.sets, "reps": item.reps,
                    "doneSets": item.doneSets, "watch": item.watch,
                ]
                if let v = item.goalDeg { pd["goalDeg"] = v }
                return pd
            }
            var d: [String: Any] = [
                "type": "setup",
                "firstName": s.firstName,
                "tremorMonitoring": s.tremorMonitoring,
                "prefs": [
                    "taps": s.prefs.taps, "arm": s.prefs.arm,
                    "doubleTap": s.prefs.doubleTap, "captions": s.prefs.captions,
                ],
                "plan": plan,
            ]
            if let v = s.hrLimit { d["hrLimit"] = v }
            if let v = s.hrLimitSetBy { d["hrLimitSetBy"] = v }
            if let v = s.therapistName { d["therapistName"] = v }
            return d
        case .breather(let state, let bpm, let limit):
            var d: [String: Any] = ["type": "breather", "state": state]
            if let v = bpm { d["bpm"] = v }
            if let v = limit { d["limit"] = v }
            return d
        case .recovery(let setIndex, let peakBpm, let bpm60, let drop):
            return ["type": "recovery", "setIndex": setIndex, "peakBpm": peakBpm, "bpm60": bpm60, "drop": drop]
        case .rotationRep(let exerciseId, let rep, let repsTarget, let angle, let goal, let reached):
            return [
                "type": "rotationRep", "exerciseId": exerciseId, "rep": rep, "repsTarget": repsTarget,
                "angle": angle, "goal": goal, "reached": reached,
            ]
        case .rotationSet(let exerciseId, let setIndex, let angles, let best, let avg, let goal):
            return [
                "type": "rotationSet", "exerciseId": exerciseId, "setIndex": setIndex, "angles": angles,
                "best": best, "avg": avg, "goal": goal,
            ]
        case .sync(let progress, let locked):
            return ["type": "sync", "progress": progress, "locked": locked]
        case .caption(let text):
            return ["type": "caption", "text": text]
        case .formCue(let text, let rep):
            var d: [String: Any] = ["type": "formCue", "text": text]
            if let v = rep { d["rep"] = v }
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
                paused: paused,
                exerciseId: d["exerciseId"] as? String,
                goodReps: d["goodReps"] as? Int,
                restRemaining: d["restRemaining"] as? Int,
                countdown: d["countdown"] as? Int,
                isWatchExercise: d["isWatchExercise"] as? Bool
            )
            return .snapshot(snapshot)
        case "haptic":
            guard let pattern = d["pattern"] as? String else { return nil }
            return .haptic(pattern: pattern)
        case "summary":
            return .summary(formScore: d["formScore"] as? Int, goodReps: d["goodReps"] as? Int, repsTotal: d["repsTotal"] as? Int)
        case "setup":
            guard let firstName = d["firstName"] as? String,
                  let tremorMonitoring = d["tremorMonitoring"] as? Bool,
                  let prefsDict = d["prefs"] as? [String: Any],
                  let taps = prefsDict["taps"] as? String,
                  let arm = prefsDict["arm"] as? String,
                  let doubleTap = prefsDict["doubleTap"] as? Bool,
                  let captions = prefsDict["captions"] as? Bool,
                  let planArr = d["plan"] as? [[String: Any]] else { return nil }
            var plan: [PlanItem] = []
            for pd in planArr {
                guard let id = pd["id"] as? String, let name = pd["name"] as? String,
                      let sets = int(pd["sets"]), let reps = int(pd["reps"]),
                      let doneSets = int(pd["doneSets"]), let watch = pd["watch"] as? Bool else { return nil }
                plan.append(PlanItem(id: id, name: name, sets: sets, reps: reps, doneSets: doneSets,
                                      watch: watch, goalDeg: double(pd["goalDeg"])))
            }
            let setup = Setup(
                firstName: firstName, hrLimit: int(d["hrLimit"]), hrLimitSetBy: d["hrLimitSetBy"] as? String,
                therapistName: d["therapistName"] as? String, tremorMonitoring: tremorMonitoring,
                prefs: WatchPrefs(taps: taps, arm: arm, doubleTap: doubleTap, captions: captions), plan: plan
            )
            return .setup(setup)
        case "breather":
            guard let state = d["state"] as? String else { return nil }
            return .breather(state: state, bpm: int(d["bpm"]), limit: int(d["limit"]))
        case "recovery":
            guard let setIndex = int(d["setIndex"]), let peakBpm = int(d["peakBpm"]),
                  let bpm60 = int(d["bpm60"]), let drop = int(d["drop"]) else { return nil }
            return .recovery(setIndex: setIndex, peakBpm: peakBpm, bpm60: bpm60, drop: drop)
        case "rotationRep":
            guard let exerciseId = d["exerciseId"] as? String, let rep = int(d["rep"]),
                  let repsTarget = int(d["repsTarget"]), let angle = double(d["angle"]),
                  let goal = double(d["goal"]), let reached = d["reached"] as? Bool else { return nil }
            return .rotationRep(exerciseId: exerciseId, rep: rep, repsTarget: repsTarget, angle: angle, goal: goal, reached: reached)
        case "rotationSet":
            guard let exerciseId = d["exerciseId"] as? String, let setIndex = int(d["setIndex"]),
                  let angles = doubleArray(d["angles"]), let best = double(d["best"]),
                  let avg = double(d["avg"]), let goal = double(d["goal"]) else { return nil }
            return .rotationSet(exerciseId: exerciseId, setIndex: setIndex, angles: angles, best: best, avg: avg, goal: goal)
        case "sync":
            guard let progress = double(d["progress"]), let locked = d["locked"] as? Bool else { return nil }
            return .sync(progress: progress, locked: locked)
        case "caption":
            guard let text = d["text"] as? String else { return nil }
            return .caption(text: text)
        case "formCue":
            guard let text = d["text"] as? String else { return nil }
            return .formCue(text: text, rep: int(d["rep"]))
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
        case .wrist(let t, let quat, let accel, let deg):
            var d: [String: Any] = ["type": "wrist", "t": t, "quat": quat, "accel": accel]
            if let v = deg { d["deg"] = v }
            return d
        case .syncWindow(let t0, let dtMs, let mags):
            return ["type": "syncWindow", "t0": t0, "dtMs": dtMs, "mags": mags]
        case .tremor(let days):
            let arr: [[String: Any]] = days.map { day in
                [
                    "day": day.day, "noneMin": day.noneMin, "slightMin": day.slightMin,
                    "mildMin": day.mildMin, "strongMin": day.strongMin, "unknownMin": day.unknownMin,
                    "dyskinesia": day.dyskinesia, "source": day.source,
                ]
            }
            return ["type": "tremor", "days": arr]
        case .prefs(let taps, let arm, let doubleTap, let captions):
            var d: [String: Any] = ["type": "prefs"]
            if let v = taps { d["taps"] = v }
            if let v = arm { d["arm"] = v }
            if let v = doubleTap { d["doubleTap"] = v }
            if let v = captions { d["captions"] = v }
            return d
        case .setupDone:
            return ["type": "setupDone"]
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
        case "wrist":
            guard let t = double(d["t"]), let quat = doubleArray(d["quat"]), let accel = doubleArray(d["accel"]) else { return nil }
            return .wrist(t: t, quat: quat, accel: accel, deg: double(d["deg"]))
        case "syncWindow":
            guard let t0 = double(d["t0"]), let dtMs = int(d["dtMs"]), let mags = doubleArray(d["mags"]) else { return nil }
            return .syncWindow(t0: t0, dtMs: dtMs, mags: mags)
        case "tremor":
            guard let arr = d["days"] as? [[String: Any]] else { return nil }
            var days: [TremorDay] = []
            for dd in arr {
                guard let day = dd["day"] as? String, let noneMin = int(dd["noneMin"]),
                      let slightMin = int(dd["slightMin"]), let mildMin = int(dd["mildMin"]),
                      let strongMin = int(dd["strongMin"]), let unknownMin = int(dd["unknownMin"]),
                      let dyskinesia = dd["dyskinesia"] as? String, let source = dd["source"] as? String else { return nil }
                days.append(TremorDay(day: day, noneMin: noneMin, slightMin: slightMin, mildMin: mildMin,
                                       strongMin: strongMin, unknownMin: unknownMin, dyskinesia: dyskinesia, source: source))
            }
            return .tremor(days: days)
        case "prefs":
            return .prefs(taps: d["taps"] as? String, arm: d["arm"] as? String,
                          doubleTap: d["doubleTap"] as? Bool, captions: d["captions"] as? Bool)
        case "setupDone":
            return .setupDone
        default:
            return nil
        }
    }
}

extension WatchWire.LiveSnapshot {
    /// What the phone pushes once the session is over (`/ws/live` went quiet or
    /// reported `done`): the watch ends its workout and leaves the live screen.
    static let idle = WatchWire.LiveSnapshot(
        phase: "idle", rep: 0, repsTarget: nil, set: nil, setsTarget: nil,
        exerciseName: nil, hr: nil, paused: false
    )
}

/// Leading-edge throttle with a trailing push: the first change in a quiet period
/// goes out at once; changes inside the `interval` window wait until it closes,
/// so the latest state is always delivered (never dropped). Pure value type —
/// `WatchBridge` owns the clock and the timer.
struct PushThrottle: Equatable {
    let interval: TimeInterval
    private(set) var lastSentAt: Date?

    init(interval: TimeInterval) {
        self.interval = interval
    }

    /// True when a push may go out right now (outside the window of the last one).
    func shouldSend(_ now: Date) -> Bool {
        guard let lastSentAt else { return true }
        return now.timeIntervalSince(lastSentAt) >= interval
    }

    /// Seconds until the window closes, or nil when a push may go out now.
    func pendingAfter(_ now: Date) -> TimeInterval? {
        guard let lastSentAt else { return nil }
        let remaining = interval - now.timeIntervalSince(lastSentAt)
        return remaining > 0 ? remaining : nil
    }

    mutating func markSent(_ now: Date) {
        lastSentAt = now
    }
}
