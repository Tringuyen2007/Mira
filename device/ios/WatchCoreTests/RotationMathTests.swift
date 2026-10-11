import XCTest
@testable import MiraWatchCore

final class RotationMathTests: XCTestCase {
    func testTwistAboutAxisIsolatesRotation() {
        let ax = SIMD3<Double>(1, 0, 0)
        let q = Quat(axis: ax, degrees: 70)
        XCTAssertEqual(RotationMath.twistDegrees(q, about: ax), 70, accuracy: 0.01)
        let swing = Quat(axis: SIMD3(0, 1, 0), degrees: 30)       // swing does not count
        XCTAssertEqual(RotationMath.twistDegrees(swing * q, about: ax), 70, accuracy: 1.0)
    }

    func testTwistIsSignedAndWrapped() {
        let ax = SIMD3<Double>(1, 0, 0)
        XCTAssertEqual(RotationMath.twistDegrees(Quat(axis: ax, degrees: -45), about: ax), -45, accuracy: 0.01)
        XCTAssertEqual(RotationMath.twistDegrees(Quat(axis: ax, degrees: 190), about: ax), -170, accuracy: 0.01)
        XCTAssertEqual(RotationMath.twistDegrees(Quat(axis: ax, degrees: 180), about: ax), 180, accuracy: 0.01)
        XCTAssertEqual(RotationMath.twistDegrees(.identity, about: ax), 0, accuracy: 0.0001)
    }

    func testQuatAlgebra() {
        let q = Quat(axis: SIMD3(0, 0, 1), degrees: 33)
        let p = q.conjugate * q
        XCTAssertEqual(p.w, 1, accuracy: 1e-9); XCTAssertEqual(p.z, 0, accuracy: 1e-9)
    }

    func testForearmAxisBySide() {
        XCTAssertEqual(RotationMath.forearmAxis(.left), SIMD3(1, 0, 0))
        XCTAssertEqual(RotationMath.forearmAxis(.right), SIMD3(-1, 0, 0))
    }

    func testZeroingAndArmSign() {
        let ref = Quat(axis: SIMD3(1, 0, 0), degrees: 15)
        let z = RotationZero(reference: ref, arm: .left)
        XCTAssertEqual(z.degrees(ref), 0, accuracy: 0.01)
        XCTAssertEqual(z.degrees(ref * Quat(axis: SIMD3(1, 0, 0), degrees: 40)), 40, accuracy: 0.01)
        XCTAssertEqual(RotationZero(reference: ref, arm: .right).degrees(ref * Quat(axis: SIMD3(1, 0, 0), degrees: 40)), -40, accuracy: 0.01)
    }

    func testStillnessNeeds1_5s() {
        var s = StillnessDetector()
        XCTAssertEqual(s.add(rotationRate: 0.01, t: 0), 0, accuracy: 0.01)
        XCTAssertEqual(s.add(rotationRate: 0.01, t: 0.75), 0.5, accuracy: 0.01)
        XCTAssertEqual(s.add(rotationRate: 0.9, t: 1.0), 0)          // motion resets
        XCTAssertEqual(s.add(rotationRate: 0.01, t: 1.2), 0, accuracy: 0.01)
        XCTAssertEqual(s.add(rotationRate: 0.01, t: 3.0), 1, accuracy: 0.01)   // clamps at 1
    }

    func testRepMirror() {
        var m = RotationRepMirror(goal: 80)
        [0, 30, 55, 70, 40, 10].forEach { XCTAssertNil(m.add($0)) }
        XCTAssertEqual(m.add(5), 70)
        // Small wiggles below 60 % of goal never count.
        [20, 40, 10, 5, 0].forEach { XCTAssertNil(m.add($0)) }
    }

    func testRepMirrorNegativeExcursion() {
        var m = RotationRepMirror(goal: 80)
        [0, -50, -66, -30, 8].forEach { XCTAssertNil(m.add($0)) }
        XCTAssertEqual(m.add(3), 66)
    }
}
