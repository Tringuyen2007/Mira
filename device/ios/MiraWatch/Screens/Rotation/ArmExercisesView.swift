import SwiftUI

/// Rotation 1 Arm Exercises (Figma 307:196): today's watch exercises (`plan.filter(\.watch)`) as cards —
/// name, "N sets · R", chevron (a check disc once every set is done). Tap → `.chooseExercise(id)` → Start Position.
/// Opened by swiping from Ready while a watch exercise is unfinished. More than three rows scroll.
struct ArmExercisesView: View {
    @Environment(WatchSessionModel.self) private var model

    var body: some View {
        let rows = model.flow.state.watchExercises
        let height = max(248, 70 + CGFloat(rows.count) * 56 + 10)
        WatchCanvas(height: height, halo: .init(ringRadius: 39.5, center: CGPoint(x: 170, y: 30))) {
            CanvasStatusBar(title: "Mira")
            Text("Arm exercises")
                .font(WatchTheme.inter(18, .medium)).foregroundStyle(WatchTheme.ink).lineLimit(1)
                .accessibilityAddTraits(.isHeader)
                .figma(24, 40, 168, 22, alignment: .leading)
            if rows.isEmpty {
                Text("No arm exercises today").watchText(16, color: WatchTheme.muted).lineLimit(2)
                    .figma(16, 90, 176, 40)
            }
            ForEach(Array(rows.enumerated()), id: \.element.id) { i, row in
                ArmExerciseRow(entry: row) { model.send(.chooseExercise(row.id)) }
                    .figma(12, 70 + CGFloat(i) * 56, 184, 50)
            }
        }
    }
}

/// One exercise card (307:207): the settings-card style — white 85 % capsule, 1 pt white border, soft shadow;
/// name Inter SemiBold 17 ink over "N sets · R" Regular 16 ink-soft; chevron, or the green check disc (shape,
/// not colour) when `doneSets == sets`.
struct ArmExerciseRow: View {
    let entry: WatchFlowState.PlanEntry
    let action: () -> Void

    private var done: Bool { entry.sets > 0 && entry.doneSets >= entry.sets }
    private var detail: String { "\(entry.sets) \(entry.sets == 1 ? "set" : "sets") · \(entry.reps)" }

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                VStack(alignment: .leading, spacing: 0) {
                    Text(entry.name).font(WatchTheme.inter(17, .semibold)).foregroundStyle(WatchTheme.ink)
                    Text(detail).font(WatchTheme.inter(16)).foregroundStyle(WatchTheme.muted)
                }
                .lineLimit(1).minimumScaleFactor(0.75)
                Spacer(minLength: 0)
                if done {
                    SelectedCheck()
                } else {
                    Image(systemName: "chevron.right").font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(WatchTheme.icon).accessibilityHidden(true)
                }
            }
            .padding(.leading, 15).padding(.trailing, done ? 12 : 16)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(WatchTheme.surface, in: Capsule())
            .overlay(Capsule().strokeBorder(Color.white, lineWidth: 1))
            .contentShape(Capsule())
            .shadow(color: WatchTheme.haloStroke.opacity(0.2), radius: 6, y: 4)
        }
        .buttonStyle(WatchPressStyle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(entry.name), \(entry.sets) sets of \(entry.reps)\(done ? ", done" : "")")
        .accessibilityAddTraits(.isButton)
    }
}
