import SwiftUI

/// Setup 5 Learn the Taps (Figma 304:197, scrolls at 208×300): the three haptic patterns. "Try them" plays
/// good_rep, fix_form and set_done one second apart (with the Taps strength); afterwards the button finishes setup.
struct LearnTapsView: View {
    @Environment(WatchSessionModel.self) private var model
    @State private var playing = false
    @State private var tried = false

    private struct Row {
        let kind: HapticChipKind
        let pattern: String
        let count: String
        let meaning: String
        let y: CGFloat
    }

    private let rows = [
        Row(kind: .tap, pattern: "good_rep", count: "1 tap", meaning: "Good rep", y: 76),
        Row(kind: .double, pattern: "fix_form", count: "2 buzzes", meaning: "Fix your form", y: 120),
        Row(kind: .long, pattern: "set_done", count: "Long buzz", meaning: "Set done", y: 164),
    ]

    var body: some View {
        WatchCanvas(height: 300, halo: .init(ringRadius: 40, center: CGPoint(x: 170, y: 30))) {
            CanvasStatusBar(title: "Mira")
            Text("Feel the taps")
                .font(WatchTheme.inter(20)).foregroundStyle(WatchTheme.ink).lineLimit(1)
                .figma(24, 40, 160, 24, alignment: .leading)
            ForEach(rows, id: \.pattern) { row in
                HapticPatternGlyph(kind: row.kind).figma(24, row.y + 13, 28, 12)
                VStack(alignment: .leading, spacing: 0) {
                    Text(row.count).font(WatchTheme.inter(16)).foregroundStyle(WatchTheme.muted)
                        .frame(height: 19)
                    Text(row.meaning).font(WatchTheme.inter(17, .semibold)).foregroundStyle(WatchTheme.ink)
                        .frame(height: 21)
                }
                .lineLimit(1)
                .accessibilityElement(children: .combine)
                .figma(62, row.y, 130, 40, alignment: .topLeading)
            }
            WatchPrimaryButton(tried ? "Done" : "Try them") {
                if tried { model.send(.tapsLearned) } else { tryThem() }
            }
            .disabled(playing)
            .figma(20, 220, 168, 50)
        }
    }

    private func tryThem() {
        guard !playing else { return }
        playing = true
        Task { @MainActor in
            for (i, row) in rows.enumerated() {
                if i > 0 { try? await Task.sleep(for: .seconds(1)) }
                model.haptics.play(row.pattern)
            }
            try? await Task.sleep(for: .seconds(1))
            playing = false
            tried = true
        }
    }
}
