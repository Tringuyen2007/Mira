import SwiftUI

/// Live 3 Set Done (Figma 305:266): long-buzz "Set done" chip, celebrating mascot with three sparkles,
/// "Set done!", "{n} good reps". Shown for 3 s after haptic `set_done`; the reducer then moves on to Recovery.
struct SetDoneView: View {
    @Environment(WatchSessionModel.self) private var model

    var body: some View {
        let reps = model.flow.state.goodReps
        WatchCanvas(halo: .init(ringRadius: 41.5, center: CGPoint(x: 104, y: 96))) {
            CanvasStatusBar(title: model.setTitle)
            HapticChip(.long, label: "Set done").figma(16, 38, 176, 31)
            Mascot(.cheer, size: 72).figma(68, 72, 72, 80)
            Sparkle(size: 9.6).figma(150, 80, 9.6, 9.6)
            Sparkle(size: 7.2).figma(46, 130, 7.2, 7.2)
            Sparkle(size: 5.6).figma(158, 140, 5.6, 5.6)
            Text("Set done!").watchText(26, .light, color: WatchTheme.berry).lineLimit(1)
                .figma(16, 154, 176, 31)
            Text(reps == 1 ? "1 good rep" : "\(reps) good reps")
                .watchText(17, color: WatchTheme.muted).lineLimit(1)
                .figma(16, 188, 176, 21)
            CaptionFootnote()
        }
    }
}
