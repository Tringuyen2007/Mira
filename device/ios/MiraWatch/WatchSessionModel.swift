import Foundation
import Observation
import WatchConnectivity
import WatchKit

/// The watch side of the Mira link. Receives the live snapshot
/// (`updateApplicationContext`), coaching haptics and the end-of-set summary
/// (`sendMessage`) from the iPhone's `WatchBridge`; sends Ready/Stop button taps and
/// heart-rate samples back. All wire shapes come from the shared `WatchWire` codec.
@MainActor
@Observable
final class WatchSessionModel: NSObject, WCSessionDelegate {
    struct Summary: Equatable {
        var formScore: Int?
        var goodReps: Int?
        var repsTotal: Int?
    }

    private(set) var snapshot: WatchWire.LiveSnapshot?
    private(set) var lastHaptic: String?
    private(set) var summary: Summary?
    private(set) var phoneReachable = false
    /// The most recent heart rate measured on this watch, whole bpm.
    private(set) var localHeartRate: Int?
    private(set) var heartRateStatus: String?
    private(set) var workoutActive = false
    /// Shown briefly when a button tap could not reach the iPhone.
    private(set) var sendError: String?

    @ObservationIgnored private var session: WCSession?
    @ObservationIgnored private let heartRate = WorkoutHeartRate()

    override init() {
        super.init()
        heartRate.onHeartRate = { [weak self] bpm, date in self?.send(heartRate: bpm, at: date) }
        heartRate.onStateChange = { [weak self] in self?.heartRateStateChanged() }
    }

    func activate() {
        guard session == nil, WCSession.isSupported() else { return }
        let session = WCSession.default
        session.delegate = self
        self.session = session
        session.activate()
    }

    // MARK: Outgoing

    /// "I'm ready" / Resume — the server maps `ready` to `start`.
    func ready() {
        WKInterfaceDevice.current().play(.click)
        sendButton("ready")
    }

    /// Stop ends the workout locally right away; the phone's idle snapshot follows.
    func stop() {
        WKInterfaceDevice.current().play(.stop)
        sendButton("stop")
        heartRate.end()
    }

    func send(heartRate bpm: Double, at date: Date = Date()) {
        localHeartRate = Int(bpm.rounded())
        guard let session, session.isReachable else { return }
        let message = WatchWire.encode(.heartRate(bpm: bpm, t: date.timeIntervalSince1970))
        session.sendMessage(message, replyHandler: nil, errorHandler: { _ in })
    }

    /// Leaves the summary screen locally; the phone's next snapshot takes over.
    func dismissSummary() {
        summary = nil
        if snapshot?.phase == "done" { snapshot?.phase = "idle" }
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

    private func sendStatus() {
        guard let session, session.isReachable else { return }
        session.sendMessage(WatchWire.encode(.status(workoutActive: workoutActive)), replyHandler: nil, errorHandler: { _ in })
    }

    // MARK: Incoming

    private func handle(_ message: WatchWire.ToWatch) {
        switch message {
        case .snapshot(let snapshot):
            apply(snapshot)
        case .haptic(let pattern):
            lastHaptic = pattern
            playHaptic(pattern)
        case .summary(let formScore, let goodReps, let repsTotal):
            // The session is over: end the workout and show the done screen.
            summary = Summary(formScore: formScore, goodReps: goodReps, repsTotal: repsTotal)
            heartRate.end()
        }
    }

    private func apply(_ new: WatchWire.LiveSnapshot) {
        let previous = snapshot
        let previousPhase = previous?.phase
        snapshot = new
        if new.phase != previousPhase { sendError = nil }
        switch new.phase {
        case "countdown", "active":
            if previousPhase == "idle" || previousPhase == "done" || previousPhase == nil { summary = nil }
            heartRate.start()
        case "done", "idle":
            heartRate.end()
            if new.phase == "idle" { localHeartRate = nil }
            // The phone pushes idle as soon as the session ends (the server clears it
            // right after `done`); keep the done screen up with the last count until
            // the summary arrives or the patient taps "Back to Mira".
            if summary == nil, let previous, Self.sessionPhases.contains(previous.phase) {
                summary = Summary(formScore: nil, goodReps: nil, repsTotal: previous.rep)
            }
        default:
            break  // rest / paused / incident keep whatever the workout is doing
        }
    }

    /// Phases during which a session is under way (or just finished).
    static let sessionPhases: Set<String> = ["countdown", "active", "rest", "paused", "incident", "done"]

    private func playHaptic(_ pattern: String) {
        let device = WKInterfaceDevice.current()
        switch pattern {
        case "good_rep":
            device.play(.success)
        case "fix_form":
            device.play(.retry)
            Task {
                try? await Task.sleep(nanoseconds: 150_000_000)
                WKInterfaceDevice.current().play(.retry)
            }
        case "set_done":
            device.play(.notification)
        default:
            device.play(.directionUp)
        }
    }

    private func heartRateStateChanged() {
        heartRateStatus = heartRate.status
        if workoutActive != heartRate.isRunning {
            workoutActive = heartRate.isRunning
            sendStatus()
        }
    }

    // MARK: WCSessionDelegate

    nonisolated func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
        let reachable = session.isReachable
        let initial = WatchWire.decodeToWatch(session.receivedApplicationContext)
        Task { @MainActor in
            self.phoneReachable = reachable
            if let initial, self.snapshot == nil { self.handle(initial) }
        }
    }

    nonisolated func sessionReachabilityDidChange(_ session: WCSession) {
        let reachable = session.isReachable
        Task { @MainActor in self.phoneReachable = reachable }
    }

    nonisolated func session(_ session: WCSession, didReceiveApplicationContext applicationContext: [String: Any]) {
        guard let decoded = WatchWire.decodeToWatch(applicationContext) else { return }
        Task { @MainActor in self.handle(decoded) }
    }

    nonisolated func session(_ session: WCSession, didReceiveMessage message: [String: Any]) {
        guard let decoded = WatchWire.decodeToWatch(message) else { return }
        Task { @MainActor in self.handle(decoded) }
    }
}
