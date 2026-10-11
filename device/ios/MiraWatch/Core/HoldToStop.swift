import Foundation

/// Ready button long-press: the ring fills over `duration`; releasing early cancels.
struct HoldToStop {
    let duration: TimeInterval
    private var startedAt: TimeInterval?

    init(duration: TimeInterval = 1.5) { self.duration = duration }

    var isHolding: Bool { startedAt != nil }

    mutating func begin(at t: TimeInterval) { startedAt = t }

    func progress(at t: TimeInterval) -> Double {
        guard let start = startedAt else { return 0 }
        guard duration > 0 else { return 1 }
        return min(max((t - start) / duration, 0), 1)
    }

    /// Ends the hold; true when it lasted the full duration (= stop).
    mutating func end(at t: TimeInterval) -> Bool {
        guard let start = startedAt else { return false }
        startedAt = nil
        return t - start >= duration
    }
}
