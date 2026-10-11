import Foundation
import Observation

/// The patient-facing data this app needs beyond the live session: profile, care
/// (therapist notes/messages/highlights), session history, progress series, and
/// milestones. Deliberately Foundation + Observation only (no SwiftUI/UIKit) so
/// `swift test` exercises it directly, same as the rest of `Net/`.
@MainActor
@Observable
final class PatientStore {
    private(set) var me: MeResponse?
    private(set) var care: CareResponse?
    private(set) var sessions: [SessionListItem] = []
    /// Keyed by exercise id.
    private(set) var progress: [String: PatientProgress] = [:]
    private(set) var milestones: [Milestone] = []
    private(set) var messages: [CareMessage] = []
    private(set) var lastError: String?
    private(set) var isRefreshing = false

    /// Clears every cached record, e.g. after `DELETE /me` wipes this phone's copy
    /// of the patient's data.
    func reset() {
        me = nil
        care = nil
        sessions = []
        progress = [:]
        milestones = []
        messages = []
        lastError = nil
    }

    /// Fetches everything the Care/Progress screens need, in parallel. Each piece's
    /// failure is recorded independently; a failure in one does not discard the
    /// others' successful results (so a flaky `/me/care` doesn't blank the sessions
    /// list, for example).
    func refreshAll(api: APIClient, patientID: String) async {
        isRefreshing = true
        defer { isRefreshing = false }

        async let meResult: MeResponse? = try? await api.me()
        async let careResult: CareResponse? = try? await api.care()
        async let sessionsResult: SessionList? = try? await api.sessions(patientID: patientID)
        async let milestonesResult: MilestoneList? = try? await api.milestones(patientID: patientID)
        async let messagesResult: MessageList? = try? await api.messages()

        let (meValue, careValue, sessionsValue, milestonesValue, messagesValue) =
            await (meResult, careResult, sessionsResult, milestonesResult, messagesResult)

        var failed: [String] = []
        if let meValue { me = meValue } else { failed.append("profile") }
        if let careValue { care = careValue } else { failed.append("care") }
        if let sessionsValue { sessions = sessionsValue.sessions } else { failed.append("sessions") }
        if let milestonesValue { milestones = milestonesValue.milestones } else { failed.append("milestones") }
        if let messagesValue { messages = messagesValue.messages } else { failed.append("messages") }

        let exerciseIDs = Set((meValue?.prescriptions.map(\.exerciseId) ?? []) + sessions.map(\.exerciseId))
        if !exerciseIDs.isEmpty {
            await refreshProgress(api: api, patientID: patientID, exerciseIDs: exerciseIDs)
        }

        lastError = failed.isEmpty ? nil : "Couldn't refresh: \(failed.joined(separator: ", "))."
    }

    private func refreshProgress(api: APIClient, patientID: String, exerciseIDs: Set<String>) async {
        await withTaskGroup(of: (String, PatientProgress?).self) { group in
            for exerciseID in exerciseIDs {
                group.addTask { (exerciseID, try? await api.progress(patientID: patientID, exerciseID: exerciseID)) }
            }
            for await (exerciseID, result) in group {
                if let result { progress[exerciseID] = result }
            }
        }
    }

    /// Pulls any messages newer than the last one already held (therapist/mira
    /// messages read as a side effect, matching `GET /me/messages`'s contract).
    func refreshMessages(api: APIClient) async {
        do {
            let result = try await api.messages(sinceID: messages.last?.id)
            if !result.messages.isEmpty { messages.append(contentsOf: result.messages) }
        } catch {
            lastError = error.localizedDescription
        }
    }

    /// Sends the patient's own message and appends the server's copy (with its
    /// assigned id) to `messages`.
    func send(_ body: String, api: APIClient) async throws {
        let sent = try await api.sendMessage(body)
        messages.append(sent.message)
    }

    /// A `message` event arriving on /ws/events (therapist/mira sent, or the
    /// patient's own echoed back). Ignored if already present (REST already
    /// appended it, or the event arrived twice).
    func appendIncoming(_ event: MessageEvent) {
        guard !messages.contains(where: { $0.id == event.message.id }) else { return }
        messages.append(event.message)
    }

    // MARK: - Pure helpers (unit-tested directly; no instance state)

    /// Consecutive days, ending today or yesterday, with at least one ended session.
    /// Yesterday still counts as "ending today" so the streak doesn't reset at
    /// midnight before the patient has had a chance to do today's session.
    static func streakDays(_ sessions: [SessionListItem], calendar: Calendar, today: Date) -> Int {
        let doneDates = endedDays(sessions, calendar: calendar)
        guard !doneDates.isEmpty else { return 0 }
        let todayStart = calendar.startOfDay(for: today)
        var cursor: Date
        if doneDates.contains(todayStart) {
            cursor = todayStart
        } else if let yesterday = calendar.date(byAdding: .day, value: -1, to: todayStart), doneDates.contains(yesterday) {
            cursor = yesterday
        } else {
            return 0
        }
        var count = 0
        while doneDates.contains(cursor) {
            count += 1
            guard let previous = calendar.date(byAdding: .day, value: -1, to: cursor) else { break }
            cursor = previous
        }
        return count
    }

