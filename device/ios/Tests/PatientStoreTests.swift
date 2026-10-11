import XCTest
@testable import MiraNetworking

@MainActor
final class PatientStoreTests: XCTestCase {
    private var utc: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        return calendar
    }

    /// 2026-10-11 is a Sunday.
    private func date(_ iso: String) -> Date { WireDates.parse(iso)! }

    private func session(startedAt: String, ended: Bool = true, exerciseId: String = "squat") -> SessionListItem {
        SessionListItem(id: UUID().uuidString, exerciseId: exerciseId, exerciseName: nil, startedAt: startedAt,
                        endedAt: ended ? startedAt : nil, repsDone: 10, painPost: nil, effort: nil, shared: false,
                        formScore: nil, goodReps: nil, repsTotal: nil, spokenPreview: nil)
    }

    // MARK: streakDays

    func testStreakDaysCountsConsecutiveDaysEndingToday() {
        let today = date("2026-10-11T08:00:00Z")
        let sessions = [
            session(startedAt: "2026-10-11T08:00:00Z"),
            session(startedAt: "2026-10-10T08:00:00Z"),
            session(startedAt: "2026-10-09T08:00:00Z"),
        ]
        XCTAssertEqual(PatientStore.streakDays(sessions, calendar: utc, today: today), 3)
    }

    func testStreakDaysCountsFromYesterdayWhenTodayHasNoSession() {
        let today = date("2026-10-11T08:00:00Z")
        let sessions = [session(startedAt: "2026-10-10T08:00:00Z"), session(startedAt: "2026-10-09T08:00:00Z")]
        XCTAssertEqual(PatientStore.streakDays(sessions, calendar: utc, today: today), 2)
    }

    func testStreakDaysStopsAtAGap() {
        let today = date("2026-10-11T08:00:00Z")
        let sessions = [session(startedAt: "2026-10-11T08:00:00Z"), session(startedAt: "2026-10-09T08:00:00Z")]
        XCTAssertEqual(PatientStore.streakDays(sessions, calendar: utc, today: today), 1)
    }

    func testStreakDaysIsZeroWhenNoRecentSession() {
        let today = date("2026-10-11T08:00:00Z")
        let sessions = [session(startedAt: "2026-10-01T08:00:00Z")]
        XCTAssertEqual(PatientStore.streakDays(sessions, calendar: utc, today: today), 0)
    }

    func testStreakDaysIgnoresSessionsThatNeverEnded() {
        let today = date("2026-10-11T08:00:00Z")
        let sessions = [session(startedAt: "2026-10-11T08:00:00Z", ended: false)]
        XCTAssertEqual(PatientStore.streakDays(sessions, calendar: utc, today: today), 0)
    }

    func testStreakDaysIsZeroWithNoSessions() {
        XCTAssertEqual(PatientStore.streakDays([], calendar: utc, today: date("2026-10-11T08:00:00Z")), 0)
    }

    // MARK: weekDots

    func testWeekDotsMondayThroughSunday() {
        // Week of 2026-10-05 (Mon) .. 2026-10-11 (Sun).
        let today = date("2026-10-11T08:00:00Z")
        let sessions = [session(startedAt: "2026-10-05T08:00:00Z"), session(startedAt: "2026-10-07T08:00:00Z"),
                        session(startedAt: "2026-10-11T08:00:00Z")]
        let dots = PatientStore.weekDots(sessions, calendar: utc, today: today)
        XCTAssertEqual(dots, [true, false, true, false, false, false, true])
    }

    func testWeekDotsExcludesSessionsFromOtherWeeks() {
        let today = date("2026-10-11T08:00:00Z")
        let sessions = [session(startedAt: "2026-10-04T08:00:00Z")]  // the previous Sunday
        let dots = PatientStore.weekDots(sessions, calendar: utc, today: today)
        XCTAssertEqual(dots, Array(repeating: false, count: 7))
    }

    func testWeekDotsWhenTodayIsMonday() {
        let today = date("2026-10-05T08:00:00Z")  // Monday
        let sessions = [session(startedAt: "2026-10-05T08:00:00Z")]
        let dots = PatientStore.weekDots(sessions, calendar: utc, today: today)
        XCTAssertEqual(dots, [true, false, false, false, false, false, false])
    }

    // MARK: doneDays

    func testDoneDaysWithinMonth() {
        let sessions = [session(startedAt: "2026-10-03T08:00:00Z"), session(startedAt: "2026-10-03T20:00:00Z"),
                        session(startedAt: "2026-10-15T08:00:00Z"), session(startedAt: "2026-09-30T08:00:00Z")]
        let days = PatientStore.doneDays(sessions, in: date("2026-10-01T00:00:00Z"), calendar: utc)
        XCTAssertEqual(days, [3, 15])
    }

    func testDoneDaysEmptyWhenNothingThatMonth() {
        let sessions = [session(startedAt: "2026-09-30T08:00:00Z")]
        let days = PatientStore.doneDays(sessions, in: date("2026-10-01T00:00:00Z"), calendar: utc)
        XCTAssertTrue(days.isEmpty)
    }

    // MARK: kneeTrend

    private func progressDay(_ day: String, maxKnee: Double? = nil, p95Knee: Double? = nil,
                             affectedLoadPct: Double? = nil, avgAsymmetry: Double? = nil) -> ProgressDay {
        ProgressDay(day: day, exerciseId: "squat", maxKnee: maxKnee, p95Knee: p95Knee, avgLoadLeft: nil,
                   twLoadLeft: nil, affectedLoadPct: affectedLoadPct, avgAsymmetry: avgAsymmetry,
                   avgTrunkLean: nil, avgConfidence: nil, frames: nil)
    }

    func testKneeTrendPrefersMaxKneeAndSortsByDate() {
        let progress = PatientProgress(patientId: "maya", exerciseId: "squat", days: 90, source: nil, series: [
            progressDay("2026-10-02T00:00:00Z", maxKnee: 80),
            progressDay("2026-10-01T00:00:00Z", maxKnee: 70, p95Knee: 65),
            progressDay("2026-10-03T00:00:00Z", p95Knee: 85),  // no maxKnee: falls back to p95Knee
        ])
        let trend = PatientStore.kneeTrend(progress)
        XCTAssertEqual(trend.map(\.1), [70, 80, 85])
        XCTAssertEqual(trend.first?.0, date("2026-10-01T00:00:00Z"))
    }

    func testKneeTrendSkipsDaysWithNeitherMetric() {
        let progress = PatientProgress(patientId: "maya", exerciseId: "squat", days: 90, source: nil,
                                       series: [progressDay("2026-10-01T00:00:00Z")])
        XCTAssertTrue(PatientStore.kneeTrend(progress).isEmpty)
    }

    // MARK: forecast

    func testForecastNilWithFewerThanThreePoints() {
        let points: [(Date, Double)] = [(date("2026-10-01T00:00:00Z"), 70), (date("2026-10-02T00:00:00Z"), 72)]
        XCTAssertNil(PatientStore.forecast(points, target: 90))
    }

    func testForecastNilWhenSlopeIsNotPositive() {
        let points: [(Date, Double)] = [
            (date("2026-10-01T00:00:00Z"), 80), (date("2026-10-02T00:00:00Z"), 80), (date("2026-10-03T00:00:00Z"), 80),
        ]
        XCTAssertNil(PatientStore.forecast(points, target: 90))
    }

    func testForecastProjectsTargetDateForAPositiveTrend() {
        // Exactly +2 degrees/day; reaches 90 at day 10 from the first point.
        let points: [(Date, Double)] = (0...4).map { offset in
            (Calendar(identifier: .gregorian).date(byAdding: .day, value: offset, to: date("2026-10-01T00:00:00Z"))!,
             70 + Double(offset) * 2)
        }
        let forecast = PatientStore.forecast(points, target: 90)
        XCTAssertNotNil(forecast)
        XCTAssertTrue(forecast!.slopePositive)
        XCTAssertEqual(forecast!.band, 0, accuracy: 0.0001)  // a perfect line has zero residual
        let expected = Calendar(identifier: .gregorian).date(byAdding: .day, value: 10, to: date("2026-10-01T00:00:00Z"))!
        XCTAssertEqual(forecast!.date.timeIntervalSince1970, expected.timeIntervalSince1970, accuracy: 1)
    }

    func testForecastUsesOnlyTheFourteenMostRecentPoints() {
        // 20 points, the first 6 wildly inconsistent with the trend; a forecast using
        // all of them would have a much larger residual than one using the last 14.
        var points: [(Date, Double)] = (0..<6).map { offset in
            (Calendar(identifier: .gregorian).date(byAdding: .day, value: offset, to: date("2026-09-20T00:00:00Z"))!,
             offset % 2 == 0 ? 10.0 : 150.0)
        }
        points += (0..<14).map { offset in
            (Calendar(identifier: .gregorian).date(byAdding: .day, value: offset, to: date("2026-10-01T00:00:00Z"))!,
             70 + Double(offset) * 2)
        }
        let forecast = PatientStore.forecast(points, target: 90)
        XCTAssertNotNil(forecast)
        XCTAssertEqual(forecast!.band, 0, accuracy: 0.0001)
    }

    // MARK: balanceSeries

    func testBalanceSeriesPrefersAffectedLoadPct() {
        let progress = PatientProgress(patientId: "maya", exerciseId: "squat", days: 90, source: nil, series: [
            progressDay("2026-10-01T00:00:00Z", affectedLoadPct: 42, avgAsymmetry: 5),
        ])
        let series = PatientStore.balanceSeries(progress, affectedSide: "right")
        XCTAssertEqual(series.map(\.1), [42])
    }

    func testBalanceSeriesFallsBackToFiftyMinusAsymmetry() {
        let progress = PatientProgress(patientId: "maya", exerciseId: "squat", days: 90, source: nil, series: [
            progressDay("2026-10-01T00:00:00Z", avgAsymmetry: 8),
        ])
        let series = PatientStore.balanceSeries(progress, affectedSide: "left")
        XCTAssertEqual(series.map(\.1), [42])
    }

    func testBalanceSeriesSkipsDaysWithNeitherMetric() {
        let progress = PatientProgress(patientId: "maya", exerciseId: "squat", days: 90, source: nil,
                                       series: [progressDay("2026-10-01T00:00:00Z")])
        XCTAssertTrue(PatientStore.balanceSeries(progress, affectedSide: "left").isEmpty)
    }

    // MARK: appendIncoming / send

    func testAppendIncomingSkipsDuplicateIDs() {
        let store = PatientStore()
        let message = CareMessage(id: 1, sender: "therapist", kind: "message", body: "hi", createdAt: "2026-10-01T00:00:00Z", readAt: nil)
        store.appendIncoming(MessageEvent(patientId: "maya", message: message))
        store.appendIncoming(MessageEvent(patientId: "maya", message: message))
        XCTAssertEqual(store.messages.count, 1)
    }

    // MARK: WireDates

    func testWireDatesParsesWithAndWithoutFractionalSeconds() {
        XCTAssertNotNil(WireDates.parse("2026-10-11T08:47:04.668537+00:00"))
        XCTAssertNotNil(WireDates.parse("2026-10-11T08:47:04Z"))
        XCTAssertNil(WireDates.parse("not a date"))
    }
}
