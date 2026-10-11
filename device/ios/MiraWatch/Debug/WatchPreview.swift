#if DEBUG
import Foundation

/// DEBUG-only screen previews for simulator screenshots (W8): launch the watch app with
/// `-MiraPreviewRoute <name>` (e.g. `xcrun simctl launch <udid> com.mira.app.watchkitapp -MiraPreviewRoute countdown`)
/// and `RootView` loads that scenario instead of activating WatchConnectivity. Names are the `WatchRoute` case names.
///
/// Adding a route (W6/W7): add a `case "<name>":` to `scenario(_:)` that returns a `Scenario` reaching it —
/// phone `messages` go through the model's normal message handling, `inputs` straight into the reducer
/// (no side effects) — and add the name to `names`.
enum WatchPreview {
    struct Scenario {
        var setup: WatchWire.Setup = WatchPreview.sampleSetup
        /// Whether setup has finished (routes outside 01 Setup need true).
        var setupDone = true
        var messages: [WatchWire.ToWatch] = []
        var inputs: [WatchFlowInput] = []
        var heartRate: Int? = 98
        /// Holding to Stop ring value.
        var holdProgress: Double? = nil
        /// Start Position stillness ring value.
        var stillness: Double = 0
        /// Live Turn angle (MotionService is off in previews).
        var angle: Double? = nil
        /// Live Turn local rep mirror count.
        var localRotationReps = 0
        /// A relayed caption (shown when prefs.captions).
        var caption: String? = nil
    }

    /// The launch argument, read through NSArgumentDomain.
    static var requestedRoute: String? { UserDefaults.standard.string(forKey: "MiraPreviewRoute") }

    /// Frozen preview clock; timed cards (form cue, set done) stay up because the clock never advances.
    static let clock: TimeInterval = 1_760_000_000

    static let sampleSetup = WatchWire.Setup(
        firstName: "Maya", hrLimit: 120, hrLimitSetBy: "Dr. Lee", therapistName: "Dr. Lee", tremorMonitoring: true,
        prefs: .defaults,
        plan: [
            .init(id: "mini_squat", name: "Mini squats", sets: 3, reps: 12, doneSets: 0, watch: false, goalDeg: nil),
            .init(id: "forearm_rotation", name: "Forearm turns", sets: 2, reps: 10, doneSets: 0, watch: true, goalDeg: 80),
        ])

    static func idleSnapshot() -> WatchWire.ToWatch { .snapshot(.idle) }

    static func liveSnapshot(phase: String, rep: Int = 0, countdown: Int? = nil, exerciseId: String = "mini_squat",
                             exerciseName: String = "Mini squats", watch: Bool = false, repsTarget: Int = 12,
                             set: Int = 2, setsTarget: Int = 3, hr: Int? = 98,
                             restRemaining: Int? = nil) -> WatchWire.ToWatch {
        .snapshot(WatchWire.LiveSnapshot(phase: phase, rep: rep, repsTarget: repsTarget, set: set,
                                         setsTarget: setsTarget, exerciseName: exerciseName, hr: hr, paused: false,
                                         exerciseId: exerciseId, goodReps: rep, restRemaining: restRemaining,
                                         countdown: countdown, isWatchExercise: watch))
    }

    /// Snapshot of the forearm-turns watch exercise (set 1 of 2, 10 reps).
    static func rotationSnapshot(phase: String, rep: Int = 0, hr: Int? = 94) -> WatchWire.ToWatch {
        liveSnapshot(phase: phase, rep: rep, exerciseId: "forearm_rotation", exerciseName: "Forearm turns",
                     watch: true, repsTarget: 10, set: 1, setsTarget: 2, hr: hr)
    }

    /// Arm Exercises sample plan as on the Figma card (307:196).
    static let armPlanSetup: WatchWire.Setup = {
        var s = sampleSetup
        s.plan = [
            .init(id: "mini_squat", name: "Mini squats", sets: 3, reps: 12, doneSets: 0, watch: false, goalDeg: nil),
            .init(id: "forearm_rotation", name: "Forearm turns", sets: 3, reps: 10, doneSets: 0, watch: true, goalDeg: 80),
            .init(id: "shoulder_rotation", name: "Shoulder turns", sets: 2, reps: 8, doneSets: 0, watch: true, goalDeg: 70),
            .init(id: "wrist_flexion", name: "Wrist bends", sets: 2, reps: 10, doneSets: 0, watch: true, goalDeg: 60),
        ]
        return s
    }()

