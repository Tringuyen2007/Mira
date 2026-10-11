import SwiftUI

/// "Rotation gauge" (307:257, 168×96 on the 208 canvas) with the Down / Up labels (307:264, 307:265).
///
/// A semicircle (r 78, 12 pt stroke, white 95 % track, round caps). `range` maps left end → right end; the
/// Figma frame is drawn for −90…90 with 0 (thumb up, the zeroed start position) at 12 o'clock: the green
/// #0F6E56 arc runs from 0 to the current angle (68° ends 22° above the right horizontal) and the 3 pt #1E1E1E
/// goal tick crosses the track at the goal (80° → 10° above horizontal). Supination is positive (RotationMath),
/// so "Up" sits on the right. The big angle / "goal 80°" text is screen content laid over the gauge.
struct RotationGauge: View {
    let angle: Double
    let goal: Double
    let range: ClosedRange<Double>

    init(angle: Double, goal: Double, range: ClosedRange<Double> = -90...90) {
        self.angle = angle
        self.goal = goal
        self.range = range
    }

    /// 0 at the left end, 1 at the right end.
    private func fraction(_ value: Double) -> Double {
        let span = range.upperBound - range.lowerBound
        guard span > 0 else { return 0 }
        return (min(max(value, range.lowerBound), range.upperBound) - range.lowerBound) / span
    }

    var body: some View {
        VStack(spacing: 0) {
            GeometryReader { geo in
                let s = geo.size.width / 168
                let stroke = 12 * s
                let r = 78 * s
                let c = CGPoint(x: geo.size.width / 2, y: 84 * s)
                // Screen angle (SwiftUI: 0° = +x, clockwise positive). Left end = 180°, right end = 360°.
                let screenAngle = { (f: Double) in Angle.degrees(180 + 180 * f) }
                let zero = fraction(min(max(0, range.lowerBound), range.upperBound))
                let now = fraction(angle)
                let goalA = screenAngle(fraction(goal)).radians

                ZStack {
                    Path { p in p.addArc(center: c, radius: r, startAngle: .degrees(180), endAngle: .degrees(360), clockwise: false) }
                        .stroke(Color.white.opacity(0.95), style: StrokeStyle(lineWidth: stroke, lineCap: .round))
                    if abs(now - zero) > 0.001 {
                        Path { p in
                            p.addArc(center: c, radius: r, startAngle: screenAngle(min(zero, now)),
                                     endAngle: screenAngle(max(zero, now)), clockwise: false)
                        }
                        .stroke(WatchTheme.good, style: StrokeStyle(lineWidth: stroke, lineCap: .round))
                    }
                    Path { p in
                        p.move(to: CGPoint(x: c.x + (r - stroke) * cos(goalA), y: c.y + (r - stroke) * sin(goalA)))
                        p.addLine(to: CGPoint(x: c.x + (r + stroke) * cos(goalA), y: c.y + (r + stroke) * sin(goalA)))
                    }
                    .stroke(WatchTheme.charcoal, style: StrokeStyle(lineWidth: 3 * s, lineCap: .round))
                }
                .animation(.easeOut(duration: 0.1), value: angle)
            }
            .aspectRatio(168.0 / 96.0, contentMode: .fit)
            HStack {
                Text("Down")
                Spacer()
                Text("Up")
            }
            .font(WatchTheme.inter(16)).foregroundStyle(WatchTheme.muted)
            // Figma: "Down" at x 4 and "Up" ending at x 193 on the 208 canvas (gauge spans 20…188).
            .padding(.leading, -16).padding(.trailing, -5)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Forearm turn")
        .accessibilityValue("\(Int(angle.rounded())) degrees, goal \(Int(goal.rounded())) degrees")
    }
}

#Preview("Rotation gauge") {
    ZStack(alignment: .top) {
        RotationGauge(angle: 68, goal: 80)
        VStack(spacing: 0) {
            Text("68°").font(WatchTheme.inter(52, .light)).foregroundStyle(WatchTheme.ink).padding(.top, 22)
            Text("goal 80°").font(WatchTheme.inter(16)).foregroundStyle(WatchTheme.muted)
        }
    }
    .padding(.horizontal, 20)
    .background(WatchTheme.background)
}
