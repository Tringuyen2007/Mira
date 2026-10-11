import Foundation
import Observation
import WatchConnectivity

/// The iPhone side of the Mira Watch App link (Plan 4). Pushes the live session
/// state to the watch (`updateApplicationContext`, throttled) and the occasional
/// haptic/summary (`sendMessage`, best-effort); relays the watch's heart rate and
/// button taps into `LiveSessionStore` exactly like any other `/ws/events` client
/// message. Lives in `Mira/Watch`, which the `MiraNetworking` SwiftPM package
/// excludes, so it is free to import `WatchConnectivity` (unavailable on plain
/// macOS, where `AppState`'s `swift test` build uses a stub of the same shape).
@MainActor
@Observable
final class WatchBridge: NSObject, WCSessionDelegate {
    private(set) var isPaired = false
    private(set) var isWatchAppInstalled = false
    private(set) var isReachable = false
    /// The watch's most recent heart-rate sample, rounded to whole bpm for display.
    private(set) var lastWatchHR: Int?

    @ObservationIgnored private let liveSession: LiveSessionStore
    @ObservationIgnored private var session: WCSession?
    /// Whether the watch reports a running `HKWorkoutSession` (its `.status` message).
    /// Nothing reads it yet; kept for a future Settings row.
    private(set) var workoutActive = false

    @ObservationIgnored private var lastPushedContext: [String: Any]?
    /// Snapshot pushes go out at most once every 0.5 s; a change inside the window
    /// is held in `pendingContext` and sent by `trailingPush` when it closes.
    @ObservationIgnored private var throttle = PushThrottle(interval: 0.5)
    @ObservationIgnored private var pendingContext: [String: Any]?
    @ObservationIgnored private var trailingPush: Task<Void, Never>?

    init(liveSession: LiveSessionStore) {
        self.liveSession = liveSession
        super.init()
    }

    func activate() {
        guard WCSession.isSupported() else { return }
        let session = WCSession.default
        session.delegate = self
        self.session = session
        session.activate()
    }

    /// Pushes the current `/ws/live` state to the watch via `updateApplicationContext`,
    /// which the system delivers even while the watch app isn't running. `nil` (the
    /// session ended) pushes the idle snapshot so the watch ends its workout. Skipped
    /// when nothing changed; throttled with a trailing push so the final state of a
    /// burst (e.g. the last rep, or the end of the session) always reaches the watch.
    func push(_ live: LiveMessage?) {
        guard let session, session.activationState == .activated else { return }
        let snapshot = live.map {
            WatchWire.LiveSnapshot(
                phase: $0.phase, rep: $0.rep, repsTarget: $0.repsTarget,
                set: $0.set, setsTarget: $0.setsTarget, exerciseName: $0.exerciseName,
                hr: $0.hr, paused: $0.paused
            )
        } ?? .idle
        let context = WatchWire.encode(.snapshot(snapshot))
        if let lastPushedContext, NSDictionary(dictionary: context).isEqual(to: lastPushedContext) {
            cancelTrailingPush()
            return
        }
        let now = Date()
        if throttle.shouldSend(now) {
            cancelTrailingPush()
            send(context, at: now)
            return
        }
        pendingContext = context
        guard trailingPush == nil, let delay = throttle.pendingAfter(now) else { return }
        trailingPush = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            guard let self, !Task.isCancelled else { return }
            self.trailingPush = nil
            guard let context = self.pendingContext else { return }
            self.pendingContext = nil
            self.send(context, at: Date())
        }
    }

    private func send(_ context: [String: Any], at now: Date) {
        guard let session, session.activationState == .activated else { return }
        do {
            try session.updateApplicationContext(context)
            lastPushedContext = context
            throttle.markSent(now)
        } catch {
            // Best-effort; the next push (or the watch's own retry) catches up.
        }
    }

    private func cancelTrailingPush() {
        pendingContext = nil
        trailingPush?.cancel()
        trailingPush = nil
    }

    /// Best-effort coaching haptic, only while the watch is reachable (foreground app).
    func forward(haptic pattern: String) {
        guard let session, session.isReachable else { return }
        session.sendMessage(WatchWire.encode(.haptic(pattern: pattern)), replyHandler: nil, errorHandler: { _ in })
    }

    /// Best-effort end-of-set/session counts, only while the watch is reachable.
    func forward(summary: SessionSummaryMessage) {
        guard let session, session.isReachable else { return }
        let message = WatchWire.ToWatch.summary(formScore: summary.formScore, goodReps: summary.goodReps, repsTotal: summary.repsTotal)
        session.sendMessage(WatchWire.encode(message), replyHandler: nil, errorHandler: { _ in })
    }

    // MARK: WCSessionDelegate

    // Each callback copies the `WCSession` values it needs before hopping to the
    // main actor, so no non-Sendable `WCSession` crosses the isolation boundary.

    nonisolated func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
        let paired = session.isPaired
        let installed = session.isWatchAppInstalled
        let reachable = session.isReachable
        Task { @MainActor in
            self.isPaired = paired
            self.isWatchAppInstalled = installed
            self.isReachable = reachable
        }
    }

    nonisolated func sessionDidBecomeInactive(_ session: WCSession) {}

    /// The watch switched to a different paired phone; reactivate so this phone
    /// keeps a usable session for whichever watch pairs with it next.
    nonisolated func sessionDidDeactivate(_ session: WCSession) {
        session.activate()
    }

    nonisolated func sessionReachabilityDidChange(_ session: WCSession) {
        let reachable = session.isReachable
        Task { @MainActor in self.isReachable = reachable }
    }

    nonisolated func sessionWatchStateDidChange(_ session: WCSession) {
        let paired = session.isPaired
        let installed = session.isWatchAppInstalled
        Task { @MainActor in
            self.isPaired = paired
            self.isWatchAppInstalled = installed
        }
    }

    nonisolated func session(_ session: WCSession, didReceiveMessage message: [String: Any]) {
        guard let decoded = WatchWire.decodeToPhone(message) else { return }
        Task { @MainActor in
            switch decoded {
            case .heartRate(let bpm, let t):
                self.lastWatchHR = Int(bpm.rounded())
                try? await self.liveSession.send(.hr(HeartRateMessage(t: t, bpm: bpm)))
            case .button(let name):
                try? await self.liveSession.send(.watchButton(WatchButtonMessage(name: name)))
            case .status(let workoutActive):
                self.workoutActive = workoutActive
            }
        }
    }
}
