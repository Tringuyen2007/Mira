import Foundation

/// Unit quaternion (w + xi + yj + zk). Matches CMQuaternion's layout so MotionService can copy fields directly.
struct Quat: Equatable {
    var w, x, y, z: Double

    init(w: Double, x: Double, y: Double, z: Double) {
        self.w = w; self.x = x; self.y = y; self.z = z
    }

    /// Rotation of `degrees` about `axis` (normalised here).
    init(axis: SIMD3<Double>, degrees: Double) {
        let len = (axis * axis).sum().squareRoot()
        let a = len > 0 ? axis / len : SIMD3<Double>(1, 0, 0)
        let half = degrees * .pi / 360
        let s = sin(half)
        self.init(w: cos(half), x: a.x * s, y: a.y * s, z: a.z * s)
    }

    static let identity = Quat(w: 1, x: 0, y: 0, z: 0)

    var conjugate: Quat { Quat(w: w, x: -x, y: -y, z: -z) }

    static func * (a: Quat, b: Quat) -> Quat {
        Quat(w: a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z,
             x: a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
             y: a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
             z: a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w)
    }
}

enum Arm: String { case left, right }

enum RotationMath {
    /// Forearm axis in device coordinates: +x toward the hand on the left wrist, −x on the right, so that
    /// supination reads positive on both sides. TODO(device): confirm the sign on a real watch per wrist.
    static func forearmAxis(_ arm: Arm) -> SIMD3<Double> {
        arm == .left ? SIMD3(1, 0, 0) : SIMD3(-1, 0, 0)
    }

    /// Swing–twist decomposition: the signed rotation of `q` about `axis`, in (−180, 180].
    static func twistDegrees(_ q: Quat, about axis: SIMD3<Double>) -> Double {
        let len = (axis * axis).sum().squareRoot()
        guard len > 0 else { return 0 }
        let a = axis / len
        var d = q.x * a.x + q.y * a.y + q.z * a.z     // vector part projected on the axis
        var w = q.w
        if w < 0 { w = -w; d = -d }                    // q and −q are the same rotation
        guard abs(d) > 1e-12 || w > 1e-12 else { return 0 }
        var deg = 2 * atan2(d, w) * 180 / .pi          // [−180, 180]
        if deg <= -180 { deg += 360 }
        return deg
    }
}

/// Twist relative to a captured start pose (Start Position screen).
struct RotationZero {
    let reference: Quat
    let arm: Arm

    init(reference: Quat, arm: Arm) {
        self.reference = reference
        self.arm = arm
    }

    func degrees(_ q: Quat) -> Double {
        RotationMath.twistDegrees(reference.conjugate * q, about: RotationMath.forearmAxis(arm))
    }
}

/// Start Position ring: progress 0…1 while the wrist stays still; any motion resets.
struct StillnessDetector {
    let threshold: Double
    let duration: TimeInterval
    private var stillSince: TimeInterval?

    init(threshold: Double = 0.15 /* rad/s */, duration: TimeInterval = 1.5) {
        self.threshold = threshold
        self.duration = duration
    }

    mutating func add(rotationRate: Double, t: TimeInterval) -> Double {
        guard abs(rotationRate) <= threshold else { stillSince = nil; return 0 }
        let start = stillSince ?? t
        stillSince = start
        guard duration > 0 else { return 1 }
        return min(max((t - start) / duration, 0), 1)
    }

    mutating func reset() { stillSince = nil }
}

/// UI-only mirror of the server's rotation rep rule, for an instant gauge response. The server's `rotation_rep`
/// stays authoritative for the count. A rep = excursion ≥ 60 % of goal, then back within 15° of zero; the return
/// must hold for two consecutive samples so one noisy sample near zero doesn't end the rep early.
struct RotationRepMirror {
    let goal: Double
    static let armFraction = 0.6
    static let returnBand = 15.0

    private var peak = 0.0
    private var armed = false
    private var samplesInBand = 0

    init(goal: Double) { self.goal = goal }

    /// Returns the rep's peak |angle| when a rep completes.
    mutating func add(_ deg: Double) -> Double? {
        let mag = abs(deg)
        if mag >= Self.armFraction * abs(goal) { armed = true }
        if armed {
            peak = max(peak, mag)
            if mag <= Self.returnBand {
                samplesInBand += 1
                if samplesInBand >= 2 {
                    let done = peak
                    peak = 0; armed = false; samplesInBand = 0
                    return done
                }
            } else {
                samplesInBand = 0
            }
        }
        return nil
    }
}
