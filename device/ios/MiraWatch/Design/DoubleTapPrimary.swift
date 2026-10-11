import SwiftUI

/// Double-tap gesture (watchOS 11+) on the screen's primary button, gated by Settings → Double-tap.
/// Apply to a `Button`: the system then runs the button's action on a double-tap of thumb and finger.
struct DoubleTapPrimary: ViewModifier {
    let enabled: Bool

    func body(content: Content) -> some View {
        if #available(watchOS 11, *), enabled {
            content.handGestureShortcut(.primaryAction)
        } else {
            content
        }
    }
}

extension View {
    func doubleTapPrimary(_ enabled: Bool) -> some View { modifier(DoubleTapPrimary(enabled: enabled)) }
}
