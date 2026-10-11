import Foundation

/// Settings → Taps (`device_prefs.watch_taps`).
enum TapStrength: String { case normal, strong }

/// Mirrors `WKHapticType` cases without importing WatchKit; `HapticPlayer` maps them.
enum HapticKind: Equatable { case click, success, failure, notification, retry, start, stop, directionUp, directionDown }

/// One haptic, `delay` seconds after the pattern starts.
struct HapticStep: Equatable {
    var kind: HapticKind
    var delay: TimeInterval
}

enum HapticPlan {
    static let strongEchoDelay: TimeInterval = 0.12

    /// Server `haptic` pattern → timed steps. Strong plays every step twice (+0.12 s). Unknown → [].
    static func steps(for pattern: String, strength: TapStrength) -> [HapticStep] {
        let base: [HapticStep]
        switch pattern {
        case "good_rep":       base = [.init(kind: .success, delay: 0)]
        case "fix_form":       base = [.init(kind: .failure, delay: 0), .init(kind: .failure, delay: 0.30)]
        case "set_done":       base = [.init(kind: .notification, delay: 0), .init(kind: .notification, delay: 0.15),
                                       .init(kind: .notification, delay: 0.30)]
        case "check_in":       base = [.init(kind: .retry, delay: 0)]
        case "countdown_tick": base = [.init(kind: .click, delay: 0)]
        case "breather":       base = [.init(kind: .directionDown, delay: 0), .init(kind: .directionDown, delay: 0.6)]
        default:               base = []
        }
        guard strength == .strong else { return base }
        return base.flatMap { [$0, HapticStep(kind: $0.kind, delay: $0.delay + strongEchoDelay)] }
    }
}
