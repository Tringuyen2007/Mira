import SwiftUI

/// Setup 1 Paired (Figma 304:110): waving mascot with sparkles, "Hi, {first name}!", "Your watch is paired.", Next.
struct PairedView: View {
    @Environment(WatchSessionModel.self) private var model

    var body: some View {
        let name = model.flow.state.firstName
        WatchCanvas(halo: .init(ringRadius: 44, center: CGPoint(x: 104, y: 94))) {
            CanvasStatusBar(title: "Mira")
            Mascot(.wave, size: 79.2).figma(64, 42, 79.2, 88)
            Sparkle(size: 9.6).figma(150, 52, 9.6, 9.6)
            Sparkle(size: 7.2).figma(44, 112, 7.2, 7.2)
            Sparkle(size: 5.6).figma(160, 118, 5.6, 5.6)
            Text(name.isEmpty ? "Hi!" : "Hi, \(name)!")
                .watchText(26, .light, color: WatchTheme.berry).lineLimit(1)
                .figma(16, 128, 176, 31)
            Text("Your watch is paired.")
                .watchText(17, color: WatchTheme.muted).lineLimit(1)
                .figma(16, 160, 176, 21)
            WatchPrimaryButton("Next") { model.send(.next) }
                .figma(20, 186, 168, 50)
        }
    }
}
