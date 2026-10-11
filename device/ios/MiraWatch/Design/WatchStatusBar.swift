import SwiftUI

/// "Watch / Status Bar" (303:111): 36 pt row, title Inter SemiBold 16 in accent at x 34, time Inter Medium 16
/// in ink-soft at the right, both on y 12. The time ticks every minute.
struct WatchStatusBar: View {
    let title: String

    init(title: String) { self.title = title }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(title)
                .font(WatchTheme.inter(16, .semibold))
                .foregroundStyle(WatchTheme.accent)
                .lineLimit(1).minimumScaleFactor(0.75)
                .accessibilityAddTraits(.isHeader)
            Spacer(minLength: 4)
            TimelineView(.everyMinute) { context in
                Text(context.date, format: .dateTime.hour(.defaultDigits(amPM: .omitted)).minute())
                    .font(WatchTheme.inter(16, .medium))
                    .foregroundStyle(WatchTheme.muted)
                    .monospacedDigit()
            }
        }
        .padding(.top, 12)
        .padding(.horizontal, WatchTheme.statusBarInset)
        .frame(height: WatchTheme.statusBarHeight, alignment: .top)
        .frame(maxWidth: .infinity)
    }
}

#Preview("Status bar") {
    VStack(spacing: 0) {
        WatchStatusBar(title: "Mira")
        WatchStatusBar(title: "Set 2 of 3")
        WatchStatusBar(title: "Mini squats")
    }
    .background(WatchTheme.background)
}
