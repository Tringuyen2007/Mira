import SwiftUI

/// Live 2 Fix Form (Figma 305:238): red warning card — triangle + "●● Fix form" chip + the plain-words cue from
/// the form event (`formCue.text`) — then "Rep N" + heart-rate chip. Shape and words carry the warning, never
/// colour alone. The reducer drops back to Good Rep 3 s after the cue (haptic `fix_form` from the server).
struct FixFormView: View {
    @Environment(WatchSessionModel.self) private var model

    var body: some View {
        let cue = model.flow.state.formCue
        WatchCanvas(halo: .init(ringRadius: 59.5, center: CGPoint(x: 104, y: 150))) {
            CanvasStatusBar(title: model.setTitle)
            WarningCard(title: cue?.text ?? "Check your form", chip: .double)
                .figma(12, 40, 184, 124, alignment: .top)
            HStack(spacing: 2) {
                if let rep = cue?.rep {
                    Text("Rep \(rep)")
                        .font(WatchTheme.inter(18, .light)).foregroundStyle(WatchTheme.muted)
                        .lineLimit(1).fixedSize()
                }
                HeartRateChip(bpm: model.displayHeartRate)
            }
            .figma(16, 186, 176, 33)
            CaptionFootnote()
        }
    }
}
