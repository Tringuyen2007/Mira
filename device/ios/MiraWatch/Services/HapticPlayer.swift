import Foundation
import WatchKit

/// Plays server `haptic` patterns on the watch with the patient's Taps strength (Settings → Taps,
/// `prefs.taps`). The timing comes from Core `HapticPlan` (strong = every step echoed 0.12 s later); this class
/// only schedules the steps and maps them to `WKHapticType`. Starting a pattern cancels one still in flight, so
/// fast events (good rep → fix form) never pile up into a long buzz train.
@MainActor
final class HapticPlayer {
    /// Updated by `WatchSessionModel` whenever setup or prefs change.
    var strength: TapStrength = .normal

    private var sequence: Task<Void, Never>?

    init() {}

    /// Plays `pattern` ("good_rep", "fix_form", "set_done", "check_in", "countdown_tick", "breather").
    /// Unknown patterns are ignored.
    func play(_ pattern: String) {
        let steps = HapticPlan.steps(for: pattern, strength: strength).sorted { $0.delay < $1.delay }
        guard !steps.isEmpty else { return }
        sequence?.cancel()
        sequence = nil
        var pending = steps[...]
        while let first = pending.first, first.delay <= 0 {
            WKInterfaceDevice.current().play(Self.wkType(first.kind))
            pending = pending.dropFirst()
        }
        guard !pending.isEmpty else { return }
        let rest = Array(pending)
        sequence = Task { @MainActor in
            let start = Date()
            for step in rest {
                let wait = step.delay - Date().timeIntervalSince(start)
                if wait > 0 { try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000)) }
                guard !Task.isCancelled else { return }
                WKInterfaceDevice.current().play(Self.wkType(step.kind))
            }
        }
    }

    /// One local haptic (UI feedback such as the Start Position zero click); not affected by strength.
    func play(kind: HapticKind) {
        WKInterfaceDevice.current().play(Self.wkType(kind))
    }

    func cancel() {
        sequence?.cancel()
        sequence = nil
    }

    static func wkType(_ kind: HapticKind) -> WKHapticType {
        switch kind {
        case .click: return .click
        case .success: return .success
        case .failure: return .failure
        case .notification: return .notification
        case .retry: return .retry
        case .start: return .start
        case .stop: return .stop
        case .directionUp: return .directionUp
        case .directionDown: return .directionDown
        }
    }
}
