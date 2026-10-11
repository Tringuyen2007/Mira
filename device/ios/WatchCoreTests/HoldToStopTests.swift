import XCTest
@testable import MiraWatchCore

final class HoldToStopTests: XCTestCase {
    func testEarlyReleaseCancelsFullHoldCompletes() {
        var h = HoldToStop()
        XCTAssertFalse(h.isHolding)
        XCTAssertEqual(h.progress(at: 5), 0)
        h.begin(at: 0)
        XCTAssertTrue(h.isHolding)
        XCTAssertEqual(h.progress(at: 0.75), 0.5, accuracy: 1e-9)
        XCTAssertFalse(h.end(at: 1.0))
        XCTAssertFalse(h.isHolding)
        h.begin(at: 2)
        XCTAssertEqual(h.progress(at: 9), 1)
        XCTAssertTrue(h.end(at: 3.6))
        XCTAssertFalse(h.end(at: 4))                        // not holding → false
    }
}
