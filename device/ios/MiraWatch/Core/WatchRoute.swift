import Foundation

/// Every full-screen state of the watch app, one per Figma "AppleWatch Hi-FI" card.
/// The Smart Stack card is the widget extension, not a route.
enum WatchRoute: Equatable, Hashable {
    // 01 Setup
    case paired, heartRate, whichArm, syncWave, learnTaps
    // 02 Ready & Stop
    case ready, countdown(Int), holdingToStop, stopped
    // 03 Live
    case goodRep, fixForm, setDone, recovery, breather
    // 04 Rotation
    case armExercises, startPosition, liveTurn, elbowDrift, rotationSummary
    // 05 More
    case tremorToday, phoneOutOfReach, settings

    /// Routes the user can open on top of the flow with `WatchFlowInput.open`.
    var isOverlay: Bool {
        switch self {
        case .settings, .tremorToday, .armExercises: return true
        default: return false
        }
    }
}
