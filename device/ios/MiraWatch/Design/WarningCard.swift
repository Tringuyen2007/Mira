import SwiftUI

/// "Warning card" (305:248): white 92 % rounded rect (radius 28) with a 2.5 pt #D92D20 border, padding 13.5;
/// top row = 26 pt warning triangle + optional haptic chip; then the cue in Inter SemiBold 19 / 24 alert-text.
/// The triangle and the words carry the message, never colour alone. `detail` (Regular 16, ink-soft) is extra
/// context under the title when a screen needs it inside the card.
struct WarningCard: View {
    let title: String
    let detail: String?
    let chip: HapticChipKind?

    init(title: String, detail: String? = nil, chip: HapticChipKind? = nil) {
        self.title = title
        self.detail = detail
        self.chip = chip
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                WarningTriangle().frame(width: 26, height: 26)
                if let chip { HapticChip(chip, label: chip.defaultLabel) }
            }
            Text(title)
                .font(WatchTheme.inter(19, .semibold)).foregroundStyle(WatchTheme.warn)
                .lineSpacing(1)
                .fixedSize(horizontal: false, vertical: true)
            if let detail {
                Text(detail).font(WatchTheme.inter(16)).foregroundStyle(WatchTheme.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(13.5)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(WatchTheme.cardFill, in: RoundedRectangle(cornerRadius: WatchTheme.cornerRadius))
        .overlay(RoundedRectangle(cornerRadius: WatchTheme.cornerRadius).strokeBorder(WatchTheme.danger, lineWidth: 2.5))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Warning. \(title)" + (detail.map { ". \($0)" } ?? ""))
    }
}

/// Red triangle with a white "!" (305:249, 26×26 viewBox). Also used inline at 18 pt (Breather limit row 305:339).
struct WarningTriangle: View {
    var body: some View {
        GeometryReader { geo in
            let s = geo.size.width / 26
            ZStack(alignment: .topLeading) {
                Path { p in
                    p.move(to: CGPoint(x: 13 * s, y: 3.033 * s))
                    p.addLine(to: CGPoint(x: 1.733 * s, y: 22.75 * s))
                    p.addLine(to: CGPoint(x: 24.267 * s, y: 22.75 * s))
                    p.closeSubpath()
                }
                .fill(WatchTheme.danger)
                Path(roundedRect: CGRect(x: 11.917 * s, y: 9.75 * s, width: 2.167 * s, height: 7.042 * s),
                     cornerRadius: 1.083 * s)
                    .fill(Color.white)
                Path(ellipseIn: CGRect(x: 11.7 * s, y: 18.2 * s, width: 2.6 * s, height: 2.6 * s))
                    .fill(Color.white)
            }
        }
        .aspectRatio(1, contentMode: .fit)
        .accessibilityHidden(true)
    }
}

#Preview("Warning card") {
    VStack(spacing: 8) {
        WarningCard(title: "Even out your weight", chip: .double)
        WarningCard(title: "Keep your elbow at your side", detail: "The cameras saw it lift", chip: .double)
    }
    .padding(.horizontal, WatchTheme.cardInset)
    .background(WatchTheme.background)
}