    static let names = [
        "paired", "heartRate", "whichArm", "syncWave", "learnTaps",
        "ready", "countdown", "holdingToStop", "stopped",
        "goodRep", "fixForm", "setDone", "recovery", "breather",
        "armExercises", "startPosition", "liveTurn", "elbowDrift", "rotationSummary",
        "goodRepCaption",   // Good Rep with a caption footnote (prefs.captions on)
    ]

    static func scenario(_ name: String) -> Scenario? {
        let toWhichArm: [WatchFlowInput] = [.next, .heartRateAnswered]
        switch name {
        // 01 Setup
        case "paired":
            return Scenario(setupDone: false)
        case "heartRate":
            return Scenario(setupDone: false, inputs: [.next])
        case "whichArm":
            return Scenario(setupDone: false, inputs: toWhichArm)
        case "syncWave":
            return Scenario(setupDone: false, messages: [.sync(progress: 0.7, locked: false)],
                            inputs: toWhichArm + [.armChosen("right")])
        case "learnTaps":
            return Scenario(setupDone: false, inputs: toWhichArm + [.armChosen("right"), .sync(progress: 1, locked: true)])
        // 02 Ready & Stop
        case "ready":
            return Scenario(messages: [idleSnapshot()])
        case "countdown":
            return Scenario(messages: [liveSnapshot(phase: "countdown", countdown: 3)])
        case "holdingToStop":
            return Scenario(messages: [idleSnapshot()], inputs: [.holdBegan], holdProgress: 0.7)
        case "stopped":
            return Scenario(messages: [idleSnapshot()], inputs: [.holdCompleted])
        // 03 Live
        case "goodRep":
            return Scenario(messages: [liveSnapshot(phase: "active", rep: 7)])
        case "fixForm":
            return Scenario(messages: [liveSnapshot(phase: "active", rep: 7, hr: 101),
                                       .formCue(text: "Even out your weight", rep: 7)])
        case "setDone":
            return Scenario(messages: [liveSnapshot(phase: "rest", rep: 12), .haptic(pattern: "set_done")])
        case "recovery":
            return Scenario(messages: [liveSnapshot(phase: "rest", rep: 12, hr: 104, restRemaining: 40),
                                       .recovery(setIndex: 2, peakBpm: 126, bpm60: 104, drop: 22)])
        case "breather":
            return Scenario(messages: [liveSnapshot(phase: "paused", rep: 7),
                                       .breather(state: "start", bpm: 128, limit: 120)])
        case "goodRepCaption":
            return Scenario(messages: [liveSnapshot(phase: "active", rep: 7)],
                            caption: "Nice and slow, keep your knees soft")
        // 04 Rotation
        case "armExercises":
            return Scenario(setup: armPlanSetup, messages: [idleSnapshot()], inputs: [.open(.armExercises)])
        case "startPosition":
            return Scenario(messages: [idleSnapshot()], inputs: [.chooseExercise("forearm_rotation")], stillness: 0.75)
        case "liveTurn":
            return Scenario(messages: [rotationSnapshot(phase: "active", rep: 3)],
                            inputs: [.chooseExercise("forearm_rotation"), .startPositionHeld],
                            angle: 68, localRotationReps: 3)
        case "elbowDrift":
            return Scenario(messages: [rotationSnapshot(phase: "active", rep: 3),
                                       .formCue(text: "Keep your elbow at your side", rep: nil)],
                            inputs: [.chooseExercise("forearm_rotation"), .startPositionHeld])
        case "rotationSummary":
            return Scenario(messages: [rotationSnapshot(phase: "active", rep: 10),
                                       rotationSnapshot(phase: "rest", rep: 10),
                                       .rotationSet(exerciseId: "forearm_rotation", setIndex: 1,
                                                    angles: [64, 70, 72, 75, 78, 81, 80, 74, 78, 82],
                                                    best: 82, avg: 75, goal: 80)],
                            inputs: [.chooseExercise("forearm_rotation"), .startPositionHeld])
        default:
            return nil
        }
    }
}
#endif
