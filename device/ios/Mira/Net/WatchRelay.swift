import Foundation

/// The phone's pure watch-relay logic (W2): which `/ws/events` events become which
/// `WatchWire` messages, what goes into the live snapshot, how watch messages turn
/// into client events / REST bodies. Foundation-only so `swift test` covers it on
/// macOS; `WatchBridge` owns the `WCSession`, the sockets and the clock, and only
/// calls in here when a watch is paired.
struct WatchRelay {
    /// Good-quality reps in the current set (reset on a countdown or set change).
    private(set) var goodReps = 0
    /// The current set, from the latest `countdown`/`rep`; a different set resets the counters.
    private var currentSet: Int?
    /// Index of the last `fix_form` rep still waiting for its explaining `cue`.
    private var pendingFixFormRep: Int?
    /// Rep number of the latest `rotation_rep` (for `elbow_drift` form cues).
    private var lastRotationRep: Int?
    /// Last caption text sent to the watch, so `live.caption` doesn't repeat a cue/greeting.
    private var lastCaption: String?

    init() {}

    // MARK: Server → watch

    /// Server event → zero or more watch messages. Anything the watch doesn't show maps to `[]`.
    mutating func map(_ event: SocketEvent) -> [WatchWire.ToWatch] {
        switch event {
        case .haptic(let h):
            return [.haptic(pattern: h.pattern)]
        case .breather(let b):
            return [.breather(state: b.state, bpm: b.bpm, limit: b.limit)]
        case .hrRecovery(let r):
            return [.recovery(setIndex: r.setIndex, peakBpm: r.peakBpm, bpm60: r.bpm60s, drop: r.dropBpm)]
        case .rotationRep(let r):
            lastRotationRep = r.rep
            return [.rotationRep(exerciseId: r.exerciseId, rep: r.rep, repsTarget: r.repsTarget,
                                 angle: r.angleDeg, goal: r.goalDeg, reached: r.reached)]
        case .rotationSet(let s):
            return [.rotationSet(exerciseId: s.exerciseId, setIndex: s.setIndex, angles: s.angles,
                                 best: s.bestDeg, avg: s.avgDeg, goal: s.goalDeg)]
        case .syncProgress(let p):
            return [.sync(progress: p.progress, locked: p.locked)]
        case .greeting(let g):
            return captionMessage(g.text).map { [$0] } ?? []
        case .cue(let cue):
            var out: [WatchWire.ToWatch] = captionMessage(cue.text).map { [$0] } ?? []
            if cue.id == "elbow_drift" {
                out.append(.formCue(text: cue.text, rep: lastRotationRep))
                pendingFixFormRep = nil
            } else if let rep = pendingFixFormRep {
                out.append(.formCue(text: cue.text, rep: rep))
                pendingFixFormRep = nil
            }
            return out
        case .rep(let rep):
            if let set = rep.set {
                if let currentSet, currentSet != set { resetSet() }
                currentSet = set
            }
            switch rep.quality {
            case "good":
                goodReps += 1
                pendingFixFormRep = nil
            case "fix_form":
                pendingFixFormRep = rep.index
            default:
                break
            }
            return []
        case .countdown(let c):
            resetSet()
            if let set = c.set { currentSet = set }
            return []
        default:
            return []
        }
    }

    private mutating func resetSet() {
        goodReps = 0
        pendingFixFormRep = nil
        lastRotationRep = nil
    }

    /// Caption for an event's text; always sent (a repeated cue is a new utterance),
    /// but remembered so the `/ws/live` echo of it isn't sent again.
    private mutating func captionMessage(_ text: String) -> WatchWire.ToWatch? {
        guard !text.isEmpty else { return nil }
        lastCaption = text
        return .caption(text: text)
    }

    /// Live state → snapshot, enriched with the relay's good-rep count and the
    /// rest/countdown/exercise extras. `nil` (session over) is the idle snapshot.
    func snapshot(_ live: LiveMessage?, plan: [WatchWire.PlanItem]) -> WatchWire.LiveSnapshot {
        guard let live else { return .idle }
        let isWatchExercise = live.exerciseId.map { id in plan.contains { $0.id == id && $0.watch } }
        return WatchWire.LiveSnapshot(
            phase: live.phase, rep: live.rep, repsTarget: live.repsTarget,
            set: live.set, setsTarget: live.setsTarget, exerciseName: live.exerciseName,
            hr: live.hr, paused: live.paused,
            exerciseId: live.exerciseId, goodReps: goodReps, restRemaining: live.restRemaining,
            countdown: live.countdown, isWatchExercise: isWatchExercise
        )
    }

