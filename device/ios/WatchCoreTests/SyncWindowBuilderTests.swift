import XCTest
@testable import MiraWatchCore

final class SyncWindowBuilderTests: XCTestCase {
    func testOneWindowPerSecond() {
        var b = SyncWindowBuilder()
        var windows: [(Int, SyncWindow)] = []
        for i in 0...50 {                                   // 0.00 … 1.00 s at 20 ms
            if let w = b.add(t: 100 + Double(i) * 0.02, mag: Double(i)) { windows.append((i, w)) }
        }
        XCTAssertEqual(windows.count, 1)
        XCTAssertEqual(windows[0].0, 50)                     // emitted on the sample that crosses 1 s
        XCTAssertEqual(windows[0].1.t0, 100, accuracy: 1e-9)
        XCTAssertEqual(windows[0].1.dtMs, 20)
        XCTAssertEqual(windows[0].1.mags.count, 50)
        XCTAssertEqual(windows[0].1.mags.first, 0)
    }

    func testCapsSamplesAndStartsNextWindowAtCrossingSample() {
        var b = SyncWindowBuilder()
        var out: [SyncWindow] = []
        for i in 0...200 {                                   // 100 Hz for 2 s
            if let w = b.add(t: Double(i) * 0.01, mag: 1) { out.append(w) }
        }
        XCTAssertEqual(out.count, 2)
        XCTAssertTrue(out.allSatisfy { $0.mags.count <= 50 })
        XCTAssertEqual(out[1].t0, 1.0, accuracy: 1e-6)
    }
}
