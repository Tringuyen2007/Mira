import SwiftUI

/// "LCD Hi-Fi / Mira" (38:298) as used on the watch (304:120, 305:202, 305:280, 305:331): a soft radial halo
/// (#FFFCF7 95 % → #FFF7F2 55 % → clear), a thin white inner ring, and the mascot pose on top. The frame is
/// size × size·10/9 (72×80 on the sample screens). Poses come from the Mood variants:
/// wave → Idle (29:128, `WatchMascot`), cheer → Celebrating (78:383, `MascotCheer`, Set Done),
/// rest / breathe → Sleeping (75:382, `MascotRest`, Session Stopped / Breather).
struct Mascot: View {
    enum Pose: Equatable {
        case wave, cheer, rest, breathe

        var assetName: String {
            switch self {
            case .wave: return "WatchMascot"
            case .cheer: return "MascotCheer"
            case .rest, .breathe: return "MascotRest"
            }
        }
    }

    let pose: Pose
    let size: CGFloat

    init(_ pose: Pose, size: CGFloat) {
        self.pose = pose
        self.size = size
    }

    var body: some View {
        ZStack(alignment: .top) {
            Circle()
                .fill(RadialGradient(stops: [
                    .init(color: Color(hex: 0xFFFCF7, opacity: 0.95), location: 0),
                    .init(color: Color(hex: 0xFFF7F2, opacity: 0.55), location: 0.5),
                    .init(color: Color(hex: 0xFFF7F2, opacity: 0), location: 1),
                ], center: .center, startRadius: 0, endRadius: size * 0.528))
                .frame(width: size * 1.056, height: size * 1.056)
            Circle().stroke(Color.white.opacity(0.85), lineWidth: max(0.33, size / 218))
                .frame(width: size * 0.917, height: size * 0.917)
                .padding(.top, size * 0.069)
            Image(pose.assetName).resizable().scaledToFit()
                .frame(width: size * 0.883, height: size * 0.997)
                .padding(.top, size * 0.06)
        }
        .frame(width: size, height: size * 10 / 9, alignment: .top)
        .accessibilityHidden(true)
    }
}

/// Four-point sparkle (24:135): white star with a #90BBCC glow, "marks a quiet moment of progress; use sparingly".
/// Sample sizes 9.6, 7.2, 5.6 (304:124, 304:127, 304:130).
struct Sparkle: View {
    var size: CGFloat = 9.6

    var body: some View {
        SparkleShape().fill(Color.white)
            .frame(width: size, height: size)
            .shadow(color: WatchTheme.haloStroke.opacity(0.7), radius: size / 4)
            .accessibilityHidden(true)
    }
}

struct SparkleShape: Shape {
    func path(in rect: CGRect) -> Path {
        let s = rect.width / 9.6
        func p(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: rect.minX + x * s, y: rect.minY + y * s) }
        var path = Path()
        path.move(to: p(4.8, 0))
        path.addCurve(to: p(9.6, 4.8), control1: p(5.16, 3.04), control2: p(6.56, 4.44))
        path.addCurve(to: p(4.8, 9.6), control1: p(6.56, 5.16), control2: p(5.16, 6.56))
        path.addCurve(to: p(0, 4.8), control1: p(4.44, 6.56), control2: p(3.04, 5.16))
        path.addCurve(to: p(4.8, 0), control1: p(3.04, 4.44), control2: p(4.44, 3.04))
        path.closeSubpath()
        return path
    }
}

/// Rotation start-position glyph (307:237, 60×56): upper arm down the side, forearm forward, elbow dot,
/// watch face, thumb up — #0F6E56 strokes, white elbow dot, #1E1E1E watch. Drawn as a vector so it stays sharp
/// at any size; sits inside a 99 pt `ProgressRing` on the Start Position screen.
struct PoseElbowSide: View {
    var body: some View {
        GeometryReader { geo in
            let s = min(geo.size.width / 60, geo.size.height / 56)
            let g = WatchTheme.good
            ZStack(alignment: .topLeading) {
                Path { p in p.move(to: CGPoint(x: 14 * s, y: 4 * s)); p.addLine(to: CGPoint(x: 14 * s, y: 34 * s)) }
                    .stroke(g, style: StrokeStyle(lineWidth: 7 * s, lineCap: .round))
                Path { p in p.move(to: CGPoint(x: 14 * s, y: 36 * s)); p.addLine(to: CGPoint(x: 46 * s, y: 36 * s)) }
                    .stroke(g, style: StrokeStyle(lineWidth: 7 * s, lineCap: .round))
                Path { p in p.move(to: CGPoint(x: 50 * s, y: 36 * s)); p.addLine(to: CGPoint(x: 50 * s, y: 24 * s)) }
                    .stroke(g, style: StrokeStyle(lineWidth: 5 * s, lineCap: .round))
                Path(roundedRect: CGRect(x: 28 * s, y: 29 * s, width: 10 * s, height: 14 * s), cornerRadius: 3 * s)
                    .fill(WatchTheme.charcoal)
                Path(ellipseIn: CGRect(x: 8.5 * s, y: 30.5 * s, width: 11 * s, height: 11 * s)).fill(Color.white)
                Path(ellipseIn: CGRect(x: 8.5 * s, y: 30.5 * s, width: 11 * s, height: 11 * s))
                    .stroke(g, lineWidth: 2.5 * s)
            }
        }
        .aspectRatio(60.0 / 56.0, contentMode: .fit)
        .accessibilityHidden(true)
    }
}

#Preview("Mascots") {
    VStack(spacing: 6) {
        HStack(spacing: 4) {
            Mascot(.wave, size: 72)
            Mascot(.cheer, size: 72)
            Mascot(.rest, size: 72)
        }
        HStack(spacing: 12) {
            Sparkle(size: 9.6)
            Sparkle(size: 7.2)
            ZStack { ProgressRing(progress: 0.85, lineWidth: 7); PoseElbowSide().frame(width: 60, height: 56) }
                .frame(width: 99, height: 99)
        }
    }
    .background(WatchTheme.background)
}
