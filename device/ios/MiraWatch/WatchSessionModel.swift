import Foundation
import Observation
import WatchConnectivity
import WatchKit

/// The watch side of the Mira link, built on the pure `WatchFlowReducer` (Core). Everything from the iPhone's
/// `WatchBridge` (setup context, snapshots, haptics, relayed server events) becomes a `WatchFlowInput`; every
/// screen reads `route` and `flow.state`. Side effects (WatchConnectivity sends, haptics, heart-rate workout,
/// motion sensors) live here, never in the reducer.
@MainActor
@Observable
final class WatchSessionModel: NSObject, WCSessionDelegate {
    struct Summary: Equatable {
        var formScore: Int?
        var goodReps: Int?
        var repsTotal: Int?
    }

    private(set) var flow = WatchFlowReducer()
    /// The clock the route is evaluated against; advanced by the 0.5 s tick so timed cards expire.
    private(set) var now = Date().timeIntervalSince1970
    var route: WatchRoute { flow.route(now: now) }

    private(set) var setup: WatchWire.Setup?
    private(set) var snapshot: WatchWire.LiveSnapshot?
    private(set) var summary: Summary?
    /// Latest relayed caption (`cue.text` / `greeting.text` / `live.caption`); screens show it when prefs.captions.
    private(set) var caption: String?
    /// The most recent heart rate measured on this watch (whole bpm); nil without samples ("Not now", simulator).
    private(set) var heartRate: Int?
    private(set) var heartRateStatus: String?
    private(set) var workoutActive = false
    /// Start Position stillness ring (0…1) while MotionService is in rotation mode.
    private(set) var stillness = 0.0
    /// Set when a button tap could not reach the iPhone.
    private(set) var sendError: String?

    let motion = MotionService()
    let heartRateSource = WorkoutHeartRate()

    @ObservationIgnored private var session: WCSession?
    @ObservationIgnored private var ticker: Task<Void, Never>?
    @ObservationIgnored private var defaults: UserDefaults

    static let setupDefaultsKey = "mira.watch.setup"
    static let setupDoneDefaultsKey = "mira.watch.setupDone"

