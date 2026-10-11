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
                             exerciseName: String = "Mini squats", watch: Bool = false) -> WatchWire.ToWatch {
        .snapshot(WatchWire.LiveSnapshot(phase: phase, rep: rep, repsTarget: 12, set: 2, setsTarget: 3,
                                         exerciseName: exerciseName, hr: 98, paused: false, exerciseId: exerciseId,
                                         goodReps: rep, restRemaining: nil, countdown: countdown,
                                         isWatchExercise: watch))
    }

    static let names = [
        "paired", "heartRate", "whichArm", "syncWave", "learnTaps",
        "ready", "countdown", "holdingToStop", "stopped",
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
        default:
            return nil
        }
    }
}
#endif
