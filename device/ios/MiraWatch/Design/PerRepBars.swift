import SwiftUI

/// "Turn per rep" (307:308, 154×56 for 10 reps): one rounded bar per rep (10 wide, 6 gap, radius 5), height
/// proportional to |angle| with the goal at 50 of 56 pt, marked by a dashed 1.5 pt #1E1E1E line (dash 3/3).
/// Reps that reach the goal are #0F6E56 with a 6 pt dot 6 pt above the bar top; the rest are #90BBCC.
/// The dot (shape) marks "reached", so colour is never the only cue.
struct PerRepBars: View {
    let angles: [Double]
    let goal: Double

    init(angles: [Double], goal: Double) {
        self.angles = angles
        self.goal = goal
    }

    private func reached(_ a: Double) -> Bool { abs(a) >= abs(goal) }

    var body: some View {
        GeometryReader { geo in
            let n = max(angles.count, 1)
            let w = geo.size.width, h = geo.size.height
            // Figma: n·bar + (n−1)·0.6·bar = width, bar ≤ 14 so short sets don't turn into blocks.
            let bar = min(w / (CGFloat(n) + CGFloat(n - 1) * 0.6), 14)
            let gap = n > 1 ? (w - CGFloat(n) * bar) / CGFloat(n - 1) : 0
            let perDegree = goal != 0 ? (h * 50 / 56) / abs(goal) : 0
            let goalY = h - abs(goal) * perDegree

            ZStack(alignment: .topLeading) {
                Path { p in
                    p.move(to: CGPoint(x: 0, y: goalY))
                    p.addLine(to: CGPoint(x: w, y: goalY))
                }
                .stroke(WatchTheme.charcoal, style: StrokeStyle(lineWidth: 1.5, dash: [3, 3]))

                ForEach(Array(angles.enumerated()), id: \.offset) { i, a in
                    let barH = min(max(abs(a) * perDegree, bar), h)
                    let x = CGFloat(i) * (bar + gap)
                    let hit = reached(a)
                    RoundedRectangle(cornerRadius: bar / 2)
                        .fill(hit ? WatchTheme.good : WatchTheme.haloStroke)
                        .frame(width: bar, height: barH)
                        .offset(x: x, y: h - barH)
                    if hit {
                        Circle().fill(WatchTheme.good)
                            .frame(width: bar * 0.6, height: bar * 0.6)
                            .offset(x: x + bar * 0.2, y: h - barH - bar * 0.6 - bar * 0.3)
                    }
                }
            }
        }
        .aspectRatio(154.0 / 56.0, contentMode: .fit)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Turn per rep")
        .accessibilityValue(angles.enumerated().map { i, a in
            "Rep \(i + 1): \(Int(abs(a).rounded())) degrees\(reached(a) ? ", reached goal" : "")"
        }.joined(separator: "; "))
    }
}

#Preview("Per-rep bars") {
    VStack(spacing: 8) {
        Text("BEST TURN").watchCaps()
        Text("82°").font(WatchTheme.inter(52, .light)).foregroundStyle(WatchTheme.ink)
        PerRepBars(angles: [64, 70, 72, 75, 78, 81, 80, 74, 78, 82], goal: 80)
            .padding(.horizontal, 27)
        Text("Average 75° · goal 80°").font(WatchTheme.inter(16)).foregroundStyle(WatchTheme.muted)
    }
    .background(WatchTheme.background)
}
