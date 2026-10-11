import XCTest
@testable import MiraWatchCore

final class TremorSummarizerTests: XCTestCase {
    private let base = Date(timeIntervalSince1970: 1_791_590_400)   // 2026-10-11 00:00 UTC

    private func iv(_ startMin: Int, _ minutes: Int, _ level: TremorLevel) -> TremorInterval {
        TremorInterval(start: base.addingTimeInterval(Double(startMin) * 60),
                       end: base.addingTimeInterval(Double(startMin + minutes) * 60), level: level)
    }

    func testHighestLevelWithTenMinutesWins() {
        let s = TremorSummarizer.summarize(day: "2026-10-11", intervals: [iv(420, 190, .slight), iv(700, 25, .mild)],
                                           dyskineticPercent: 5, source: "simulated")
        XCTAssertEqual(s.headline, .mild)
        XCTAssertEqual(s.headlineText, "Mild for 25 min")
        XCTAssertEqual(s.minutes[.slight], 190)
        XCTAssertEqual(s.minutes[.mild], 25)
        XCTAssertEqual(s.dyskinesia, "few")
        XCTAssertEqual(s.extraMovementsText, "Extra movements: few")
        XCTAssertEqual(s.source, "simulated")
        XCTAssertEqual(s.day, "2026-10-11")
    }

    func testShortHigherLevelIgnored() {
        let s = TremorSummarizer.summarize(day: "d", intervals: [iv(420, 5, .mild), iv(500, 190, .slight)],
                                           dyskineticPercent: nil, source: "real")
        XCTAssertEqual(s.headline, .slight)
        XCTAssertEqual(s.headlineText, "Slight for 3 h 10 min")
        XCTAssertEqual(s.dyskinesia, "none")
    }

    func testNoneToday() {
        let s = TremorSummarizer.summarize(day: "d", intervals: [iv(0, 600, .none), iv(600, 30, .unknown)],
                                           dyskineticPercent: 0, source: "real")
        XCTAssertEqual(s.headline, .none)
        XCTAssertEqual(s.headlineText, "None today")
        XCTAssertEqual(s.minutes[.none], 600)
        XCTAssertEqual(s.minutes[.unknown], 30)
    }

    func testWholeHoursFormat() {
        let s = TremorSummarizer.summarize(day: "d", intervals: [iv(0, 120, .strong)], dyskineticPercent: nil, source: "real")
        XCTAssertEqual(s.headlineText, "Strong for 2 h")
    }

    func testDyskinesiaBuckets() {
        XCTAssertEqual(TremorSummarizer.dyskinesiaBucket(nil), "none")
        XCTAssertEqual(TremorSummarizer.dyskinesiaBucket(0.5), "none")
        XCTAssertEqual(TremorSummarizer.dyskinesiaBucket(5), "few")
        XCTAssertEqual(TremorSummarizer.dyskinesiaBucket(15), "some")
        XCTAssertEqual(TremorSummarizer.dyskinesiaBucket(40), "many")
    }

    func testSimulatedIsDeterministicAndBounded() {
        let a = SimulatedTremor.intervals(for: base, seed: 42)
        let b = SimulatedTremor.intervals(for: base, seed: 42)
        XCTAssertFalse(a.isEmpty)
        XCTAssertEqual(a.map(\.level), b.map(\.level))
        XCTAssertEqual(a.map(\.start), b.map(\.start))
        let c = SimulatedTremor.intervals(for: base, seed: 7)
        XCTAssertNotEqual(a.map(\.level), c.map(\.level))
        let s = TremorSummarizer.summarize(day: "d", intervals: a, dyskineticPercent: nil, source: "simulated")
        XCTAssertLessThanOrEqual(s.minutes.values.reduce(0, +), 1440)
        let cal = Calendar.current
        for i in a {
            let h = cal.component(.hour, from: i.start)
            XCTAssertTrue((7..<22).contains(h), "waking hours only, got \(h)")
            XCTAssertLessThan(i.start, i.end)
        }
    }
}
