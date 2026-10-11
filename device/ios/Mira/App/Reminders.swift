import Foundation
#if canImport(UserNotifications)
import UserNotifications
#endif

/// Schedules this phone's local "time for your Mira session" notifications from
/// `MiraPreferences`. Lives outside the `MiraNetworking` Swift package (it imports
/// UserNotifications, which the macOS `swift test` target has no need of and no
/// simulator to back); `AppState.onPreferencesChanged` is how the rest of the app,
/// which does build against this file, calls in.
enum Reminders {
    static let identifierPrefix = "mira.reminder."

    /// Clears every pending `mira.reminder.*` request, then — when the daily
    /// reminder and the phone notification are both on — requests notification
    /// authorization and schedules one repeating `UNCalendarNotificationTrigger`
    /// per selected weekday at the chosen time.
    static func sync(_ prefs: MiraPreferences) async {
        #if canImport(UserNotifications) && os(iOS)
        let center = UNUserNotificationCenter.current()
        let pending = await center.pendingNotificationRequests()
        let ours = pending.map(\.identifier).filter { $0.hasPrefix(identifierPrefix) }
        if !ours.isEmpty { center.removePendingNotificationRequests(withIdentifiers: ours) }

        guard prefs.dailyReminder, prefs.phoneReminder else { return }

        do {
            let granted = try await center.requestAuthorization(options: [.alert, .sound])
            guard granted else { return }
        } catch {
            return
        }

        let content = UNMutableNotificationContent()
        content.title = "Time for your Mira session"
        content.body = "Your plan is ready. Step on the mat and give Mira a thumbs up."
        content.sound = .default

        for weekday in prefs.reminderDays {
            var components = DateComponents()
            components.weekday = weekday
            components.hour = prefs.reminderHour
            components.minute = prefs.reminderMinute
            let trigger = UNCalendarNotificationTrigger(dateMatching: components, repeats: true)
            let request = UNNotificationRequest(identifier: "\(identifierPrefix)\(weekday)", content: content, trigger: trigger)
            try? await center.add(request)
        }
        #endif
    }
}
