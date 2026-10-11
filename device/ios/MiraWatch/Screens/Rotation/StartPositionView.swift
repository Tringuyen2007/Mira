import SwiftUI

/// Rotation 2 Start Position (Figma 307:223): pose glyph inside the stillness ring, "Elbow at your side, thumb up",
/// "Hold still…". MotionService (`.rotation(nil)`) fills the ring while the wrist is still for 1.5 s; when it is
/// full the model zeroes the heading here (`motion.zeroHere()`), sends `.startPositionHeld` and `ready()` (click).
struct StartPositionView: View {
    @Environment(WatchSessionModel.self) private var model

    var body: some View {
        WatchCanvas(halo: .init(ringRadius: 59.5, center: CGPoint(x: 104, y: 92))) {
            CanvasStatusBar(title: model.rotationTitle)
            ProgressRing(progress: model.stillness, lineWidth: 7)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Holding still")
                .accessibilityValue("\(Int((model.stillness * 100).rounded())) percent")
                .figma(54.5, 42.5, 99, 99)
            PoseElbowSide().figma(74, 64, 60, 56)
            Text("Elbow at your side, thumb up")
                .watchText(18, .medium).lineSpacing(0).lineLimit(2)
                .figma(16, 150, 176, 46)
            Text("Hold still…").watchText(16, color: WatchTheme.muted).lineLimit(1).figma(16, 202, 176, 19)
            CaptionFootnote()
        }
        .onChange(of: model.stillness) { _, value in
            if value >= 1 { model.startPositionHeld() }
        }
    }
}
