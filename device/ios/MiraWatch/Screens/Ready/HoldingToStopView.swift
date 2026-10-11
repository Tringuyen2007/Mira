import SwiftUI

/// Ready 3 Holding to Stop (Figma 305:173). The screen is drawn by `ReadyView(holding: true)` — the press that
/// started on the Ready button must stay alive — with the charcoal 151-pt `ProgressRing` (1.5 s), the 120-pt
/// button with a white stop square, and this caption.
struct HoldingToStopCaption: View {
    var body: some View {
        Text("Keep holding to stop").watchText(17).lineLimit(1)
    }
}
