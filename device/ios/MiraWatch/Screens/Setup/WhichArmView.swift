import SwiftUI

/// Setup 3 Which Arm (Figma 304:156): "Wear me on the arm you exercise", Left / Right. The current arm
/// (`prefs.arm`, default right) is the filled button with a check; tapping either chooses it and moves on.
struct WhichArmView: View {
    @Environment(WatchSessionModel.self) private var model

    var body: some View {
        let selected = model.flow.state.prefs.arm == "left" ? "left" : "right"
        WatchCanvas(halo: .init(ringRadius: 40, center: CGPoint(x: 104, y: 40))) {
            CanvasStatusBar(title: "Mira")
            Text("Wear me on the arm you exercise")
                .watchText(18).lineLimit(2)
                .figma(16, 42, 176, 46)
            armButton("left", title: "Left arm", selected: selected == "left").figma(20, 104, 168, 50)
            armButton("right", title: "Right arm", selected: selected == "right").figma(20, 164, 168, 50)
        }
    }

    @ViewBuilder
    private func armButton(_ arm: String, title: String, selected: Bool) -> some View {
        if selected {
            WatchPrimaryButton(title) { model.send(.armChosen(arm)) }
                .overlay(alignment: .leading) { SelectedCheck().padding(.leading, 14).allowsHitTesting(false) }
                .accessibilityAddTraits(.isSelected)
        } else {
            WatchSecondaryButton(title) { model.send(.armChosen(arm)) }
        }
    }
}

/// "Selected check" (304:171): 22 pt #0F6E56 disc with a white tick.
struct SelectedCheck: View {
    var body: some View {
        ZStack {
            Circle().fill(WatchTheme.good)
            Image(systemName: "checkmark").font(.system(size: 11, weight: .bold)).foregroundStyle(.white)
        }
        .frame(width: 22, height: 22)
        .accessibilityHidden(true)
    }
}
