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
    @ObservationIgnored private var lastPushedContext: [String: Any]?
    @ObservationIgnored private var lastPushAt: Date?
    /// A snapshot push must wait at least this long after the previous one.
    @ObservationIgnored private let pushInterval: TimeInterval = 0.5

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
    /// which the system delivers even while the watch app isn't running. Throttled to
    /// at most once every `pushInterval`, and skipped entirely when nothing changed —
    /// a dropped update is fine since the next live frame supersedes it.
    func push(_ live: LiveMessage?) {
        guard let session, session.activationState == .activated else { return }
        guard let live else { return }
        let snapshot = WatchWire.LiveSnapshot(
            phase: live.phase, rep: live.rep, repsTarget: live.repsTarget,
            set: live.set, setsTarget: live.setsTarget, exerciseName: live.exerciseName,
            hr: live.hr, paused: live.paused
        )
        let context = WatchWire.encode(.snapshot(snapshot))
        if let lastPushedContext, NSDictionary(dictionary: context).isEqual(to: lastPushedContext) { return }
        if let lastPushAt, Date().timeIntervalSince(lastPushAt) < pushInterval { return }
        do {
            try session.updateApplicationContext(context)
            lastPushedContext = context
            lastPushAt = Date()
        } catch {
            // Best-effort; the next push (or the watch's own retry) catches up.
        }
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

    nonisolated func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
        Task { @MainActor in
            self.isPaired = session.isPaired
            self.isWatchAppInstalled = session.isWatchAppInstalled
            self.isReachable = session.isReachable
        }
    }

    nonisolated func sessionDidBecomeInactive(_ session: WCSession) {}

    /// The watch switched to a different paired phone; reactivate so this phone
    /// keeps a usable session for whichever watch pairs with it next.
    nonisolated func sessionDidDeactivate(_ session: WCSession) {
        session.activate()
    }

    nonisolated func sessionReachabilityDidChange(_ session: WCSession) {
        Task { @MainActor in self.isReachable = session.isReachable }
    }

    nonisolated func sessionWatchStateDidChange(_ session: WCSession) {
        Task { @MainActor in
            self.isPaired = session.isPaired
            self.isWatchAppInstalled = session.isWatchAppInstalled
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
            case .status:
                break
            }
        }
    }
}
