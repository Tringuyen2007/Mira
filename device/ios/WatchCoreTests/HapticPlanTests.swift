import XCTest
@testable import MiraWatchCore

final class HapticPlanTests: XCTestCase {
    func testPatterns() {
        XCTAssertEqual(HapticPlan.steps(for: "good_rep", strength: .normal), [.init(kind: .success, delay: 0)])
        XCTAssertEqual(HapticPlan.steps(for: "fix_form", strength: .normal).count, 2)
        XCTAssertEqual(HapticPlan.steps(for: "set_done", strength: .normal).count, 3)
        XCTAssertEqual(HapticPlan.steps(for: "countdown_tick", strength: .normal).map(\.kind), [.click])
        XCTAssertEqual(HapticPlan.steps(for: "fix_form", strength: .strong).count, 4)
        XCTAssertTrue(HapticPlan.steps(for: "nope", strength: .strong).isEmpty)
    }

    func testTimings() {
        XCTAssertEqual(HapticPlan.steps(for: "fix_form", strength: .normal),
                       [.init(kind: .failure, delay: 0), .init(kind: .failure, delay: 0.30)])
        XCTAssertEqual(HapticPlan.steps(for: "set_done", strength: .normal).map(\.delay), [0, 0.15, 0.30])
        XCTAssertEqual(HapticPlan.steps(for: "check_in", strength: .normal).map(\.kind), [.retry])
        XCTAssertEqual(HapticPlan.steps(for: "breather", strength: .normal),
                       [.init(kind: .directionDown, delay: 0), .init(kind: .directionDown, delay: 0.6)])
    }

    func testStrongDoublesEachStep() {
        let s = HapticPlan.steps(for: "fix_form", strength: .strong)
        XCTAssertEqual(s.map(\.kind), [.failure, .failure, .failure, .failure])
        XCTAssertEqual(s[0].delay, 0, accuracy: 1e-9); XCTAssertEqual(s[1].delay, 0.12, accuracy: 1e-9)
        XCTAssertEqual(s[2].delay, 0.30, accuracy: 1e-9); XCTAssertEqual(s[3].delay, 0.42, accuracy: 1e-9)
        XCTAssertEqual(TapStrength(rawValue: "strong"), .strong)
    }
}
