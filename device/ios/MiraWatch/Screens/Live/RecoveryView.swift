import SwiftUI

/// Live 4 Recovery (Figma 305:298): header "Rest", HEART RATE + big bpm, the recovery line (peak → bpm at 60 s)
/// and "Down {drop} in 1 min" once `hr_recovery` arrives (hidden until then), "Next set in m:ss" from the
/// snapshot's `restRemaining` — or a local countdown from 60 s after the set ended when the phone sends none.
struct RecoveryView: View {
    @Environment(WatchSessionModel.self) private var model
    /// When this screen appeared; the local rest countdown runs from here.
    @State private var appeared = Date()
    static let defaultRest: TimeInterval = 60

    var body: some View {
        let st = model.flow.state
        let bpm = model.displayHeartRate
        WatchCanvas(halo: .init(ringRadius: 55.5, center: CGPoint(x: 104, y: 90))) {
            CanvasStatusBar(title: "Rest")
            Text("Heart rate").watchCaps().lineLimit(1).accessibilityHidden(true).figma(16, 40, 176, 19)
            HStack(spacing: 6) {
                HeartShape().fill(WatchTheme.berry).frame(width: 26, height: 26)
                Text(bpm.map(String.init) ?? "—")
                    .font(WatchTheme.inter(60, .light)).foregroundStyle(WatchTheme.ink)
                    .monospacedDigit().lineLimit(1).minimumScaleFactor(0.6)
                Text("bpm").font(WatchTheme.inter(17)).foregroundStyle(WatchTheme.muted).lineLimit(1).fixedSize()
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(bpm.map { "Heart rate \($0) beats per minute" } ?? "Heart rate unavailable")
            .figma(16, 62, 176, 64)
            if let rec = st.recovery {
                RecoveryLine(drop: rec.drop)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel("Heart rate went from \(rec.peak) to \(rec.bpm60) in one minute")
                    .figma(20, 134, 168, 40)
                Text(rec.drop >= 0 ? "Down \(rec.drop) in 1 min" : "Up \(-rec.drop) in 1 min")
                    .font(WatchTheme.inter(16, .medium)).foregroundStyle(WatchTheme.accent)
                    .multilineTextAlignment(.center).lineLimit(1).minimumScaleFactor(0.8)
                    .figma(14, 178, 180, 20)
            }
            TimelineView(.periodic(from: appeared, by: 1)) { ctx in
                Text("Next set in \(Self.clock(remaining(at: ctx.date)))")
                    .watchText(16, color: WatchTheme.muted).lineLimit(1)
            }
            .figma(16, 202, 176, 19)
            CaptionFootnote()
        }
    }

    private func remaining(at date: Date) -> Int {
        if let r = model.flow.state.restRemaining { return max(r, 0) }
        return max(Int((Self.defaultRest - date.timeIntervalSince(appeared)).rounded(.up)), 0)
    }

    static func clock(_ seconds: Int) -> String {
        String(format: "%d:%02d", seconds / 60, seconds % 60)
    }
}

/// The recovery line (305:314, 168×40): berry 3 pt curve from the peak (faded dot, top left) down to the bpm
/// one minute later (solid dot). The fall is drawn in proportion to the drop — 22 bpm (the Figma sample) uses
/// the full 28 pt; a rise is drawn flat.
struct RecoveryLine: View {
    let drop: Int

    var body: some View {
        GeometryReader { geo in
            let sx = geo.size.width / 168, sy = geo.size.height / 40
            let fall = 28 * min(max(Double(drop), 0) / 22, 1)
            let endY = 6 + fall
            let p = { (x: Double, y: Double) in CGPoint(x: x * sx, y: y * sy) }
            // Figma: M2 6 C30 6 40 8 60 16 C80 24 110 32 166 34 — y scaled by the fall.
            let y = { (v: Double) in 6 + (v - 6) * fall / 28 }
            ZStack(alignment: .topLeading) {
                Path { path in
                    path.move(to: p(2, 6))
                    path.addCurve(to: p(60, y(16)), control1: p(30, 6), control2: p(40, y(8)))
                    path.addCurve(to: p(166, endY), control1: p(80, y(24)), control2: p(110, y(32)))
                }
                .stroke(WatchTheme.berry, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                Circle().fill(WatchTheme.berry.opacity(0.45)).frame(width: 6, height: 6).position(p(2, 6))
                Circle().fill(WatchTheme.berry).frame(width: 8, height: 8).position(p(166, endY))
            }
        }
    }
}
