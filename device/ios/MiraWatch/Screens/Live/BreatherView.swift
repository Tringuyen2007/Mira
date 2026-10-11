import SwiftUI

/// Live 5 Breather (Figma 305:321): header "Paused", sleeping mascot, "Let's take a breather",
/// "⚠ {bpm} bpm · limit {limit}" (triangle + words, never colour alone), End session (secondary) = the same as
/// hold-to-stop (button `stop`). The server resumes on its own (`breather end`), which closes this screen.
struct BreatherView: View {
    @Environment(WatchSessionModel.self) private var model

    var body: some View {
        let st = model.flow.state
        let bpm = st.breather?.bpm ?? st.hr ?? 0
        let limit = st.breather?.limit ?? st.hrLimit ?? 0
        WatchCanvas(halo: .init(ringRadius: 37.5, center: CGPoint(x: 104, y: 72))) {
            CanvasStatusBar(title: "Paused")
            Mascot(.breathe, size: 64.8).figma(72, 34, 64.8, 72)
            // Explicit break: SwiftUI's orphan-avoiding line breaking would give "Let's take / a breather".
            Text("Let's take a\nbreather")
                .watchText(20, .medium).lineSpacing(0).lineLimit(2)
                .figma(16, 108, 176, 48)
            HStack(spacing: 6) {
                WarningTriangle().frame(width: 18, height: 18)
                Text("\(bpm) bpm").font(WatchTheme.inter(17, .semibold)).foregroundStyle(WatchTheme.warn)
                Text("· limit \(limit)").font(WatchTheme.inter(16)).foregroundStyle(WatchTheme.muted)
            }
            .lineLimit(1).minimumScaleFactor(0.8)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Warning. Heart rate \(bpm) beats per minute, your limit is \(limit)")
            .figma(16, 160, 176, 21)
            WatchSecondaryButton("End session") { model.endSession() }.figma(20, 186, 168, 50)
            CaptionFootnote()
        }
    }
}
