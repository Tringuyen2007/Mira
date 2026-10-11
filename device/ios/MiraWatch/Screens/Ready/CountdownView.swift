import SwiftUI

/// Ready 2 Countdown (Figma 305:151): 3-2-1 from the `countdown` phase inside a filling ring, "Get ready…" and a
/// "● Ready" haptic chip. Each number's `countdown_tick` haptic comes from the server.
struct CountdownView: View {
    @Environment(WatchSessionModel.self) private var model
    let count: Int

    var body: some View {
        WatchCanvas(halo: .init(ringRadius: 70, center: CGPoint(x: 104, y: 104))) {
            CanvasStatusBar(title: model.exerciseTitle)
            ProgressRing(progress: Double(4 - min(max(count, 1), 3)) / 3, lineWidth: 8).figma(44, 44, 120, 120)
            Text("\(count)")
                .font(WatchTheme.inter(80, .light)).foregroundStyle(WatchTheme.ink)
                .monospacedDigit().lineLimit(1).minimumScaleFactor(0.5)
                .contentTransition(.numericText(countsDown: true))
                .animation(.easeOut(duration: 0.2), value: count)
                .figma(54, 62, 100, 80)
            Text("Get ready…").watchText(18, .medium).lineLimit(1).figma(16, 182, 176, 22)
            HapticChip(.tap, label: "Ready").figma(52, 206, 104, 31)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Starting in \(count). Get ready.")
    }
}
