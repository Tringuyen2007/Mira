import SwiftUI

/// Ready 1 Ready (Figma 305:134) and Ready 3 Holding to Stop (305:173) as one view, because the long press that
/// starts on the Ready button has to survive the switch to the Holding screen. `RootView` renders this for both
/// `.ready` and `.holdingToStop` with the same identity; `holding` picks the visuals.
///
/// The big button is a real `Button` (so the double-tap gesture can trigger it):
/// - tap (or double-tap, watchOS 11+, Settings → Double-tap) → `ready()`;
/// - press ≥ `holdDelay` → `.holdBegan` (Holding to Stop), the ring fills over `HoldToStop.duration` (1.5 s from the
///   press) and completes with `.holdCompleted` (button `stop` + stop haptic); releasing early → `.holdCancelled`.
struct ReadyView: View {
    @Environment(WatchSessionModel.self) private var model
    let holding: Bool

    /// A press shorter than this is a tap, so a tap never flashes the Holding screen.
    static let holdDelay: TimeInterval = 0.25

    @State private var hold = HoldToStop()
    @State private var holdTask: Task<Void, Never>?
    @State private var holdBegun = false
    @State private var suppressNextAction = false

    var body: some View {
        let state = model.flow.state
        WatchCanvas(halo: .init(ringRadius: holding ? 78 : 72, center: CGPoint(x: 104, y: 108))) {
            CanvasStatusBar(title: model.exerciseTitle)
            if holding {
                TimelineView(.animation(minimumInterval: 1 / 30, paused: !hold.isHolding)) { ctx in
                    ProgressRing(progress: ringProgress(at: ctx.date), lineWidth: 6, tint: WatchTheme.charcoal)
                }
                .figma(28.5, 32.5, 151, 151)
            }
            bigButton
                .figma(holding ? 44 : 40, holding ? 48 : 44, holding ? 120 : 128, holding ? 120 : 128)
            if holding {
                HoldingToStopCaption().figma(16, 196, 176, 21)
            } else {
                Text("Hold to stop")
                    .watchText(16, color: WatchTheme.muted).lineLimit(1)
                    .figma(16, 186, 176, 19)
                if state.prefs.doubleTap {
                    HStack(spacing: 4) {
                        Image(systemName: "hand.raised").font(.system(size: 14, weight: .medium))
                            .foregroundStyle(WatchTheme.icon).frame(width: 18, height: 18)
                        Text("or double-tap").font(WatchTheme.inter(16)).foregroundStyle(WatchTheme.muted)
                    }
                    .lineLimit(1)
                    .accessibilityElement(children: .combine)
                    .figma(16, 208, 176, 19)
                }
            }
        }
        .onDisappear { holdTask?.cancel() }
    }

    private var bigButton: some View {
        Button {
            if suppressNextAction { suppressNextAction = false; return }
            model.ready()
        } label: {
            ZStack {
                Circle().fill(WatchTheme.charcoal)
                    .shadow(color: WatchTheme.buttonShadow, radius: 4, y: 6)
                if holding {
                    RoundedRectangle(cornerRadius: 7).fill(Color.white).frame(width: 28, height: 28)
                } else {
                    Text("I'm ready").font(WatchTheme.inter(22)).foregroundStyle(.white)
                        .lineLimit(1).minimumScaleFactor(0.7)
                }
            }
            .contentShape(Circle())
        }
        .buttonStyle(PressReportingStyle { pressed in pressed ? pressBegan() : pressEnded() })
        .doubleTapPrimary(model.flow.state.prefs.doubleTap)
        .accessibilityLabel(holding ? "Keep holding to stop" : "I'm ready")
        .accessibilityHint("Tap to start. Touch and hold to stop the session.")
        .accessibilityAction(named: "Stop session") { model.send(.holdBegan); model.send(.holdCompleted) }
    }

    private func ringProgress(at date: Date) -> Double {
        #if DEBUG
        if let p = model.previewHoldProgress, !hold.isHolding { return p }
        #endif
        return hold.progress(at: date.timeIntervalSince1970)
    }

    private func pressBegan() {
        holdTask?.cancel()
        suppressNextAction = false
        holdBegun = false
        let start = Date().timeIntervalSince1970
        hold.begin(at: start)
        let total = hold.duration
        holdTask = Task { @MainActor in
            try? await Task.sleep(for: .seconds(Self.holdDelay))
            guard !Task.isCancelled else { return }
            holdBegun = true
            suppressNextAction = true
            model.send(.holdBegan)
            try? await Task.sleep(for: .seconds(max(total - Self.holdDelay, 0)))
            guard !Task.isCancelled else { return }
            _ = hold.end(at: start + total)
            holdBegun = false
            model.send(.holdCompleted)
        }
    }

    private func pressEnded() {
        holdTask?.cancel()
        holdTask = nil
        guard hold.isHolding else { return }
        _ = hold.end(at: Date().timeIntervalSince1970)
        if holdBegun {
            holdBegun = false
            model.send(.holdCancelled)
        }
    }
}

/// Reports press start / end of a button (watchOS has no press-and-release callbacks on `Button`).
struct PressReportingStyle: ButtonStyle {
    let onPressChange: (Bool) -> Void

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
            .onChange(of: configuration.isPressed) { _, pressed in onPressChange(pressed) }
    }
}
