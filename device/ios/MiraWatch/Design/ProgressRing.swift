import SwiftUI

/// Countdown / hold-to-stop / sync / start-position ring (305:161 + 305:163, 307:233 + 307:235): white 90 % track,
/// #0F6E56 progress with round caps, starting at 12 o'clock and filling clockwise. Figma uses 8 pt on the 120 pt
/// ring and 7 pt on the 99 pt ring. The ring fills whatever frame it gets; put content on top with a ZStack.
struct ProgressRing: View {
    let progress: Double
    let lineWidth: CGFloat
    /// Progress colour: `good` everywhere except Holding to Stop, which fills in charcoal (305:185).
    let tint: Color

    init(progress: Double, lineWidth: CGFloat = 8, tint: Color = WatchTheme.good) {
        self.progress = progress
        self.lineWidth = lineWidth
        self.tint = tint
    }

    private var clamped: Double { min(max(progress, 0), 1) }

    var body: some View {
        ZStack {
            Circle().stroke(WatchTheme.track, lineWidth: lineWidth)
            Circle().trim(from: 0, to: clamped)
                .stroke(tint, style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
                .rotationEffect(.degrees(-90))
                .opacity(clamped > 0 ? 1 : 0)
        }
        .padding(lineWidth / 2)
        .aspectRatio(1, contentMode: .fit)
        .animation(.linear(duration: 0.2), value: clamped)
        .accessibilityElement()
        .accessibilityValue("\(Int((clamped * 100).rounded())) percent")
    }
}

#Preview("Progress ring") {
    HStack(spacing: 12) {
        ZStack {
            ProgressRing(progress: 0.8)
            Text("3").font(WatchTheme.inter(80, .light)).foregroundStyle(WatchTheme.ink)
        }
        .frame(width: 120, height: 120)
        ProgressRing(progress: 0.85, lineWidth: 7).frame(width: 99, height: 99)
    }
    .padding()
    .background(WatchTheme.background)
}
