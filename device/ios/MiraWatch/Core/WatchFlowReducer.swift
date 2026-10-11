import Foundation

/// Everything the watch UI needs, built only from setup context, snapshots, relayed events and local taps.
/// Nested types mirror the `WatchWire` shapes so Core never imports MiraNetworking.
struct WatchFlowState: Equatable {
    struct Prefs: Equatable {
        var taps = "normal"         // "normal" | "strong"
        var arm = "right"           // "left" | "right"
        var doubleTap = true
        var captions = true
    }
    struct PlanEntry: Equatable {
        var id: String
        var name: String
        var sets: Int
        var reps: Int
        var doneSets: Int
        var watch: Bool
        var goalDeg: Double?
    }
    struct FormCue: Equatable {
        var text: String
        var rep: Int?
        var until: TimeInterval
    }
    struct Breather: Equatable {
        var bpm: Int
        var limit: Int
    }
    struct Recovery: Equatable {
        var peak: Int
        var bpm60: Int
        var drop: Int
        var setIndex: Int
    }
    struct RotationRep: Equatable {
        var exerciseId: String
        var rep: Int
        var repsTarget: Int
        var angle: Double
        var goal: Double
        var reached: Bool
    }
    struct RotationSummary: Equatable {
        var exerciseId: String
        var angles: [Double]
        var best: Double
        var avg: Double
        var goal: Double
    }

    var setupStep: WatchRoute? = .paired     // nil once setup done
    var firstName = ""; var hrLimit: Int?; var hrLimitSetBy: String?; var therapistName: String?
    var tremorMonitoring = false; var prefs = Prefs(); var plan: [PlanEntry] = []
    var phase = "idle"; var rep = 0; var repsTarget: Int?; var set: Int?; var setsTarget: Int?; var goodReps = 0
    var countdown: Int?; var restRemaining: Int?; var exerciseId: String?; var isWatchExercise = false; var hr: Int?
    var paused = false
    var formCue: FormCue?                    // shown 3 s
    var setDoneUntil: TimeInterval?          // set-done card for 3 s, then recovery
    var breather: Breather?
    var recovery: Recovery?
    var rotationRep: RotationRep?; var rotationSet: RotationSummary?; var selectedExercise: String?; var startZeroed = false
    var syncProgress = 0.0; var syncLocked = false
    var phoneReachable = true; var outOfReachDismissed = false
    var holding = false; var stopped = false
    var overlay: WatchRoute?                 // .settings / .tremorToday / .armExercises opened by the user

    /// A session is running (anything but idle/done).
    var sessionActive: Bool { phase != "idle" && phase != "done" }
    /// Today's watch exercises (Arm Exercises list).
    var watchExercises: [PlanEntry] { plan.filter(\.watch) }
    var hasUnfinishedWatchExercise: Bool { watchExercises.contains { $0.doneSets < $0.sets } }
}

enum WatchFlowInput: Equatable {
    case setup(firstName: String, hrLimit: Int?, hrLimitSetBy: String?, therapist: String?, tremor: Bool,
               prefs: WatchFlowState.Prefs, plan: [WatchFlowState.PlanEntry], setupDone: Bool)
    case next                    // Paired → Heart Rate
    case heartRateAnswered       // allow or "Not now" → Which Arm
    case armChosen(String)       // → Sync Wave
    case sync(progress: Double, locked: Bool)   // locked → Learn the Taps
    case tapsLearned             // → setupStep nil (send setupDone)
    case snapshot(phase: String, rep: Int, repsTarget: Int?, set: Int?, setsTarget: Int?, goodReps: Int?, countdown: Int?,
                  restRemaining: Int?, exerciseId: String?, isWatchExercise: Bool, hr: Int?, paused: Bool)
    case haptic(String)          // "set_done" → setDoneUntil = now+3
    case formCue(text: String, rep: Int?)
    case breather(state: String, bpm: Int?, limit: Int?)
    case recovery(setIndex: Int, peak: Int, bpm60: Int, drop: Int)
    case rotationRep(exerciseId: String, rep: Int, repsTarget: Int, angle: Double, goal: Double, reached: Bool)
    case rotationSet(exerciseId: String, angles: [Double], best: Double, avg: Double, goal: Double)
    case reachability(Bool); case dismissOutOfReach
    case holdBegan; case holdCancelled; case holdCompleted     // holdCompleted → stopped = true (model sends button stop)
    case resume                  // Stopped → Ready (model sends button resume)
    case open(WatchRoute); case close                          // settings / tremorToday / armExercises
    case chooseExercise(String)  // Arm Exercises → Start Position
    case startPositionHeld       // → startZeroed; Live Turn once phase active
    case deepLinkReady           // widget tap → close overlays → Ready
    case tick                    // re-evaluate time-based cards
}

