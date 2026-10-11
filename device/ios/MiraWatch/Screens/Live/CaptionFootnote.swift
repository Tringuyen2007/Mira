import SwiftUI

/// One-line caption footnote for the Live / Rotation screens: the latest relayed caption (`cue.text` /
/// `greeting.text` / `live.caption`), shown only when Settings → Captions is on and cleared by the model 6 s after
/// it arrives. Not in the Figma frames (they show the captions-off state); it sits on a soft pill along the
/// bottom edge so it stays legible over chips and never takes taps.
struct CaptionFootnote: View {
    @Environment(WatchSessionModel.self) private var model

    var body: some View {
        if let text = model.visibleCaption {
            Text(text)
                .font(WatchTheme.inter(12, .medium)).foregroundStyle(WatchTheme.ink)
                .lineLimit(1).truncationMode(.tail)
                .padding(.horizontal, 8)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(WatchTheme.surface, in: Capsule())
                .allowsHitTesting(false)
                .accessibilityLabel("Caption: \(text)")
                .transition(.opacity)
                .figma(28, 230, 152, 16)
        }
    }
}

extension WatchSessionModel {
    /// Live status-bar title: "Set X of Y" while the server reports sets, else the exercise name.
    var setTitle: String {
        let st = flow.state
        if let set = st.set, let total = st.setsTarget { return "Set \(set) of \(total)" }
        if let set = st.set { return "Set \(set)" }
        return exerciseTitle
    }
}
