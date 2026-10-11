import SwiftUI

/// Lays a screen out on the Figma 208-pt-wide canvas and scales it to the watch's width, so every screen keeps
/// the board's exact geometry (the 46 mm watch is 208×248, scale 1). Children place themselves with `.figma(…)`
/// in design points. Canvases taller than the screen (Learn the Taps 208×300, Heart Rate's "Not now") scroll.
/// The radial wash is drawn once by `RootView`; the halo rings are drawn here so they line up with the hero.
struct WatchCanvas<Content: View>: View {
    struct Halo {
        var ringRadius: CGFloat
        /// Centre in design points (Figma "Halos & orbit" frame centre).
        var center: CGPoint
    }

    var height: CGFloat = 248
    var halo: Halo?
    @ViewBuilder var content: () -> Content

    init(height: CGFloat = 248, halo: Halo? = nil, @ViewBuilder content: @escaping () -> Content) {
        self.height = height
        self.halo = halo
        self.content = content
    }

    var body: some View {
        GeometryReader { geo in
            let scale = max(geo.size.width, 1) / WatchTheme.designWidth
            let scaledHeight = height * scale
            if scaledHeight > geo.size.height + 1 {
                ScrollView(.vertical, showsIndicators: false) { canvas(scale: scale) }
            } else {
                canvas(scale: scale)
            }
        }
        .ignoresSafeArea()
    }

    private func canvas(scale: CGFloat) -> some View {
        ZStack(alignment: .topLeading) {
            if let halo {
                HaloBackground(ringRadius: halo.ringRadius,
                               center: UnitPoint(x: halo.center.x / WatchTheme.designWidth, y: halo.center.y / height),
                               showsWash: false)
                    .frame(width: WatchTheme.designWidth, height: height)
            }
            content()
        }
        .frame(width: WatchTheme.designWidth, height: height, alignment: .topLeading)
        .scaleEffect(scale, anchor: .topLeading)
        .frame(width: WatchTheme.designWidth * scale, height: height * scale, alignment: .topLeading)
    }
}

extension View {
    /// Places the view in the Figma frame (x, y, w, h), in design points on a `WatchCanvas`.
    func figma(_ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat, alignment: Alignment = .center) -> some View {
        frame(width: w, height: h, alignment: alignment).position(x: x + w / 2, y: y + h / 2)
    }

    /// Centred body text block as used on the setup / stop screens (x 16, w 176).
    func watchText(_ size: CGFloat, _ weight: Font.Weight = .regular, color: Color = WatchTheme.ink) -> some View {
        font(WatchTheme.inter(size, weight)).foregroundStyle(color)
            .multilineTextAlignment(.center).minimumScaleFactor(0.7)
    }
}

/// The status bar as screens use it on a canvas: the app title at x 34 / y 12; the system clock supplies the time.
struct CanvasStatusBar: View {
    let title: String
    var body: some View {
        WatchStatusBar(title: title, showsTime: false).figma(0, 0, WatchTheme.designWidth, WatchTheme.statusBarHeight)
    }
}