    #if DEBUG
    /// DEBUG screen previews (`-MiraPreviewRoute`): no WatchConnectivity, frozen clock, no sensors or workout.
    private(set) var isPreview = false
    /// Holding to Stop ring value for the preview (live, it comes from the press itself).
    private(set) var previewHoldProgress: Double?
    #endif

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        super.init()
        heartRateSource.onHeartRate = { [weak self] bpm, date in self?.send(heartRate: bpm, at: date) }
        heartRateSource.onStateChange = { [weak self] in self?.heartRateStateChanged() }
        motion.onWrist = { [weak self] t, quat, accel, deg in
            self?.sendToPhone(.wrist(t: t, quat: quat, accel: accel, deg: deg))
        }
        motion.onSyncWindow = { [weak self] w in
            self?.sendToPhone(.syncWindow(t0: w.t0, dtMs: w.dtMs, mags: w.mags))
        }
        motion.onStillness = { [weak self] p in self?.stillness = p }
        // Cold start: the last setup context, so the watch knows the patient before the phone answers.
        if let stored = defaults.dictionary(forKey: Self.setupDefaultsKey),
           case .setup(let s)? = WatchWire.decodeToWatch(stored) {
            applySetup(s, persist: false)
        }
    }

    func activate() {
        #if DEBUG
        if isPreview { return }
        #endif
        startTicker()
        guard session == nil, WCSession.isSupported() else { return }
        let session = WCSession.default
        session.delegate = self
        self.session = session
        session.activate()
    }

    // MARK: Inputs

    /// Reduce a local input and run its side effects.
    func send(_ input: WatchFlowInput) {
        reduce(input)
        switch input {
        case .armChosen(let arm):
            var prefs = currentPrefs
            prefs.arm = arm
            send(prefs: prefs)
        case .tapsLearned:
            defaults.set(true, forKey: Self.setupDoneDefaultsKey)
            sendToPhone(.setupDone)
        case .holdCompleted:
            WKInterfaceDevice.current().play(.stop)
            sendButton("stop")
            stopWorkout()
        case .resume:
            sendButton("resume")
        default:
            break
        }
    }

    /// "I'm ready" (tap or double-tap) — the server maps `ready` to `start`.
    func ready() {
        WKInterfaceDevice.current().play(.click)
        sendButton("ready")
    }

    /// Stopped → Resume (`watch_button resume` = engine `command start`, un-pausing the same set).
    func resume() { send(.resume) }

    /// Settings / Which Arm: tell the phone (→ `PUT /me` device_prefs) and update the flow right away.
    func send(prefs: WatchWire.WatchPrefs) {
        sendToPhone(.prefs(taps: prefs.taps, arm: prefs.arm, doubleTap: prefs.doubleTap, captions: prefs.captions))
        if var s = setup {
            s.prefs = prefs
            applySetup(s, persist: true)
        } else {
            let st = flow.state
            reduce(.setup(firstName: st.firstName, hrLimit: st.hrLimit, hrLimitSetBy: st.hrLimitSetBy,
                          therapist: st.therapistName, tremor: st.tremorMonitoring, prefs: Self.flowPrefs(prefs),
                          plan: st.plan, setupDone: st.setupStep == nil))
            motion.arm = Arm(rawValue: prefs.arm) ?? .right
        }
    }

    /// Setup 2 "Allow": HealthKit permission, then on to Which Arm whatever the answer.
    func allowHeartRate() async {
        _ = await heartRateSource.requestAuthorization()
        send(.heartRateAnswered)
    }

    /// Plays a server haptic pattern with the patient's Taps strength (W6 moves this into `HapticPlayer`).
    func playHaptic(_ pattern: String) {
        let strength = TapStrength(rawValue: flow.state.prefs.taps) ?? .normal
        for step in HapticPlan.steps(for: pattern, strength: strength) {
            let type = Self.wkType(step.kind)
            if step.delay <= 0 {
                WKInterfaceDevice.current().play(type)
            } else {
                Task { @MainActor in
                    try? await Task.sleep(nanoseconds: UInt64(step.delay * 1_000_000_000))
                    WKInterfaceDevice.current().play(type)
                }
            }
        }
    }

    var currentPrefs: WatchWire.WatchPrefs {
        let p = flow.state.prefs
        return WatchWire.WatchPrefs(taps: p.taps, arm: p.arm, doubleTap: p.doubleTap, captions: p.captions)
    }

    /// Ready / Countdown status-bar title: the live exercise, else today's next unfinished plan item, else "Mira".
    var exerciseTitle: String {
        if let name = snapshot?.exerciseName, !name.isEmpty, flow.state.sessionActive { return name }
        if let next = flow.state.plan.first(where: { $0.doneSets < $0.sets }) { return next.name }
        if let name = snapshot?.exerciseName, !name.isEmpty { return name }
        return "Mira"
    }

    private func reduce(_ input: WatchFlowInput) {
        flow.reduce(input, now: now)
        updateMotionMode()
    }

    // MARK: Outgoing

    private func send(heartRate bpm: Double, at date: Date = Date()) {
        heartRate = Int(bpm.rounded())
        sendToPhone(.heartRate(bpm: bpm, t: date.timeIntervalSince1970))
    }

    private func sendButton(_ name: String) {
        sendError = nil
        guard let session, session.activationState == .activated, session.isReachable else {
            sendError = "Open Mira on your iPhone"
            return
        }
        session.sendMessage(WatchWire.encode(.button(name: name)), replyHandler: nil) { [weak self] _ in
            Task { @MainActor in self?.sendError = "Couldn't reach your iPhone" }
        }
    }

    /// Fire-and-forget to the phone while reachable (wrist / sync / heart-rate samples are live-only);
    /// `prefs` and `setupDone` fall back to `transferUserInfo` so they arrive later.
    private func sendToPhone(_ message: WatchWire.ToPhone) {
        guard let session, session.activationState == .activated else { return }
        let payload = WatchWire.encode(message)
        if session.isReachable {
            session.sendMessage(payload, replyHandler: nil, errorHandler: { _ in })
        } else {
            switch message {
            case .prefs, .setupDone: session.transferUserInfo(payload)
            default: break
            }
        }
    }

    // MARK: Incoming

    private func handle(_ message: WatchWire.ToWatch) {
        switch message {
        case .snapshot(let s):
            apply(s)
        case .haptic(let pattern):
            reduce(.haptic(pattern))
            playHaptic(pattern)
        case .summary(let formScore, let goodReps, let repsTotal):
            summary = Summary(formScore: formScore, goodReps: goodReps, repsTotal: repsTotal)
            stopWorkout()
        case .setup(let s):
            applySetup(s, persist: true)
        case let .breather(state, bpm, limit):
            reduce(.breather(state: state, bpm: bpm, limit: limit))
        case let .recovery(setIndex, peakBpm, bpm60, drop):
            reduce(.recovery(setIndex: setIndex, peak: peakBpm, bpm60: bpm60, drop: drop))
        case let .rotationRep(exerciseId, rep, repsTarget, angle, goal, reached):
            reduce(.rotationRep(exerciseId: exerciseId, rep: rep, repsTarget: repsTarget, angle: angle, goal: goal,
                                reached: reached))
        case let .rotationSet(exerciseId, _, angles, best, avg, goal):
            reduce(.rotationSet(exerciseId: exerciseId, angles: angles, best: best, avg: avg, goal: goal))
        case let .sync(progress, locked):
            let wasLocked = flow.state.syncLocked
            reduce(.sync(progress: progress, locked: locked))
            if locked, !wasLocked { WKInterfaceDevice.current().play(.click) }   // light click on lock
        case .caption(let text):
            caption = text
        case let .formCue(text, rep):
            reduce(.formCue(text: text, rep: rep))
        }
    }

    private func applySetup(_ s: WatchWire.Setup, persist: Bool) {
        setup = s
        if persist { defaults.set(WatchWire.encode(.setup(s)), forKey: Self.setupDefaultsKey) }
        motion.arm = Arm(rawValue: s.prefs.arm) ?? .right
        reduce(.setup(firstName: s.firstName, hrLimit: s.hrLimit, hrLimitSetBy: s.hrLimitSetBy,
                      therapist: s.therapistName, tremor: s.tremorMonitoring, prefs: Self.flowPrefs(s.prefs),
                      plan: s.plan.map(Self.flowPlan), setupDone: defaults.bool(forKey: Self.setupDoneDefaultsKey)))
    }

    private func apply(_ new: WatchWire.LiveSnapshot) {
        let previousPhase = snapshot?.phase
        snapshot = new
        if new.phase != previousPhase { sendError = nil }
        reduce(.snapshot(phase: new.phase, rep: new.rep, repsTarget: new.repsTarget, set: new.set,
                         setsTarget: new.setsTarget, goodReps: new.goodReps, countdown: new.countdown,
                         restRemaining: new.restRemaining, exerciseId: new.exerciseId,
                         isWatchExercise: new.isWatchExercise ?? false, hr: new.hr, paused: new.paused))
        // Heart rate (as Plan 4): the workout runs while a set is under way.
        switch new.phase {
        case "countdown", "active":
            if previousPhase == "idle" || previousPhase == "done" || previousPhase == nil { summary = nil }
            startWorkout()
        case "done", "idle":
            stopWorkout()
        default:
            break   // rest / paused / incident keep whatever the workout is doing
        }
    }

    private func startWorkout() {
        #if DEBUG
        if isPreview { return }
        #endif
        heartRateSource.start()
    }

    private func stopWorkout() {
        heartRateSource.end()
    }

    private func heartRateStateChanged() {
        heartRateStatus = heartRateSource.status
        if workoutActive != heartRateSource.isRunning {
            workoutActive = heartRateSource.isRunning
            if !workoutActive { heartRate = nil }
            sendToPhone(.status(workoutActive: workoutActive))
        }
    }

    // MARK: Clock + sensors

    private func startTicker() {
        guard ticker == nil else { return }
        ticker = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 500_000_000)
                guard let self else { return }
                self.now = Date().timeIntervalSince1970
                self.reduce(.tick)
            }
        }
    }

    /// `.sync` on Sync Wave; `.rotation` on Start Position / Live Turn / Elbow Drift; otherwise off.
    private func updateMotionMode() {
        #if DEBUG
        if isPreview { return }
        #endif
        let mode: MotionService.Mode
        switch route {
        case .syncWave:
            mode = .sync
        case .startPosition, .liveTurn, .elbowDrift:
            if case .rotation = motion.mode { mode = .rotation(motion.zero) } else { mode = .rotation(nil) }
        default:
            mode = .off
        }
        if case .rotation = mode {} else if stillness != 0 { stillness = 0 }
        motion.setMode(mode)
    }

    // MARK: Mapping

    static func flowPrefs(_ p: WatchWire.WatchPrefs) -> WatchFlowState.Prefs {
        WatchFlowState.Prefs(taps: p.taps, arm: p.arm, doubleTap: p.doubleTap, captions: p.captions)
    }

    static func flowPlan(_ p: WatchWire.PlanItem) -> WatchFlowState.PlanEntry {
        WatchFlowState.PlanEntry(id: p.id, name: p.name, sets: p.sets, reps: p.reps, doneSets: p.doneSets,
                                 watch: p.watch, goalDeg: p.goalDeg)
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

    // MARK: WCSessionDelegate

    nonisolated func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
        let reachable = session.isReachable
        let initial = WatchWire.decodeToWatch(session.receivedApplicationContext)
        Task { @MainActor in
            self.reduce(.reachability(reachable))
            if let initial, self.snapshot == nil { self.handle(initial) }
        }
    }

    nonisolated func sessionReachabilityDidChange(_ session: WCSession) {
        let reachable = session.isReachable
        Task { @MainActor in self.reduce(.reachability(reachable)) }
    }

    nonisolated func session(_ session: WCSession, didReceiveApplicationContext applicationContext: [String: Any]) {
        guard let decoded = WatchWire.decodeToWatch(applicationContext) else { return }
        Task { @MainActor in self.handle(decoded) }
    }

    nonisolated func session(_ session: WCSession, didReceiveMessage message: [String: Any]) {
        guard let decoded = WatchWire.decodeToWatch(message) else { return }
        Task { @MainActor in self.handle(decoded) }
    }

    /// The phone also sends the setup context with `transferUserInfo` (queued while the watch app is closed).
    nonisolated func session(_ session: WCSession, didReceiveUserInfo userInfo: [String: Any]) {
        guard let decoded = WatchWire.decodeToWatch(userInfo) else { return }
        Task { @MainActor in self.handle(decoded) }
    }
}

#if DEBUG
extension WatchSessionModel {
    /// Loads a `WatchPreview` scenario: sample setup, phone messages and local inputs, frozen clock, no phone link.
    /// Uses a throwaway defaults suite so previews never touch the real setup state. False for an unknown name.
    @discardableResult
    func loadPreview(_ name: String) -> Bool {
        guard let scenario = WatchPreview.scenario(name) else { return false }
        isPreview = true
        let suite = "mira.watch.preview"
        UserDefaults().removePersistentDomain(forName: suite)
        defaults = UserDefaults(suiteName: suite) ?? .standard
        defaults.set(scenario.setupDone, forKey: Self.setupDoneDefaultsKey)
        flow = WatchFlowReducer()
        snapshot = nil
        now = WatchPreview.clock
        applySetup(scenario.setup, persist: false)
        for message in scenario.messages { handle(message) }
        for input in scenario.inputs { reduce(input) }
        heartRate = scenario.heartRate
        previewHoldProgress = scenario.holdProgress
        stillness = scenario.stillness
        return true
    }
}
#endif
