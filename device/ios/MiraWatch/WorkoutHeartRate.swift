import Foundation
import HealthKit

/// Runs an `HKWorkoutSession` while a Mira set is live so the watch samples heart
/// rate continuously, and hands each new reading to `onHeartRate` (at most once
/// every `minInterval`). Every HealthKit failure is swallowed into `status`, which
/// the heart-rate card shows under "—": no permission, or no sensor (simulator).
@MainActor
final class WorkoutHeartRate: NSObject {
    /// Human-readable reason heart rate is unavailable, or nil when all is well.
    private(set) var status: String?
    private(set) var isRunning = false

    /// Called on the main actor with (bpm, sample time).
    var onHeartRate: ((Double, Date) -> Void)?
    /// Called on the main actor whenever `isRunning` or `status` changes.
    var onStateChange: (() -> Void)?

    private let store = HKHealthStore()
    private var session: HKWorkoutSession?
    private var builder: HKLiveWorkoutBuilder?
    private var lastDeliveredAt: Date?
    private let minInterval: TimeInterval = 3

    static let needsPermission = "Heart rate needs permission in the Watch app"
    static let needsRealWatch = "Heart rate needs a real Apple Watch"
    static let unavailable = "Heart rate isn't available"

    /// Setup 2 "Allow": asks for the same HealthKit access a workout needs (heart-rate read, workout share).
    /// True when the request completed (HealthKit never reveals whether *read* access was granted);
    /// false when health data is unavailable or the request failed.
    func requestAuthorization() async -> Bool {
        guard HKHealthStore.isHealthDataAvailable() else { return false }
        do {
            try await store.requestAuthorization(toShare: [HKObjectType.workoutType()], read: [HKQuantityType(.heartRate)])
            return true
        } catch {
            return false
        }
    }

    func start() {
        guard !isRunning else { return }
        #if targetEnvironment(simulator)
        let healthAvailable = false  // the simulator never produces heart-rate samples
        #else
        let healthAvailable = HKHealthStore.isHealthDataAvailable()
        #endif
        guard healthAvailable else {
            setStatus(Self.needsRealWatch)
            return
        }
        isRunning = true
        lastDeliveredAt = nil
        onStateChange?()
        Task { await begin() }
    }

    func end() {
        guard isRunning else { return }
        isRunning = false
        onStateChange?()
        let session = self.session
        let builder = self.builder
        self.session = nil
        self.builder = nil
        guard let session, let builder else { return }
        session.end()
        Task {
            do {
                try await builder.endCollection(at: Date())
                _ = try await builder.finishWorkout()
            } catch {
                // Best-effort: a workout that fails to save is not worth surfacing.
            }
        }
    }

    private func begin() async {
        let heartRate = HKQuantityType(.heartRate)
        do {
            try await store.requestAuthorization(toShare: [HKObjectType.workoutType()], read: [heartRate])
        } catch {
            setStatus(isRunning ? Self.unavailable : nil)
            return
        }
        // `end()` may have been called while the permission sheet was up.
        guard isRunning else { setStatus(nil); return }
        guard session == nil else { return }
        // HealthKit never says whether *read* access was denied, but a denied workout
        // share means no workout session (so no samples) — say so instead of waiting.
        if store.authorizationStatus(for: HKObjectType.workoutType()) == .sharingDenied {
            isRunning = false
            onStateChange?()
            setStatus(Self.needsPermission)
            return
        }
        let configuration = HKWorkoutConfiguration()
        configuration.activityType = .other
        configuration.locationType = .indoor
        do {
            let session = try HKWorkoutSession(healthStore: store, configuration: configuration)
            let builder = session.associatedWorkoutBuilder()
            builder.dataSource = HKLiveWorkoutDataSource(healthStore: store, workoutConfiguration: configuration)
            session.delegate = self
            builder.delegate = self
            self.session = session
            self.builder = builder
            let start = Date()
            session.startActivity(with: start)
            try await builder.beginCollection(at: start)
            setStatus(nil)
        } catch {
            // An `end()` that interrupted the start is not a failure worth showing.
            setStatus(isRunning ? Self.unavailable : nil)
        }
    }

    private func deliver(bpm: Double, at date: Date) {
        guard isRunning else { return }
        let now = Date()
        if let lastDeliveredAt, now.timeIntervalSince(lastDeliveredAt) < minInterval { return }
        lastDeliveredAt = now
        onHeartRate?(bpm, date)
    }

    private func setStatus(_ status: String?) {
        guard self.status != status else { return }
        self.status = status
        onStateChange?()
    }
}

extension WorkoutHeartRate: HKLiveWorkoutBuilderDelegate {
    nonisolated func workoutBuilder(_ workoutBuilder: HKLiveWorkoutBuilder, didCollectDataOf collectedTypes: Set<HKSampleType>) {
        let heartRate = HKQuantityType(.heartRate)
        guard collectedTypes.contains(heartRate),
              let statistics = workoutBuilder.statistics(for: heartRate),
              let quantity = statistics.mostRecentQuantity() else { return }
        let bpm = quantity.doubleValue(for: HKUnit.count().unitDivided(by: .minute()))
        let date = statistics.mostRecentQuantityDateInterval()?.end ?? Date()
        Task { @MainActor in self.deliver(bpm: bpm, at: date) }
    }

    nonisolated func workoutBuilderDidCollectEvent(_ workoutBuilder: HKLiveWorkoutBuilder) {}
}

extension WorkoutHeartRate: HKWorkoutSessionDelegate {
    nonisolated func workoutSession(_ workoutSession: HKWorkoutSession, didChangeTo toState: HKWorkoutSessionState, from fromState: HKWorkoutSessionState, date: Date) {}

    nonisolated func workoutSession(_ workoutSession: HKWorkoutSession, didFailWithError error: Error) {
        Task { @MainActor in self.setStatus(self.isRunning ? Self.unavailable : nil) }
    }
}