/// Pure state machine behind `RootView`. The watch model feeds it inputs and renders `route(now:)`.
struct WatchFlowReducer {
    static let cardSeconds: TimeInterval = 3

    private(set) var state = WatchFlowState()

    init() {}

    mutating func reduce(_ input: WatchFlowInput, now: TimeInterval) {
        switch input {
        case let .setup(firstName, hrLimit, hrLimitSetBy, therapist, tremor, prefs, plan, setupDone):
            state.firstName = firstName
            state.hrLimit = hrLimit
            state.hrLimitSetBy = hrLimitSetBy
            state.therapistName = therapist
            state.tremorMonitoring = tremor
            state.prefs = prefs
            state.plan = plan
            // A re-pushed context (limits change, foreground) never restarts setup; it can only finish it.
            if setupDone { state.setupStep = nil }
            if !tremor, state.overlay == .tremorToday { state.overlay = nil }

        case .next:
            if state.setupStep == .paired { state.setupStep = .heartRate }
        case .heartRateAnswered:
            if state.setupStep == .heartRate { state.setupStep = .whichArm }
        case .armChosen(let arm):
            state.prefs.arm = arm
            if state.setupStep == .whichArm { state.setupStep = .syncWave }
        case let .sync(progress, locked):
            state.syncProgress = min(max(progress, 0), 1)
            state.syncLocked = locked
            if locked, state.setupStep == .syncWave { state.setupStep = .learnTaps }
        case .tapsLearned:
            if state.setupStep == .learnTaps { state.setupStep = nil }

        case let .snapshot(phase, rep, repsTarget, set, setsTarget, goodReps, countdown, restRemaining, exerciseId,
                           isWatchExercise, hr, paused):
            applySnapshot(phase: phase, rep: rep, repsTarget: repsTarget, set: set, setsTarget: setsTarget,
                          goodReps: goodReps, countdown: countdown, restRemaining: restRemaining,
                          exerciseId: exerciseId, isWatchExercise: isWatchExercise, hr: hr, paused: paused)

        case .haptic(let pattern):
            if pattern == "set_done" { state.setDoneUntil = now + Self.cardSeconds }
        case let .formCue(text, rep):
            state.formCue = .init(text: text, rep: rep, until: now + Self.cardSeconds)
        case let .breather(s, bpm, limit):
            if s == "start" {
                state.breather = .init(bpm: bpm ?? state.hr ?? 0, limit: limit ?? state.hrLimit ?? 0)
                if let bpm { state.hr = bpm }
            } else {
                state.breather = nil
                if let bpm { state.hr = bpm }
            }
        case let .recovery(setIndex, peak, bpm60, drop):
            state.recovery = .init(peak: peak, bpm60: bpm60, drop: drop, setIndex: setIndex)
        case let .rotationRep(exerciseId, rep, repsTarget, angle, goal, reached):
            state.rotationRep = .init(exerciseId: exerciseId, rep: rep, repsTarget: repsTarget, angle: angle,
                                      goal: goal, reached: reached)
        case let .rotationSet(exerciseId, angles, best, avg, goal):
            state.rotationSet = .init(exerciseId: exerciseId, angles: angles, best: best, avg: avg, goal: goal)

        case .reachability(let reachable):
            state.phoneReachable = reachable
            if reachable { state.outOfReachDismissed = false }   // the next drop shows the card again
        case .dismissOutOfReach:
            state.outOfReachDismissed = true

        case .holdBegan:
            if state.setupStep == nil, !state.stopped { state.holding = true }
        case .holdCancelled:
            state.holding = false
        case .holdCompleted:
            state.holding = false
            if state.setupStep == nil { state.stopped = true }
        case .resume:
            state.stopped = false
            state.holding = false

        case .open(let route):
            guard route.isOverlay, state.setupStep == nil else { return }
            if route == .tremorToday, !state.tremorMonitoring { return }
            if route == .armExercises, state.sessionActive { return }
            state.overlay = route
        case .close:
            state.overlay = nil
        case .chooseExercise(let id):
            state.selectedExercise = id
            state.startZeroed = false
            state.overlay = nil
        case .startPositionHeld:
            state.startZeroed = true
        case .deepLinkReady:
            state.overlay = nil
            if !state.sessionActive { state.selectedExercise = nil }
        case .tick:
            if let cue = state.formCue, cue.until <= now { state.formCue = nil }
            if let until = state.setDoneUntil, until <= now { state.setDoneUntil = nil }
        }
    }

