import SwiftUI

/// The three haptic patterns the watch shows when it buzzes (303:136 description):
/// 1 tap = good rep, 2 buzzes = fix form, long buzz = set done. Shape carries the meaning, not colour.
enum HapticChipKind: Equatable {
    case tap, double, long

    /// Default label for the kind, as used on the sample screens.
    var defaultLabel: String {
        switch self {
        case .tap: return "Good rep"
        case .double: return "Fix form"
        case .long: return "Set done"
        }
    }

    /// Tap and Long are accent green (303:123, 303:132); Double is alert-text red (303:127).
    var color: Color { self == .double ? WatchTheme.warn : WatchTheme.accent }

    var accessibilityPattern: String {
        switch self {
        case .tap: return "one tap"
        case .double: return "two buzzes"
        case .long: return "long buzz"
        }
    }
}

/// "Watch / Haptic Chip" (303:136): white 85 % pill (radius 16, padding 6 / 10 / 12, gap 6), a 28×12 pattern
/// (● / ●● / ▬) and the label in Inter Medium 16, both in the kind's colour.
struct HapticChip: View {
    let kind: HapticChipKind
    let label: String

    init(_ kind: HapticChipKind, label: String) {
        self.kind = kind
        self.label = label
    }

    var body: some View {
        HStack(spacing: 6) {
            HapticPatternGlyph(kind: kind)
            Text(label).font(WatchTheme.inter(16, .medium)).foregroundStyle(kind.color)
                .lineLimit(1).minimumScaleFactor(0.8)
        }
        .padding(.vertical, 6).padding(.leading, 10).padding(.trailing, 12)
        .frame(minHeight: WatchTheme.chipHeight)
        .background(WatchTheme.surface, in: RoundedRectangle(cornerRadius: WatchTheme.chipRadius))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(label), \(kind.accessibilityPattern)")
    }
}

/// The 28×12 pattern glyph (303:124 / 303:128 / 303:133): one 10 pt dot centred; two 10 pt dots at x 2 and 16;
/// a 26×10 capsule.
struct HapticPatternGlyph: View {
    let kind: HapticChipKind

    var body: some View {
        ZStack {
            switch kind {
            case .tap:
                Circle().frame(width: 10, height: 10)
            case .double:
                HStack(spacing: 4) {
                    Circle().frame(width: 10, height: 10)
                    Circle().frame(width: 10, height: 10)
                }
            case .long:
                Capsule().frame(width: 26, height: 10)
            }
        }
        .foregroundStyle(kind.color)
        .frame(width: 28, height: 12)
        .accessibilityHidden(true)
    }
}

#Preview("Haptic chips") {
    VStack(spacing: 8) {
        HapticChip(.tap, label: "Good rep")
        HapticChip(.double, label: "Fix form")
        HapticChip(.long, label: "Set done")
    }
    .padding()
    .background(WatchTheme.background)
}
