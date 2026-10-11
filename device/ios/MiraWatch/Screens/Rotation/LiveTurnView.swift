import SwiftUI

/// Rotation 3 Live Turn (Figma 307:246): "REP n OF N", the semicircle gauge (green arc = current twist, dark tick
/// = goal) with Down / Up, the big live angle, "goal 80°", heart-rate chip. The angle is the watch's own zeroed
/// twist (10 Hz, also streamed to the phone as `wrist.deg`); reps come from the server's `rotation_rep`, else
/// the local `RotationRepMirror`. The server sends `good_rep` when a rep reaches the goal.
struct LiveTurnView: View {
    @Environment(WatchSessionModel.self) private var model

    var body: some View {
        let progress = model.rotationRepProgress
        let goal = model.rotationGoal
        let angle = model.liveAngle ?? model.flow.state.rotationRep?.angle ?? 0
        WatchCanvas(halo: .init(ringRadius: 59.5, center: CGPoint(x: 104, y: 152))) {
            CanvasStatusBar(title: model.rotationTitle)
            Text("Rep \(progress.current) of \(progress.target)").watchCaps().lineLimit(1)
                .accessibilityLabel("Rep \(progress.current) of \(progress.target)")
                .figma(16, 38, 176, 19)
            RotationGauge(angle: angle, goal: goal).figma(20, 66, 168, 115, alignment: .top)
            Text("\(Int(angle.rounded()))°")
                .font(WatchTheme.inter(52, .light)).foregroundStyle(WatchTheme.ink)
                .monospacedDigit().lineLimit(1).minimumScaleFactor(0.7)
                .accessibilityHidden(true)
                .figma(44, 88, 120, 56)
            Text("goal \(Int(goal.rounded()))°").watchText(16, color: WatchTheme.muted).lineLimit(1)
                .accessibilityHidden(true)
                .figma(16, 146, 176, 19)
            HeartRateChip(bpm: model.displayHeartRate).figma(16, 198, 176, 33)
            CaptionFootnote()
        }
    }
}