    private mutating func applySnapshot(phase: String, rep: Int, repsTarget: Int?, set: Int?, setsTarget: Int?,
                                        goodReps: Int?, countdown: Int?, restRemaining: Int?, exerciseId: String?,
                                        isWatchExercise: Bool, hr: Int?, paused: Bool) {
        let previousPhase = state.phase
        let wasActive = state.sessionActive
        if exerciseId != state.exerciseId, exerciseId != nil, state.exerciseId != nil,
           exerciseId != state.selectedExercise {
            // A different exercise than the one zeroed for: the zero and rotation results belong to the old one.
            state.startZeroed = false
            state.rotationRep = nil
            state.rotationSet = nil
        }
        state.phase = phase
        state.rep = rep
        state.repsTarget = repsTarget
        state.set = set
        state.setsTarget = setsTarget
        if let goodReps { state.goodReps = goodReps }
        state.countdown = countdown
        state.restRemaining = restRemaining
        state.exerciseId = exerciseId
        let planSaysWatch = exerciseId.flatMap { id in state.plan.first { $0.id == id }?.watch } ?? false
        state.isWatchExercise = isWatchExercise || planSaysWatch
        if let hr { state.hr = hr }
        state.paused = paused

        if phase == "active", previousPhase != "active" {
            // A new set begins: last set's summary and rep no longer apply.
            state.rotationSet = nil
            state.rotationRep = nil
        }
        if !state.sessionActive {
            state.formCue = nil
            state.setDoneUntil = nil
            state.breather = nil
            state.outOfReachDismissed = false
            state.holding = false
            if wasActive {
                // The session just ended: drop rotation results and the chosen exercise / zero.
                // (Idle snapshots between sessions keep a just-chosen exercise and its zero.)
                state.rotationRep = nil
                state.rotationSet = nil
                state.startZeroed = false
                state.selectedExercise = nil
            }
            if phase == "idle" { state.goodReps = goodReps ?? 0 }
        }
    }

    /// First match wins; see the W3 brief "Route priority".
    func route(now: TimeInterval) -> WatchRoute {
        let s = state
        if let step = s.setupStep { return step }
        if s.stopped { return .stopped }
        if s.holding { return .holdingToStop }
        if s.sessionActive, !s.phoneReachable, !s.outOfReachDismissed { return .phoneOutOfReach }
        if s.breather != nil { return .breather }
        if let overlay = s.overlay { return overlay }
        if s.phase == "countdown" { return .countdown(s.countdown ?? 3) }

        let liveCue = s.formCue.flatMap { $0.until > now ? $0 : nil }
        let watchFlow = s.sessionActive ? s.isWatchExercise : s.selectedExercise != nil
        if watchFlow {
            if let cue = liveCue, s.sessionActive, cue.rep == nil || cue.text.lowercased().contains("elbow") {
                return .elbowDrift
            }
            if s.phase == "rest" {
                if s.rotationSet != nil { return .rotationSummary }
                return .recovery
            }
            if s.phase == "active" || s.phase == "paused", s.startZeroed { return .liveTurn }
            return .startPosition
        }

        if let until = s.setDoneUntil, until > now { return .setDone }
        if s.phase == "rest" { return .recovery }
        if liveCue != nil, s.sessionActive { return .fixForm }
        if s.sessionActive { return .goodRep }
        return .ready
    }
}