    /// Mon..Sun of the week containing `today`; `true` where at least one session ended.
    static func weekDots(_ sessions: [SessionListItem], calendar: Calendar, today: Date) -> [Bool] {
        let doneDates = endedDays(sessions, calendar: calendar)
        let monday = startOfWeek(containing: today, calendar: calendar)
        return (0..<7).map { offset in
            guard let day = calendar.date(byAdding: .day, value: offset, to: monday) else { return false }
            return doneDates.contains(calendar.startOfDay(for: day))
        }
    }

    /// Day-of-month numbers, within `month`'s calendar month, with at least one ended session.
    static func doneDays(_ sessions: [SessionListItem], in month: Date, calendar: Calendar) -> Set<Int> {
        let target = calendar.dateComponents([.year, .month], from: month)
        var result: Set<Int> = []
        for date in endedDays(sessions, calendar: calendar) {
            let parts = calendar.dateComponents([.year, .month, .day], from: date)
            if parts.year == target.year, parts.month == target.month, let day = parts.day {
                result.insert(day)
            }
        }
        return result
    }

    /// The day's deepest knee bend (degrees), oldest first; days missing both metrics are skipped.
    static func kneeTrend(_ progress: PatientProgress) -> [(Date, Double)] {
        progress.series.compactMap { day -> (Date, Double)? in
            guard let date = WireDates.parse(day.day) else { return nil }
            guard let value = day.maxKnee ?? day.p95Knee else { return nil }
            return (date, value)
        }.sorted { $0.0 < $1.0 }
    }

    struct Forecast: Sendable, Equatable {
        let date: Date
        let slopePositive: Bool
        let band: Double  // RMSE of the fit, same units as the series
    }

    /// Least-squares projection of when `target` is reached, over the ≤14 most
    /// recent points. `nil` when there isn't enough data (< 3 points) or the trend
    /// isn't moving toward `target` (slope <= 0).
    static func forecast(_ points: [(Date, Double)], target: Double) -> Forecast? {
        let recent = points.sorted { $0.0 < $1.0 }.suffix(14)
        guard recent.count >= 3 else { return nil }
        let origin = recent.first!.0
        let xs = recent.map { $0.0.timeIntervalSince(origin) / 86400 }  // days since the earliest point
        let ys = recent.map(\.1)
        let n = Double(xs.count)
        let sumX = xs.reduce(0, +), sumY = ys.reduce(0, +)
        let sumXY = zip(xs, ys).reduce(0) { $0 + $1.0 * $1.1 }
        let sumXX = xs.reduce(0) { $0 + $1 * $1 }
        let denominator = n * sumXX - sumX * sumX
        guard denominator != 0 else { return nil }
        let slope = (n * sumXY - sumX * sumY) / denominator
        guard slope > 0 else { return nil }
        let intercept = (sumY - slope * sumX) / n
        let residuals = zip(xs, ys).map { x, y -> Double in
            let predicted = slope * x + intercept
            return (y - predicted) * (y - predicted)
        }
        let rmse = (residuals.reduce(0, +) / n).squareRoot()
        let targetX = (target - intercept) / slope
        let targetDate = origin.addingTimeInterval(targetX * 86400)
        return Forecast(date: targetDate, slopePositive: true, band: rmse)
    }

    /// Percent of load on the affected leg, oldest first: `affected_load_pct` when the
    /// server computed it, else `50 - avg_asymmetry` (asymmetry is signed toward the
    /// unaffected side in the server's convention). `affectedSide` is accepted for the
    /// caller's context even though both formulas are already side-aware upstream.
    static func balanceSeries(_ progress: PatientProgress, affectedSide: String?) -> [(Date, Double)] {
        progress.series.compactMap { day -> (Date, Double)? in
            guard let date = WireDates.parse(day.day) else { return nil }
            if let affected = day.affectedLoadPct { return (date, affected) }
            if let asymmetry = day.avgAsymmetry { return (date, 50 - asymmetry) }
            return nil
        }.sorted { $0.0 < $1.0 }
    }

    // MARK: - Private date helpers

    private static func endedDays(_ sessions: [SessionListItem], calendar: Calendar) -> Set<Date> {
        Set(sessions.compactMap { session -> Date? in
            guard session.endedAt != nil, let started = WireDates.parse(session.startedAt) else { return nil }
            return calendar.startOfDay(for: started)
        })
    }

    /// Monday of the week containing `date`, independent of the calendar's own
    /// `firstWeekday` (the week dots are always presented Mon..Sun).
    private static func startOfWeek(containing date: Date, calendar: Calendar) -> Date {
        var mondayFirst = calendar
        mondayFirst.firstWeekday = 2
        let components = mondayFirst.dateComponents([.yearForWeekOfYear, .weekOfYear], from: date)
        return mondayFirst.date(from: components) ?? mondayFirst.startOfDay(for: date)
    }
}
