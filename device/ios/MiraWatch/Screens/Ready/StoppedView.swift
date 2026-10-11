import SwiftUI

/// Ready 4 Stopped (Figma 305:192): sleeping mascot, "Session stopped", "Rest well, {name}.", Resume
/// (`watch_button resume` = engine `command start`, which un-pauses the same set).
struct StoppedView: View {
    @Environment(WatchSessionModel.self) private var model

    var body: some View {
        let name = model.flow.state.firstName
        WatchCanvas(halo: .init(ringRadius: 42, center: CGPoint(x: 104, y: 80))) {
            CanvasStatusBar(title: "Mira")
            Mascot(.rest, size: 72).figma(68, 36, 72, 80)
            Text("Session stopped").watchText(20, .medium).lineLimit(1).figma(16, 120, 176, 24)
            Text(name.isEmpty ? "Rest well." : "Rest well, \(name).")
                .watchText(17, color: WatchTheme.muted).lineLimit(1)
                .figma(16, 148, 176, 21)
            WatchPrimaryButton("Resume") { model.resume() }.figma(20, 186, 168, 50)
        }
    }
}
