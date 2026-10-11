import SwiftUI

/// Rotation 5 Set Summary (Figma 307:295), from `rotation_set`: BEST TURN + big best angle, per-rep bars (goal
/// reached = green bar with a dot on top) and "Average {avg}° · goal {goal}°". Below the Figma frame (scroll) a
/// "Next set" button sends `ready` (= start) to begin the next set; it starts with the same zero, so the flow
/// goes straight back to Live Turn. On the last set there is no button — the session ends to Ready.
struct RotationSummaryView: View {
    @Environment(WatchSessionModel.self) private var model

    var body: some View {
        let st = model.flow.state
        let summary = st.rotationSet
        let goal = summary?.goal ?? model.rotationGoal
        let moreSets = (st.set ?? 0) < (st.setsTarget ?? 0)
        WatchCanvas(height: moreSets ? 306 : 248, halo: .init(ringRadius: 49.5, center: CGPoint(x: 104, y: 80))) {
            CanvasStatusBar(title: model.rotationTitle)
            Text("Best turn").watchCaps().lineLimit(1).accessibilityHidden(true).figma(16, 38, 176, 19)
            Text("\(Int((summary?.best ?? 0).rounded()))°")
                .font(WatchTheme.inter(52, .light)).foregroundStyle(WatchTheme.ink)
                .monospacedDigit().lineLimit(1).minimumScaleFactor(0.7)
                .accessibilityLabel("Best turn \(Int((summary?.best ?? 0).rounded())) degrees")
                .figma(44, 58, 120, 56)
            PerRepBars(angles: summary?.angles ?? [], goal: goal).figma(27, 120, 154, 56)
            Text("Average \(Int((summary?.avg ?? 0).rounded()))° · goal \(Int(goal.rounded()))°")
                .watchText(16, color: WatchTheme.muted).lineLimit(1)
                .figma(12, 188, 184, 19)
            if moreSets {
                WatchPrimaryButton("Next set") { model.ready() }.figma(20, 246, 168, 50)
            }
            CaptionFootnote()
        }
    }
}
