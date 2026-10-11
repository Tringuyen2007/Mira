import SwiftUI

/// "Watch / Heart Rate Chip" (303:118): white 85 % pill (radius 16, padding 6 / 10 / 12, gap 5), 16 pt berry heart,
/// bpm in Inter SemiBold 17 ink, "bpm" in Inter Regular 16 ink-soft. `nil` shows "—".
struct HeartRateChip: View {
    let bpm: Int?

    init(bpm: Int?) { self.bpm = bpm }

    var body: some View {
        HStack(spacing: 5) {
            HeartShape().fill(WatchTheme.berry).frame(width: 16, height: 16)
            Text(bpm.map(String.init) ?? "—")
                .font(WatchTheme.inter(17, .semibold)).foregroundStyle(WatchTheme.ink)
                .monospacedDigit()
            Text("bpm").font(WatchTheme.inter(16)).foregroundStyle(WatchTheme.muted)
        }
        .lineLimit(1)
        .padding(.vertical, 6).padding(.leading, 10).padding(.trailing, 12)
        .frame(minHeight: WatchTheme.chipHeight)
        .background(WatchTheme.surface, in: RoundedRectangle(cornerRadius: WatchTheme.chipRadius))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(bpm.map { "Heart rate \($0) beats per minute" } ?? "Heart rate unavailable")
    }
}

/// Heart glyph traced from the Figma vector (303:119, 16×16 viewBox).
struct HeartShape: Shape {
    func path(in rect: CGRect) -> Path {
        let sx = rect.width / 16, sy = rect.height / 16
        func p(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: rect.minX + x * sx, y: rect.minY + y * sy) }
        var path = Path()
        path.move(to: p(8, 14))
        path.addCurve(to: p(1.6, 7.867), control1: p(8, 14), control2: p(3, 10.933))
        path.addCurve(to: p(4.533, 3), control1: p(0.6, 5.6), control2: p(2, 3))
        path.addCurve(to: p(8, 5), control1: p(6, 3), control2: p(6.933, 3.8))
        path.addCurve(to: p(11.467, 3), control1: p(9.067, 3.8), control2: p(10, 3))
        path.addCurve(to: p(14.4, 7.867), control1: p(14, 3), control2: p(15.4, 5.6))
        path.addCurve(to: p(8, 14), control1: p(13, 10.933), control2: p(8, 14))
        path.closeSubpath()
        return path
    }
}

#Preview("Heart rate chip") {
    VStack(spacing: 8) {
        HeartRateChip(bpm: 98)
        HeartRateChip(bpm: 101)
        HeartRateChip(bpm: nil)
    }
    .padding()
    .background(WatchTheme.background)
}
