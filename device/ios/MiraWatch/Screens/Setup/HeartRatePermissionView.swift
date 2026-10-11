import SwiftUI

/// Setup 2 Heart Rate (Figma 304:138): heart in a white circle, "Share heart rate?", "Mira uses it to keep you safe.",
/// Allow (→ HealthKit). "Not now" sits just below the 248-pt frame (scroll) — the HR chip then shows "—".
struct HeartRatePermissionView: View {
    @Environment(WatchSessionModel.self) private var model
    @State private var asking = false

    var body: some View {
        WatchCanvas(height: 306, halo: .init(ringRadius: 20, center: CGPoint(x: 104, y: 78))) {
            CanvasStatusBar(title: "Mira")
            Circle().fill(Color.white)
                .shadow(color: WatchTheme.haloStroke.opacity(0.25), radius: 6, y: 4)
                .figma(76, 50, 56, 56)
            HeartShape().fill(WatchTheme.berry).figma(90, 64, 28, 28)
                .accessibilityHidden(true)
            Text("Share heart rate?")
                .watchText(20, .medium).lineLimit(1)
                .figma(16, 110, 176, 24)
            Text("Mira uses it to keep you safe.")
                .watchText(17, color: WatchTheme.muted).lineLimit(2)
                .figma(16, 137, 176, 42)
            WatchPrimaryButton("Allow") {
                guard !asking else { return }
                asking = true
                Task { await model.allowHeartRate() }
            }
            .figma(20, 186, 168, 50)
            WatchSecondaryButton("Not now") { model.send(.heartRateAnswered) }
                .figma(20, 246, 168, 50)
        }
    }
}
