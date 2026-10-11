import SwiftUI

/// The single watch screen, driven by the phone's live snapshot. Same look as the
/// `device/watch` demo (mint ring, pink heart card), but with real data and system fonts.
struct WatchLiveView: View {
    @Environment(WatchSessionModel.self) private var model
    @State private var confirmsStop = false

    private enum Screen: Equatable { case ready, live, paused, done }

    private var screen: Screen {
        guard let snapshot = model.snapshot else { return .ready }
        if snapshot.phase == "done" { return .done }
        if snapshot.phase == "paused" || snapshot.phase == "incident" || snapshot.paused { return .paused }
        if ["countdown", "active", "rest"].contains(snapshot.phase) { return .live }
        return .ready
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                header
                switch screen {
                case .ready: ready
                case .live: live
                case .paused: paused
                case .done: done
                }
                if let error = model.sendError {
                    Text(error).font(.system(size: 11)).foregroundStyle(WatchStyle.pink)
                        .multilineTextAlignment(.center)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
        }
        .id(screen)
        .background(Color.black)
        .foregroundStyle(.white)
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if screen == .live || screen == .paused {
                stopButton
                    .padding(.horizontal, 12)
                    .padding(.vertical, 4)
                    .background(Color.black)
            }
        }
        .confirmationDialog("Stop this session?", isPresented: $confirmsStop, titleVisibility: .visible) {
            Button("Stop", role: .destructive) { model.stop() }
            Button("Keep going", role: .cancel) {}
        }
    }

    private var header: some View {
        HStack(spacing: 6) {
            Text("MIRA").font(.system(size: 12, weight: .medium)).tracking(2.5)
                .foregroundStyle(WatchStyle.mint)
            Spacer()
            Circle()
                .fill(model.phoneReachable ? WatchStyle.mint : WatchStyle.secondary.opacity(0.5))
                .frame(width: 7, height: 7)
                .accessibilityLabel(model.phoneReachable ? "iPhone connected" : "iPhone not connected")
        }
    }

    // MARK: Screens

    private var ready: some View {
        VStack(spacing: 9) {
            Image("WatchMascot")
                .resizable().scaledToFit().frame(height: 70)
                .accessibilityHidden(true)
            Text("A little progress,\none rep at a time.")
                .font(.system(size: 17, weight: .medium)).multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
            if let name = model.snapshot?.exerciseName {
                Text(name).font(.system(size: 12)).foregroundStyle(WatchStyle.secondary)
            }
            action("I'm ready", symbol: "checkmark", color: WatchStyle.mint) { model.ready() }
            Text(model.phoneReachable ? "Mira will start when you are" : "Open Mira on your iPhone")
                .font(.system(size: 10)).foregroundStyle(WatchStyle.secondary)
                .multilineTextAlignment(.center)
        }
    }

    private var live: some View {
        let snapshot = model.snapshot
        let rep = snapshot?.rep ?? 0
        let target = snapshot?.repsTarget
        let progress = target.map { $0 > 0 ? min(1, CGFloat(rep) / CGFloat($0)) : 0 } ?? 0
        return VStack(spacing: 10) {
            Text(snapshot?.exerciseName ?? "Exercise")
                .font(.system(size: 18, weight: .medium)).multilineTextAlignment(.center)
            ZStack {
                Circle().stroke(WatchStyle.card, lineWidth: 7)
                Circle().trim(from: 0, to: progress)
                    .stroke(WatchStyle.mint, style: StrokeStyle(lineWidth: 7, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                    .animation(.easeOut(duration: 0.3), value: progress)
                VStack(spacing: 0) {
                    Text("\(rep)").font(.system(size: 38, weight: .semibold)).monospacedDigit()
                    Text(target.map { "of \($0) reps" } ?? "reps").font(.system(size: 11))
                        .foregroundStyle(WatchStyle.secondary)
                }
            }
            .frame(width: 106, height: 106)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(target.map { "\(rep) of \($0) repetitions" } ?? "\(rep) repetitions")
            if let set = snapshot?.set, let sets = snapshot?.setsTarget {
                Text("Set \(set) of \(sets)").font(.system(size: 11)).foregroundStyle(WatchStyle.secondary)
            }
            heartRateCard
            Text(liveCaption)
                .font(.system(size: 12, weight: .medium)).foregroundStyle(WatchStyle.mint)
                .multilineTextAlignment(.center)
        }
    }

    private var liveCaption: String {
        switch model.snapshot?.phase {
        case "countdown": return "Get ready…"
        case "rest": return "Rest a moment."
        default: return (model.snapshot?.rep ?? 0) == 0 ? "Take your time" : "Nice work. Keep it steady."
        }
    }

    private var paused: some View {
        VStack(spacing: 12) {
            Image(systemName: "pause.circle.fill")
                .font(.system(size: 38)).foregroundStyle(WatchStyle.pink)
                .accessibilityHidden(true)
            Text("Take a breather").font(.system(size: 18, weight: .medium))
                .multilineTextAlignment(.center)
            if let snapshot = model.snapshot {
                Text(snapshot.repsTarget.map { "\(snapshot.rep) of \($0) reps" } ?? "\(snapshot.rep) reps")
                    .font(.system(size: 12)).foregroundStyle(WatchStyle.secondary)
            }
            heartRateCard
            action("Resume", symbol: "play.fill", color: WatchStyle.mint) { model.ready() }
        }
    }

    private var done: some View {
        let summary = model.summary
        let reps = summary?.repsTotal ?? model.snapshot?.rep ?? 0
        return VStack(spacing: 12) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 38)).foregroundStyle(WatchStyle.mint)
                .accessibilityHidden(true)
            Text("Session complete!")
                .font(.system(size: 20, weight: .medium)).multilineTextAlignment(.center)
            Text("\(reps)").font(.system(size: 32, weight: .semibold)).monospacedDigit()
            Text(summary?.goodReps.map { "reps · \($0) with good form" } ?? "reps")
                .font(.system(size: 12)).foregroundStyle(WatchStyle.secondary)
                .multilineTextAlignment(.center)
            if let score = summary?.formScore {
                HStack(spacing: 7) {
                    Image(systemName: "star.fill").foregroundStyle(WatchStyle.mint)
                        .accessibilityHidden(true)
                    Text("\(score)").font(.system(size: 18, weight: .semibold)).monospacedDigit()
                    Text("form score").font(.system(size: 10, weight: .medium))
                }
                .frame(maxWidth: .infinity).padding(.vertical, 8)
                .background(WatchStyle.card, in: RoundedRectangle(cornerRadius: 16))
                .accessibilityElement(children: .combine)
            }
            Text("Every little step counts.")
                .font(.system(size: 12)).multilineTextAlignment(.center)
            action("Back to Mira", symbol: "arrow.uturn.backward", color: WatchStyle.mint) {
                model.dismissSummary()
            }
        }
    }

    // MARK: Pieces

    private var heartRateCard: some View {
        let fromMira = model.snapshot?.hr
        let bpm = fromMira ?? model.localHeartRate
        let source = fromMira != nil ? "from Mira" : (bpm != nil ? "watch" : (model.heartRateStatus ?? "waiting"))
        return HStack(spacing: 7) {
            Image(systemName: "heart.fill").foregroundStyle(WatchStyle.pink)
                .accessibilityHidden(true)
            Text(bpm.map(String.init) ?? "—").font(.system(size: 18, weight: .semibold)).monospacedDigit()
            VStack(alignment: .leading, spacing: 0) {
                Text("BPM").font(.system(size: 10, weight: .medium))
                Text(source).font(.system(size: 9)).foregroundStyle(WatchStyle.secondary)
                    .lineLimit(2)
            }
        }
        .frame(maxWidth: .infinity).padding(.vertical, 8)
        .background(WatchStyle.card, in: RoundedRectangle(cornerRadius: 16))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(bpm.map { "Heart rate, \($0) beats per minute" } ?? "Heart rate not available")
    }

    private var stopButton: some View {
        Button {
            confirmsStop = true
        } label: {
            Label("Stop", systemImage: "stop.fill")
                .font(.system(size: 14, weight: .medium))
                .frame(maxWidth: .infinity, minHeight: 44)
                .foregroundStyle(WatchStyle.pink)
                .background(WatchStyle.card, in: Capsule())
        }
        .buttonStyle(.plain)
        .simultaneousGesture(LongPressGesture(minimumDuration: 1).onEnded { _ in
            confirmsStop = false
            model.stop()
        })
        .accessibilityHint("Tap to confirm or hold for one second to stop")
    }

    private func action(_ title: String, symbol: String, color: Color, foreground: Color = .black, perform: @escaping () -> Void) -> some View {
        Button(action: perform) {
            Label(title, systemImage: symbol)
                .font(.system(size: 14, weight: .medium))
                .frame(maxWidth: .infinity, minHeight: 44)
                .foregroundStyle(foreground)
                .background(color, in: Capsule())
        }
        .buttonStyle(.plain)
    }
}

enum WatchStyle {
    static let mint = Color(red: 0.56, green: 0.85, blue: 0.73)
    static let pink = Color(red: 0.94, green: 0.68, blue: 0.81)
    static let card = Color(red: 0.10, green: 0.13, blue: 0.12)
    static let secondary = Color(red: 0.69, green: 0.73, blue: 0.71)
}

#Preview { WatchLiveView().environment(WatchSessionModel()) }
