import SwiftUI

/// Rotation 4 Elbow Drift (Figma 307:272): the cameras saw the elbow lift (`cue {id: elbow_drift}` +
/// haptic `fix_form`) → warning card "Keep your elbow at your side" with the "●● Fix form" chip, and
/// "The cameras saw it lift" below. Triangle + words, never colour alone. Back to Live Turn after 3 s.
struct ElbowDriftView: View {
    @Environment(WatchSessionModel.self) private var model

    var body: some View {
        WatchCanvas(halo: .init(ringRadius: 59.5, center: CGPoint(x: 104, y: 150))) {
            CanvasStatusBar(title: model.rotationTitle)
            WarningCard(title: "Keep your elbow at your side", chip: .double)
                .figma(12, 40, 184, 124, alignment: .top)
            Text("The cameras saw it lift").watchText(16, color: WatchTheme.muted).lineLimit(1)
                .figma(16, 186, 176, 19)
            CaptionFootnote()
        }
    }
}
