import SwiftUI
import UIKit

/// Watch design tokens, read from the Figma "AppleWatch Hi-FI" board (file UwyMjFR4Vbi0Y1Ej38qTlR) with
/// get_variable_defs / get_design_context. Node ids are cited per value. Where a value matches the phone's
/// `MiraUI` token it keeps the same name (`ink`, `soft`, `accent`, `charcoal`, `border`, `alert`).
/// The design canvas is 208×248 pt; components lay out with stacks and flexible frames, and the few
/// size-driven pieces (halo, ring, gauge, bars) scale from the space they are given.
enum WatchTheme {
    // MARK: Colours

    /// `color/ink` #1C1A22 — headings, counts, chip numbers (303:121, 305:229).
    static let ink = Color(hex: 0x1C1A22)
    /// `color/ink-soft` #5E5A63 — secondary text, status-bar time, "bpm" (303:113, 303:122).
    static let muted = Color(hex: 0x5E5A63)
    /// Phone name for `muted`.
    static let soft = muted
    /// `color/accent-text-small` #0B5A47 — status-bar title, Tap/Long haptic chip, eyebrows (303:112, 303:126).
    static let accent = Color(hex: 0x0B5A47)
    /// #0F6E56 — progress ring, gauge arc, goal-reached bars, pose glyph (305:163, 307:257, 307:308, 307:237).
    static let good = Color(hex: 0x0F6E56)
    /// `color/alert-text` #B42318 — warning-card title, Double haptic chip (303:131, 305:258).
    static let warn = Color(hex: 0xB42318)
    /// `color/alert` #D92D20 — warning-card border and triangle (305:248, 305:249). Same as phone `MiraUI.alert`.
    static let danger = Color(hex: 0xD92D20)
    static let alert = danger
    /// `color/berry` #9F3462 — heart icon, "Hi, Maya!", "Set done!" (303:119, 304:133, 305:295).
    static let berry = Color(hex: 0x9F3462)
    /// #1E1E1E — primary button fill, big Ready button, goal tick, bars goal line (303:114, 305:144, 307:257).
    static let charcoal = Color(hex: 0x1E1E1E)
    /// #857F8A — secondary button outline (303:116). Same as phone `MiraUI.border`.
    static let border = Color(hex: 0x857F8A)
    /// #5E5866 — line icons: double-tap hand, chevron (305:147, 307:400).
    static let icon = Color(hex: 0x5E5866)
    /// White 85 % — chips, secondary button, settings cards (303:118, 303:116, 307:397).
    static let surface = Color.white.opacity(0.85)
    /// White 92 % — warning card fill (305:248).
    static let cardFill = Color.white.opacity(0.92)
    /// #90BBCC — outer halo rings (at 32 % / 24 %), unreached per-rep bars, shadows (304:116, 307:308).
    static let haloStroke = Color(hex: 0x90BBCC)
    static let sky = haloStroke
    /// Ring / gauge track: white 90 % (305:161) and 95 % (307:257).
    static let track = Color.white.opacity(0.9)

    /// Screen background: radial #F5FAF8 → #FBF2EB (50 %) → #E5EEED, centred, filling the 208×248 frame (all screens).
    static let background = EllipticalGradient(stops: [
        .init(color: Color(hex: 0xF5FAF8), location: 0),
        .init(color: Color(hex: 0xFBF2EB), location: 0.5),
        .init(color: Color(hex: 0xE5EEED), location: 1),
    ], center: .center, startRadiusFraction: 0, endRadiusFraction: 0.5)

    // MARK: Type

    /// Inter (bundled from Mira/Design/Fonts via UIAppFonts), falling back to the system font if the face is missing.
    /// Figma uses Light (big numbers, hello lines), Regular (body), Medium (buttons, chips), SemiBold (titles).
    static func inter(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font {
        let name: String
        switch weight {
        case .ultraLight, .thin, .light: name = "Inter-Light"
        case .medium: name = "Inter-Medium"
        case .semibold, .bold, .heavy, .black: name = "Inter-SemiBold"
        default: name = "Inter-Regular"
        }
        return UIFont(name: name, size: size) != nil ? .custom(name, size: size) : .system(size: size, weight: weight)
    }

    // MARK: Metrics

    /// Figma canvas width (all screens are 208×248; Settings scrolls to 208×360).
    static let designWidth: CGFloat = 208
    /// Side inset for full-width controls and cards (buttons at x 20 w 168 → 20; cards at x 12 → see `cardInset`).
    static let screenPadding: CGFloat = 20
    /// Warning / settings cards sit 12 pt from the edge (305:248, 307:397).
    static let cardInset: CGFloat = 12
    /// Warning card radius (305:248).
    static let cornerRadius: CGFloat = 28
    /// Chip height: 6 + 19 + 6 (303:118, 303:136); chip radius 16.
    static let chipHeight: CGFloat = 31
    static let chipRadius: CGFloat = 16
    /// Buttons and settings cards: 50 high, radius 25 (303:114, 303:116, 307:397).
    static let buttonHeight: CGFloat = 50
    /// Status bar: 36 high, text baseline row at y 12, title at x 34 (303:111).
    static let statusBarHeight: CGFloat = 36
    static let statusBarInset: CGFloat = 34
    /// Small tracked caps ("GOOD REPS", "REP 4 OF 10"): Inter Medium 16, tracking 2.24 (305:231, 307:256).
    static let capsTracking: CGFloat = 2.24
    /// Drop shadow on buttons: rgba(143,186,204,0.25), y 6, blur 8 (303:114).
    static let buttonShadow = haloStroke.opacity(0.25)
}

extension Color {
    /// 0xRRGGBB → sRGB colour.
    init(hex: UInt32, opacity: Double = 1) {
        self.init(.sRGB,
                  red: Double((hex >> 16) & 0xFF) / 255,
                  green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255,
                  opacity: opacity)
    }
}

extension View {
    /// Tracked small caps label used above hero numbers ("GOOD REPS", "BEST TURN").
    func watchCaps() -> some View {
        font(WatchTheme.inter(16, .medium)).tracking(WatchTheme.capsTracking)
            .foregroundStyle(WatchTheme.accent).textCase(.uppercase)
    }
}
