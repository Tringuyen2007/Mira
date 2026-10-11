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
///
/// W2 relay: server events → watch messages (`handle(event:)`, via the pure
/// `WatchRelay`), the watch's setup from `GET /me/watch` (`refreshSetup`), and the
/// watch's wrist/sync/tremor/prefs/setupDone messages on to the server. Every W2
/// path is a no-op unless a watch is paired with the Mira watch app installed, so a
/// phone without a watch makes no extra requests or socket sends.
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
    /// The watch's last setup (`GET /me/watch`), for Settings.
    private(set) var setup: WatchWire.Setup?
    /// The watch finished its own setup flow (`setupDone`); stored locally only.
    private(set) var watchSetupDone: Bool

    /// A watch is paired and has the Mira watch app: the gate for every W2 path.
    var watchPaired: Bool { isPaired && isWatchAppInstalled }
    /// The REST client to use for watch requests, or nil without a linked patient token.
    /// Set by `AppState`.
    @ObservationIgnored var apiProvider: (() -> APIClient?)?
    /// Called with the `PUT /me` response after the watch changed its prefs, so
    /// `AppState.me` doesn't keep (and later re-send) stale `device_prefs`.
    @ObservationIgnored var onMeUpdated: ((MeResponse) -> Void)?

    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private var relay = WatchRelay()
    @ObservationIgnored private var wristGate = RateGate(minInterval: 0.1)
    /// `refreshSetup` coalescing: one request at a time, plus one more if asked meanwhile.
    @ObservationIgnored private var setupRefreshInFlight = false
    @ObservationIgnored private var setupRefreshAgain = false
    private static let setupDoneKey = "watchSetupDone"

    @ObservationIgnored private var lastPushedContext: [String: Any]?
    /// Snapshot pushes go out at most once every 0.5 s; a change inside the window
    /// is held in `pendingContext` and sent by `trailingPush` when it closes.
    @ObservationIgnored private var throttle = PushThrottle(interval: 0.5)
    @ObservationIgnored private var pendingContext: [String: Any]?
    @ObservationIgnored private var trailingPush: Task<Void, Never>?

    init(liveSession: LiveSessionStore, defaults: UserDefaults = .standard) {
        self.liveSession = liveSession
        self.defaults = defaults
        self.watchSetupDone = defaults.bool(forKey: Self.setupDoneKey)
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
        if watchPaired, let caption = relay.caption(from: live) { sendMessage(caption) }
        let snapshot = relay.snapshot(live, plan: setup?.plan ?? [])
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

    /// A decoded `/ws/events` event (from `LiveSessionStore.onWatchEvent`): relays
    /// haptics, captions, form cues, breather/recovery/rotation/sync to the watch, and
    /// refetches the setup when the therapist changed limits or watch features.
    func handle(event: SocketEvent) {
        guard watchPaired else { return }
        for message in relay.map(event) { sendMessage(message) }
        if WatchRelay.setupRefreshNeeded(event) {
            Task { await self.refreshSetup(api: self.apiProvider?()) }
        }
    }

    /// `GET /me/watch` → the watch's `setup`. The application context is the live
    /// snapshot's, so setup goes out as queued `transferUserInfo` (delivered even when
    /// the watch app is closed; older queued setups are cancelled) plus `sendMessage`
    /// when reachable. No-op without a paired watch or a patient token; errors are swallowed.
    func refreshSetup(api: APIClient?) async {
        guard watchPaired, let api else { return }
        if setupRefreshInFlight {
            setupRefreshAgain = true
            return
        }
        setupRefreshInFlight = true
        defer { setupRefreshInFlight = false }
        repeat {
            setupRefreshAgain = false
            guard let response = try? await api.meWatch() else { return }
            let setup = WatchRelay.setup(response)
            self.setup = setup
            deliver(setup: setup)
        } while setupRefreshAgain && watchPaired
    }

    private func deliver(setup: WatchWire.Setup) {
        guard let session, session.activationState == .activated, watchPaired else { return }
        let payload = WatchWire.encode(.setup(setup))
        for transfer in session.outstandingUserInfoTransfers where transfer.userInfo["type"] as? String == "setup" {
            transfer.cancel()
        }
        session.transferUserInfo(payload)
        if session.isReachable { session.sendMessage(payload, replyHandler: nil, errorHandler: { _ in }) }
    }

    /// Best-effort phone → watch message, only while the watch is reachable (foreground app).
    private func sendMessage(_ message: WatchWire.ToWatch) {
        guard let session, session.isReachable else { return }
        session.sendMessage(WatchWire.encode(message), replyHandler: nil, errorHandler: { _ in })
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
            self.isReachable = reachable
            self.updateWatchState(paired: paired, installed: installed)
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
        Task { @MainActor in self.updateWatchState(paired: paired, installed: installed) }
    }

    /// Records pairing state; a watch that just became paired+installed gets its setup.
    private func updateWatchState(paired: Bool, installed: Bool) {
        let wasPaired = watchPaired
        isPaired = paired
        isWatchAppInstalled = installed
        if !wasPaired, watchPaired {
            Task { await self.refreshSetup(api: self.apiProvider?()) }
        }
    }

    nonisolated func session(_ session: WCSession, didReceiveMessage message: [String: Any]) {
        guard let decoded = WatchWire.decodeToPhone(message) else { return }
        Task { @MainActor in
            switch decoded {
            case .heartRate(let bpm, let t):
                self.lastWatchHR = Int(bpm.rounded())
                try? await self.liveSession.send(.hr(HeartRateMessage(t: t, bpm: bpm)))
            case .button, .syncWindow:
                if let event = WatchRelay.socketEvent(decoded) { try? await self.liveSession.send(event) }
            case .wrist(let t, _, _, _):
                // The watch streams faster than the server's 20 msg/s budget allows; keep 10 Hz.
                guard self.wristGate.allow(t), let event = WatchRelay.socketEvent(decoded) else { return }
                try? await self.liveSession.send(event)
            case .status(let workoutActive):
                self.workoutActive = workoutActive
            case .tremor(let days):
                // 403 "tremor monitoring off" (and any other failure) is ignored.
                guard let api = self.apiProvider?() else { return }
                _ = try? await api.uploadTremor(WatchRelay.tremorUpload(days))
            case .prefs(let taps, let arm, let doubleTap, let captions):
                await self.savePrefs(taps: taps, arm: arm, doubleTap: doubleTap, captions: captions)
            case .setupDone:
                self.watchSetupDone = true
                self.defaults.set(true, forKey: Self.setupDoneKey)
            }
        }
    }

    /// Merges the watch's prefs into `device_prefs` (as `watch_*` keys, every other key
    /// kept) with a fresh `GET /me` + `PUT /me`, then re-sends the setup. Errors swallowed.
    private func savePrefs(taps: String?, arm: String?, doubleTap: Bool?, captions: Bool?) async {
        guard let api = apiProvider?() else { return }
        do {
            let current = try await api.me()
            let merged = WatchRelay.mergedDevicePrefs(current.patient.devicePrefs, taps: taps, arm: arm,
                                                      doubleTap: doubleTap, captions: captions)
            let updated = try await api.updateMe(MeUpdate(devicePrefs: merged))
            onMeUpdated?(updated)
        } catch {
            return
        }
        await refreshSetup(api: api)
    }
}
