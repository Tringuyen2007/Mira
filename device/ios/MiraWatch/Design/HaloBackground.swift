import SwiftUI

/// Screen background: the radial wash plus the "Halos & orbit" group drawn behind each screen's hero element.
///
/// Figma (304:116, 305:140, 305:157, 305:220, 305:304, 307:229): four concentric 1 pt rings — white 85 %,
/// white 55 %, #90BBCC 32 %, #90BBCC 24 % — around a soft white glow (70 %, blur 14, radius ≈ 0.8 × inner ring).
/// Across screens the rings sit at r, r+19, r+43, r+76 (±2) from the inner radius `r`, which hugs the hero
/// (big button r 64 → 71.5, count/ring → 56–70, mascot → 43.5). Sizes scale with screen width / 208.
struct HaloBackground: View {
    /// Inner ring radius on the 208-wide canvas.
    var ringRadius: CGFloat = 60
    /// Ring centre as a fraction of the screen (Figma heroes sit at y ≈ 0.38–0.45; the warning card at 0.6).
    var center: UnitPoint = UnitPoint(x: 0.5, y: 0.44)
    /// Draw the radial wash too (off when layering over another background).
    var showsWash = true

    init(ringRadius: CGFloat = 60, center: UnitPoint = UnitPoint(x: 0.5, y: 0.44), showsWash: Bool = true) {
        self.ringRadius = ringRadius
        self.center = center
        self.showsWash = showsWash
    }

    private static let rings: [(offset: CGFloat, color: Color)] = [
        (0, .white.opacity(0.85)),
        (19, .white.opacity(0.55)),
        (43, WatchTheme.haloStroke.opacity(0.32)),
        (76, WatchTheme.haloStroke.opacity(0.24)),
    ]

    var body: some View {
        GeometryReader { geo in
            let scale = geo.size.width / WatchTheme.designWidth
            let r = ringRadius * scale
            let c = CGPoint(x: geo.size.width * center.x, y: geo.size.height * center.y)
            ZStack {
                if showsWash { Rectangle().fill(WatchTheme.background) }
                Circle().fill(Color.white.opacity(0.7))
                    .frame(width: r * 1.6, height: r * 1.6)
                    .blur(radius: 14 * scale)
                    .position(c)
                ForEach(Self.rings.indices, id: \.self) { i in
                    let radius = r + Self.rings[i].offset * scale
                    Circle().stroke(Self.rings[i].color, lineWidth: 1)
                        .frame(width: radius * 2, height: radius * 2)
                        .position(c)
                }
            }
        }
        .ignoresSafeArea()
        .accessibilityHidden(true)
    }
}

#Preview("Halo") {
    ZStack {
        HaloBackground(ringRadius: 64, center: UnitPoint(x: 0.5, y: 0.435))
        Circle().fill(WatchTheme.charcoal).frame(width: 128, height: 128)
            .overlay(Text("I'm ready").font(WatchTheme.inter(22, .medium)).foregroundStyle(.white))
    }
}
