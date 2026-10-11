import SwiftUI

/// "Watch / Button Primary" (303:114): #1E1E1E capsule, 50 high (176 on the sheet, 168 = full width minus 20 on
/// screens), Inter Medium 17 white, shadow rgba(143,186,204,.25) y 6 blur 8.
struct WatchPrimaryButton: View {
    let title: String
    let action: () -> Void

    init(_ title: String, action: @escaping () -> Void) {
        self.title = title
        self.action = action
    }

    var body: some View {
        Button(action: action) {
            Text(title).font(WatchTheme.inter(17, .medium)).foregroundStyle(.white)
                .lineLimit(1).minimumScaleFactor(0.7)
                .frame(maxWidth: .infinity, minHeight: WatchTheme.buttonHeight)
                .background(WatchTheme.charcoal, in: Capsule())
                .contentShape(Capsule())
        }
        .buttonStyle(WatchPressStyle())
        .shadow(color: WatchTheme.buttonShadow, radius: 4, y: 6)
    }
}

/// "Watch / Button Secondary" (303:116): white 85 % capsule with a 1.5 pt #857F8A outline, Inter Medium 17 ink.
struct WatchSecondaryButton: View {
    let title: String
    let action: () -> Void

    init(_ title: String, action: @escaping () -> Void) {
        self.title = title
        self.action = action
    }

    var body: some View {
        Button(action: action) {
            Text(title).font(WatchTheme.inter(17, .medium)).foregroundStyle(WatchTheme.ink)
                .lineLimit(1).minimumScaleFactor(0.7)
                .frame(maxWidth: .infinity, minHeight: WatchTheme.buttonHeight)
                .background(WatchTheme.surface, in: Capsule())
                .overlay(Capsule().strokeBorder(WatchTheme.border, lineWidth: 1.5))
                .contentShape(Capsule())
        }
        .buttonStyle(WatchPressStyle())
        .shadow(color: WatchTheme.buttonShadow, radius: 4, y: 6)
    }
}

/// Settings row card (307:397): white 85 % capsule, 1 pt white border, shadow rgba(143,186,204,.2) y 4 blur 12,
/// title Inter SemiBold 17 ink over value Inter Regular 16 ink-soft, chevron (#5E5866, 10×16) when tappable.
/// Read-only rows (e.g. "Heart limit", set by the therapist) pass no action and show no chevron.
struct WatchSettingRow: View {
    let title: String
    let value: String
    let action: (() -> Void)?

    init(_ title: String, value: String, action: (() -> Void)? = nil) {
        self.title = title
        self.value = value
        self.action = action
    }

    var body: some View {
        if let action {
            Button(action: action) { card(chevron: true) }
                .buttonStyle(WatchPressStyle())
        } else {
            card(chevron: false).accessibilityElement(children: .combine)
        }
    }

    private func card(chevron: Bool) -> some View {
        HStack(spacing: 6) {
            VStack(alignment: .leading, spacing: 0) {
                Text(title).font(WatchTheme.inter(17, .semibold)).foregroundStyle(WatchTheme.ink)
                Text(value).font(WatchTheme.inter(16)).foregroundStyle(WatchTheme.muted)
            }
            .lineLimit(1).minimumScaleFactor(0.75)
            Spacer(minLength: 0)
            if chevron {
                Image(systemName: "chevron.right").font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(WatchTheme.icon).accessibilityHidden(true)
            }
        }
        .padding(.leading, 15).padding(.trailing, 16)
        .frame(maxWidth: .infinity, minHeight: WatchTheme.buttonHeight)
        .background(WatchTheme.surface, in: Capsule())
        .overlay(Capsule().strokeBorder(Color.white, lineWidth: 1))
        .contentShape(Capsule())
        .shadow(color: WatchTheme.haloStroke.opacity(0.2), radius: 6, y: 4)
    }
}

/// Plain press feedback (watchOS's default button style would add its own grey platter).
struct WatchPressStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.96 : 1)
            .opacity(isEnabled ? 1 : 0.55)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

#Preview("Buttons") {
    VStack(spacing: 8) {
        WatchPrimaryButton("Next") {}
        WatchSecondaryButton("Not now") {}
        WatchSettingRow("Taps", value: "Strong") {}
        WatchSettingRow("Heart limit", value: "120 bpm · by Dr. Lee")
    }
    .padding(.horizontal, WatchTheme.screenPadding)
    .background(WatchTheme.background)
}