    /// `live.caption` changes → `.caption` once per distinct non-empty text.
    mutating func caption(from live: LiveMessage?) -> WatchWire.ToWatch? {
        guard let text = live?.caption?.text, !text.isEmpty, text != lastCaption else { return nil }
        lastCaption = text
        return .caption(text: text)
    }

    /// `GET /me/watch` → the watch's setup payload; unset prefs fall back to `WatchPrefs.defaults` per key.
    static func setup(_ r: MeWatchResponse) -> WatchWire.Setup {
        let d = WatchWire.WatchPrefs.defaults
        return WatchWire.Setup(
            firstName: r.firstName, hrLimit: r.hrLimit, hrLimitSetBy: r.hrLimitSetByName,
            therapistName: r.therapistName, tremorMonitoring: r.tremorMonitoring,
            prefs: WatchWire.WatchPrefs(taps: r.prefs.watchTaps ?? d.taps, arm: r.prefs.watchArm ?? d.arm,
                                        doubleTap: r.prefs.watchDoubleTap ?? d.doubleTap,
                                        captions: r.prefs.watchCaptions ?? d.captions),
            plan: r.plan.map {
                WatchWire.PlanItem(id: $0.exerciseId, name: $0.name, sets: $0.sets, reps: $0.reps,
                                   doneSets: $0.doneSets, watch: $0.watch, goalDeg: $0.goalDeg)
            }
        )
    }

    /// Events after which the watch's setup (`/me/watch`) is stale.
    static func setupRefreshNeeded(_ event: SocketEvent) -> Bool {
        switch event {
        case .limitsChanged, .watchFeaturesChanged: return true
        default: return false
        }
    }

    // MARK: Watch → server

    /// Watch message → the `/ws/events` client event the phone sends on its behalf
    /// (`nil` for messages handled locally or over REST).
    static func socketEvent(_ m: WatchWire.ToPhone) -> SocketEvent? {
        switch m {
        case .button(let name):
            return .watchButton(WatchButtonMessage(name: name))
        case .wrist(let t, let quat, let accel, let deg):
            return .wrist(WristMessage(t: t, quat: quat, accel: accel, rotationDeg: deg))
        case .syncWindow(let t0, let dtMs, let mags):
            return .watchSync(WatchSyncMessage(t0: t0, dtMs: dtMs, accelMag: mags))
        default:
            return nil
        }
    }

    /// `POST /me/tremor` body: the newest 14 days (the server's cap), oldest first.
    static func tremorUpload(_ days: [WatchWire.TremorDay]) -> TremorUpload {
        let newest = days.sorted { $0.day < $1.day }.suffix(14)
        return TremorUpload(days: newest.map {
            TremorDayBody(day: $0.day, noneMin: $0.noneMin, slightMin: $0.slightMin, mildMin: $0.mildMin,
                          strongMin: $0.strongMin, unknownMin: $0.unknownMin, dyskinesia: $0.dyskinesia, source: $0.source)
        })
    }

    /// `device_prefs` with the watch's changes applied as `watch_*` keys; every other key is kept as is.
    static func mergedDevicePrefs(_ current: JSONValue?, taps: String?, arm: String?, doubleTap: Bool?, captions: Bool?) -> JSONValue {
        var object: [String: JSONValue] = [:]
        if case .object(let existing) = current { object = existing }
        if let taps { object["watch_taps"] = .string(taps) }
        if let arm { object["watch_arm"] = .string(arm) }
        if let doubleTap { object["watch_double_tap"] = .bool(doubleTap) }
        if let captions { object["watch_captions"] = .bool(captions) }
        return .object(object)
    }
}

/// Drops samples that arrive within `minInterval` of the last one let through
/// (wrist motion at 10 Hz → `minInterval` 0.1). A clock that jumps backwards
/// (watch restarted its stream) lets the next sample through.
struct RateGate {
    let minInterval: TimeInterval
    private var last: TimeInterval?

    init(minInterval: TimeInterval) {
        self.minInterval = minInterval
    }

    mutating func allow(_ t: TimeInterval) -> Bool {
        if let last, t >= last, t - last < minInterval - 1e-9 { return false }
        last = t
        return true
    }
}
