import SwiftUI

/// Live 1 Good Rep (Figma 305:214): "Set X of Y", the one-tap "Good rep" chip, big good-rep count "/ target",
/// GOOD REPS, heart-rate chip (server HR, else this watch's own). Haptic `good_rep` comes from the server.
struct GoodRepView: View {
    @Environment(WatchSessionModel.self) private var model

    var body: some View {
        let st = model.flow.state
        WatchCanvas(halo: .init(ringRadius: 59.5, center: CGPoint(x: 104, y: 112))) {
            CanvasStatusBar(title: model.setTitle)
            HapticChip(.tap, label: "Good rep").figma(16, 40, 176, 31)
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text("\(st.goodReps)")
                    .font(WatchTheme.inter(92, .light)).foregroundStyle(WatchTheme.ink)
                if let target = st.repsTarget {
                    Text("/ \(target)")
                        .font(WatchTheme.inter(35, .light)).foregroundStyle(WatchTheme.muted)
                }
            }
            .monospacedDigit().lineLimit(1).minimumScaleFactor(0.6)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(st.repsTarget.map { "\(st.goodReps) of \($0) good reps" } ?? "\(st.goodReps) good reps")
            // Inter's natural line box at 92 pt is ~111 pt; a 112 pt frame keeps it at full size with the
            // baseline where Figma's 92 pt line box puts it.
            .figma(16, 64, 176, 112)
            Text("Good reps").watchCaps().lineLimit(1)
                .accessibilityHidden(true)
                .figma(16, 170, 176, 19)
            HeartRateChip(bpm: model.displayHeartRate).figma(16, 196, 176, 33)
            CaptionFootnote()
        }
    }
}
