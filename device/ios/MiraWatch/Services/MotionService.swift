import CoreMotion
import Foundation

/// CoreMotion device motion at 50 Hz on a private serial queue. What it emits depends on the mode:
/// - `.sync` (Setup 4 Sync Wave): one `SyncWindow` of user-acceleration magnitudes per second (→ `syncWindow`).
/// - `.rotation(zero)` (Start Position / Live Turn / Elbow Drift): wrist samples at 10 Hz with the twist from the
///   captured zero (→ `wrist`), plus Start Position stillness progress.
/// - `.off`: sensors stopped.
/// All maths lives in Core (`SyncWindowBuilder`, `RotationZero`, `StillnessDetector`); this class only feeds it.
/// Callbacks are delivered on the main actor.
@MainActor
final class MotionService {
    enum Mode: Equatable {
        case off
        case rotation(RotationZero?)
        case sync

        static func == (a: Mode, b: Mode) -> Bool {
            switch (a, b) {
            case (.off, .off), (.sync, .sync): return true
            case let (.rotation(x), .rotation(y)): return x?.reference == y?.reference && x?.arm == y?.arm
            default: return false
            }
        }

        fileprivate var kind: Int {
            switch self {
            case .off: return 0
            case .rotation: return 1
            case .sync: return 2
            }
        }
    }

    nonisolated static let sampleHz = 50.0
    nonisolated static let wristHz = 10.0

    var onWrist: ((_ t: Double, _ quat: [Double], _ accel: [Double], _ deg: Double?) -> Void)?
    var onSyncWindow: ((SyncWindow) -> Void)?
    var onStillness: ((Double) -> Void)?
    /// Which wrist the watch is on (Settings → Watch arm); used when zeroing.
    var arm: Arm = .right {
        didSet { let arm = self.arm; queue.addOperation { [processor] in processor.arm = arm } }
    }

    private(set) var mode: Mode = .off
    /// The zero captured by the last `zeroHere()`, if any (published back from the motion queue).
    private(set) var zero: RotationZero?

    private let manager = CMMotionManager()
    private let queue: OperationQueue = {
        let q = OperationQueue()
        q.name = "mira.motion"
        q.maxConcurrentOperationCount = 1
        q.qualityOfService = .userInitiated
        return q
    }()
    private let processor = MotionProcessor()

    init() {}

    func setMode(_ newMode: Mode) {
        guard newMode != mode else { return }
        let restart = newMode.kind != mode.kind
        if case .rotation(let z) = newMode {
            if let z { zero = z } else if restart { zero = nil }   // a fresh rotation mode starts unzeroed
        }
        mode = newMode
        let snapshot = newMode
        queue.addOperation { [processor] in processor.configure(snapshot) }
        guard restart else { return }
        if case .off = newMode {
            manager.stopDeviceMotionUpdates()
            return
        }
        guard manager.isDeviceMotionAvailable else { return }   // simulator: no sensors, nothing emitted
        if manager.isDeviceMotionActive { return }
        manager.deviceMotionUpdateInterval = 1 / Self.sampleHz
        manager.startDeviceMotionUpdates(using: .xArbitraryZVertical, to: queue,
                                         withHandler: Self.handler(processor: processor, owner: self))
    }

    /// Built outside the main actor: CoreMotion calls it on `queue`.
    nonisolated private static func handler(processor: MotionProcessor, owner: MotionService) -> CMDeviceMotionHandler {
        { [weak owner] motion, _ in
            guard let motion else { return }
            let events = processor.handle(motion)
            guard !events.isEmpty else { return }
            Task { @MainActor in owner?.deliver(events) }
        }
    }

    /// Captures the current attitude as the rotation zero (Start Position).
    func zeroHere() {
        queue.addOperation { [weak self, processor] in
            guard let zero = processor.zeroHere() else { return }
            Task { @MainActor in self?.zero = zero }
        }
    }

    private func deliver(_ events: [MotionProcessor.Event]) {
        for event in events {
            switch event {
            case let .wrist(t, quat, accel, deg): onWrist?(t, quat, accel, deg)
            case .syncWindow(let w): onSyncWindow?(w)
            case .stillness(let p): onStillness?(p)
            }
        }
    }
}

/// Per-sample work, touched only on the motion queue.
private final class MotionProcessor: @unchecked Sendable {
    enum Event {
        case wrist(t: Double, quat: [Double], accel: [Double], deg: Double?)
        case syncWindow(SyncWindow)
        case stillness(Double)
    }

    var arm: Arm = .right
    private var kind = 0                  // Mode.kind
    private var zero: RotationZero?
    private var sync = SyncWindowBuilder(dtMs: Int(1000 / MotionService.sampleHz))
    private var stillness = StillnessDetector()
    private var lastWristAt: Double?
    private var lastStillness: Double = -1
    private var lastQuat: Quat?
    /// motion.timestamp is seconds since boot; this turns it into epoch seconds.
    private let bootEpoch = Date().timeIntervalSince1970 - ProcessInfo.processInfo.systemUptime

    func configure(_ mode: MotionService.Mode) {
        let previousKind = kind
        kind = mode.kind
        switch mode {
        case .off:
            sync.reset(); stillness.reset(); lastWristAt = nil; lastStillness = -1
        case .sync:
            sync.reset()
        case .rotation(let z):
            if let z { zero = z } else if previousKind != 1 { zero = nil }
            stillness.reset(); lastWristAt = nil; lastStillness = -1
        }
    }

    func zeroHere() -> RotationZero? {
        guard let q = lastQuat else { return nil }
        let z = RotationZero(reference: q, arm: arm)
        zero = z
        return z
    }

    func handle(_ motion: CMDeviceMotion) -> [Event] {
        let t = bootEpoch + motion.timestamp
        let a = motion.attitude.quaternion
        let q = Quat(w: a.w, x: a.x, y: a.y, z: a.z)
        lastQuat = q
        let ua = motion.userAcceleration
        var events: [Event] = []
        switch kind {
        case 2:
            let mag = (ua.x * ua.x + ua.y * ua.y + ua.z * ua.z).squareRoot()
            if let w = sync.add(t: t, mag: mag) { events.append(.syncWindow(w)) }
        case 1:
            let r = motion.rotationRate
            let progress = stillness.add(rotationRate: (r.x * r.x + r.y * r.y + r.z * r.z).squareRoot(), t: t)
            if abs(progress - lastStillness) >= 0.02 || (progress == 1) != (lastStillness == 1) {
                lastStillness = progress
                events.append(.stillness(progress))
            }
            if lastWristAt == nil || t - lastWristAt! >= 1 / MotionService.wristHz - 1e-6 {
                lastWristAt = t
                events.append(.wrist(t: t, quat: [q.w, q.x, q.y, q.z], accel: [ua.x, ua.y, ua.z],
                                     deg: zero.map { $0.degrees(q) }))
            }
        default:
            break
        }
        return events
    }
}
