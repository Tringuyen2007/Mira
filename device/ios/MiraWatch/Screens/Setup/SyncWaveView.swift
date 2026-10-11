import SwiftUI

/// Setup 4 Sync Wave (Figma 304:175): the patient waves; MotionService (`.sync` mode) streams accelerometer
/// windows to the phone and the server's `sync_progress` fills the ring; `locked` moves on to Learn the Taps.
/// If nothing locks within `skipAfter` seconds (phone or server away), a "Skip for now" link appears below
/// the frame so setup can't get stuck.
struct SyncWaveView: View {
    @Environment(WatchSessionModel.self) private var model
    @State private var showSkip = false
    static let skipAfter: Duration = .seconds(20)

    var body: some View {
        WatchCanvas(height: showSkip ? 280 : 248, halo: .init(ringRadius: 58, center: CGPoint(x: 104, y: 92))) {
            CanvasStatusBar(title: "Mira")
            ProgressRing(progress: model.flow.state.syncProgress, lineWidth: 8).figma(54, 42, 100, 100)
            Image(systemName: "applewatch.radiowaves.left.and.right")
                .symbolRenderingMode(.monochrome)
                .font(.system(size: 30, weight: .medium)).foregroundStyle(WatchTheme.good)
                .figma(82, 72, 44, 40)
                .accessibilityHidden(true)
            Text("Give your arm a little wave")
                .watchText(18, .medium).lineLimit(2)
                .figma(16, 152, 176, 46)
            Text("Syncing with cameras")
                .watchText(16, color: WatchTheme.muted).lineLimit(1)
                .figma(16, 204, 176, 19)
            if showSkip {
                Button("Skip for now") { model.send(.sync(progress: 1, locked: true)) }
                    .buttonStyle(WatchPressStyle())
                    .font(WatchTheme.inter(16, .medium)).foregroundStyle(WatchTheme.accent)
                    .figma(16, 240, 176, 32)
            }
        }
        .task {
            try? await Task.sleep(for: Self.skipAfter)
            showSkip = true
        }
    }
}
