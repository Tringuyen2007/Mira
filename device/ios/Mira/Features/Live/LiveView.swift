import SwiftUI

struct LiveView: View {
    enum Screen: String { case live, paused, pain, stop }
    @Environment(AppState.self) private var state
    @Environment(\.miraDesignPreview) private var preview
    @Environment(\.dismiss) private var dismiss
    @State private var screen: Screen
    @State private var pain = 6.0
    @State private var commandError: String?
    @State private var isSending = false
    init(initialScreen: Screen = .live) { _screen = State(initialValue: initialScreen) }
    private var online: Bool { preview || state.liveSession.eventSocket.state == .connected }
    private var displayed: Screen {
        if screen == .live && state.liveSession.live?.paused == true { return .paused }
        return screen
    }
    var body: some View {
        MiraPage(top: 0) {
            if displayed == .live || displayed == .paused { header }
            switch displayed {
            case .live: session
            case .paused: paused
            case .pain: painScreen
            case .stop: stopScreen
            }
            if let commandError { Text(commandError).font(MiraUI.font(15)).foregroundStyle(MiraUI.alert).accessibilityAddTraits(.updatesFrequently) }
            if !online { Text("Mira server offline · reconnecting…").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft) }
            if let alert = state.liveSession.alert {
                MiraCard {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Mira alert").font(MiraUI.font(20, "SemiBold"))
                        Text("\(alert.kind): \(alert.step)").font(MiraUI.font(17))
                        MiraButton(title: "I’m OK") { send(.imOK) }.disabled(!online || isSending)
                    }
                }
            }
        }.navigationBarTitleDisplayMode(.inline)
    }
    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 4) {
                Text(preview ? "Mini squats" : state.liveSession.live?.exerciseId ?? "Live session").font(MiraUI.font(24, "SemiBold"))
                Text(preview ? "Set 2 of 3" : state.liveSession.live?.phase ?? "Waiting for Mira").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
            }
            Spacer()
            HStack(spacing: 6) {
                FigmaIcon(screen: "live", slot: "imgLiveDot", size: 10)
                Text(online ? "Live with Mira" : "Mira offline").font(MiraUI.font(15, "Medium"))
            }.padding(10).background(.white.opacity(0.8), in: Capsule())
        }
    }
    private var session: some View {
        VStack(spacing: 16) {
            if state.preferences.showCaptions && (preview || state.liveSession.cue != nil) {
                MiraCard(padding: 18) {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack(spacing: 10) {
                            FigmaIcon(screen: "live", slot: "imgIconWarning", size: 30)
                            Text(preview ? "Even out your weight" : "Mira’s guidance").font(MiraUI.font(24, "SemiBold"))
                        }
                        Text(preview ? "Push down through your right heel." : state.liveSession.cue ?? "")
                            .font(MiraUI.font(state.preferences.extraLargeCaptions ? 26 : 20)).foregroundStyle(MiraUI.soft)
                    }
                }.overlay { RoundedRectangle(cornerRadius: 28).stroke(MiraUI.alert, lineWidth: preview || !(state.liveSession.live?.flags.isEmpty ?? true) ? 2.5 : 0) }
            }
            if preview {
                FigmaIllustration(name: "live")
                    .overlay(alignment: .bottomLeading) {
                        HStack { FigmaIcon(screen: "live", slot: "imgIconWarning1", size: 18); Text("Right knee").font(MiraUI.font(15, "SemiBold")) }
                            .padding(10).background(.white, in: Capsule())
                    }
                    .overlay(alignment: .bottomTrailing) { Text("Mirror view").font(MiraUI.font(13)).foregroundStyle(MiraUI.soft) }
            } else {
                MiraCard {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Your movement view").font(MiraUI.font(20, "SemiBold"))
                        Text(state.liveSession.pose == nil ? "Waiting for skeleton data from Mira." : "Skeleton data received. The movement renderer still needs the server’s coordinate contract.")
                            .font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                    }
                }
            }
            HStack(spacing: 12) {
                metric("Reps", preview ? "6" : state.liveSession.live.map { String($0.rep) } ?? "—", preview ? "of 10" : state.liveSession.live.map { "of \($0.repsTarget)" } ?? "Waiting for Mira")
                metric("Knee bend", preview ? "84°" : state.liveSession.live?.mainAngle.map { "\(Int($0.rounded()))°" } ?? "—", preview ? "Goal 90°" : "Live measurement")
            }
            if preview || state.liveSession.live != nil {
                let left = preview ? 64.0 : state.liveSession.live?.loadLPct ?? 50
                HStack(spacing: 2) {
                    Text("Left \(Int(left))%").frame(maxWidth: .infinity).frame(height: 42).background(MiraUI.charcoal).foregroundStyle(.white)
                    Text("Right \(Int(100-left))%").frame(maxWidth: .infinity).frame(height: 42).background(preview ? MiraUI.alert : MiraUI.accent).foregroundStyle(.white)
                }.font(MiraUI.font(17, "Medium")).clipShape(Capsule()).accessibilityLabel("Left load \(Int(left)) percent. Right load \(Int(100-left)) percent.")
            }
            HStack(spacing: 10) {
                control(state.liveSession.live == nil && !preview ? "Start" : "Pause", symbol: "pause.fill", primary: true) {
                    if preview { screen = .paused } else { send(state.liveSession.live == nil ? .start : .pause) }
                }
                control("Skip", symbol: "forward.end.fill") { if !preview { send(.skip) } }
                control("Stop", symbol: "stop.fill") { screen = .stop }
            }.disabled(!online || isSending)
            MiraButton(title: "It hurts", secondary: true, icon: ("live", "imgIconPain")) { reportPain() }.disabled(!online || isSending)
        }
    }
    private var paused: some View {
        VStack(spacing: 22) {
            FigmaIllustration(name: "paused").padding(.horizontal, -24)
            Text("Paused").font(MiraUI.font(46, "Light")).foregroundStyle(MiraUI.title)
            Text("Take your time.\nMira is here when you’re ready.").font(MiraUI.font(20)).foregroundStyle(MiraUI.soft).multilineTextAlignment(.center)
            MiraButton(title: "Resume") {
                if preview { screen = .live }
            }.disabled(!preview) // No resume command has been agreed with the server yet.
            if !preview { Text("Resume will be available when the server supports it.").font(MiraUI.font(15)).foregroundStyle(MiraUI.soft) }
            MiraButton(title: "Skip this exercise", secondary: true) { if !preview { send(.skip) } }.disabled(!online || isSending)
            MiraButton(title: "End session", secondary: true) { screen = .stop }
            Button("Report pain") { reportPain() }.font(MiraUI.font(17, "Medium")).disabled(!online || isSending)
        }.frame(maxWidth: .infinity)
    }
    private var painScreen: some View {
        VStack(spacing: 24) {
            MiraIntro(eyebrow: preview ? "MIRA HAS PAUSED" : "TELL MIRA", title: "How much does\nit hurt?", detail: "Move the dot to how you feel.\nThere’s no wrong answer.", size: 38)
            RatingCard(value: $pain, title: "Pain", detail: pain < 4 ? "Mild" : pain < 7 ? "Moderate" : "Severe", minimum: "No pain", maximum: "Worst pain")
            Text(preview ? "Mira will tell Dr. Lee how you’re feeling." : "Your pain report was requested. Sending a pain score and notifying your care team still need server support.")
                .font(MiraUI.font(17)).foregroundStyle(MiraUI.soft).padding(20).background(MiraUI.mist, in: RoundedRectangle(cornerRadius: 20))
            MiraButton(title: "Keep going gently") { screen = .live }.disabled(!preview)
            MiraButton(title: "Stop for today", secondary: true) { screen = .stop }
        }
    }
    private var stopScreen: some View {
        VStack(spacing: 24) {
            FigmaIllustration(name: "stop").padding(.horizontal, -24)
            MiraIntro(eyebrow: "", title: "End today’s session?", detail: preview ? "Mira will save your progress.\nYou’ve already done something good." : "You can ask Mira to stop your session.\nWait for Mira to confirm it has ended.", size: 38, centered: true)
            if preview {
                MiraCard {
                    HStack(spacing: 16) {
                        FigmaIcon(screen: "stop", slot: "imgSet1Done", size: 38)
                        Text("You’ve done 1 of 3 sets.").font(MiraUI.font(19, "Medium"))
                    }
                }
            }
            MiraButton(title: "Keep going") { screen = .live }
            MiraButton(title: "End session", secondary: true) {
                if preview { dismiss() } else { send(.stop) }
            }.disabled(!online || isSending)
        }
    }
    private func metric(_ label: String, _ value: String, _ detail: String) -> some View {
        MiraCard(padding: 18) {
            VStack(alignment: .leading, spacing: 6) {
                Text(label).font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                Text(value).font(MiraUI.font(48, "Light")).minimumScaleFactor(0.5).lineLimit(1)
                Text(detail).font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
            }
        }
    }
    private func control(_ title: String, symbol: String, primary: Bool = false, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 8) {
                if title == "Skip" { FigmaIcon(screen: "live", slot: "imgIconSkip", size: 24) }
                else { Image(systemName: symbol).font(.system(size: 20)) }
                Text(title).font(MiraUI.font(17, "Medium"))
            }
                .frame(maxWidth: .infinity, minHeight: 80).foregroundStyle(primary ? .white : MiraUI.ink)
                .background(primary ? MiraUI.charcoal : .white, in: RoundedRectangle(cornerRadius: 24))
                .overlay { if !primary { RoundedRectangle(cornerRadius: 24).stroke(MiraUI.border, lineWidth: 1.5) } }
        }.buttonStyle(.plain)
    }
    private func reportPain() {
        if preview { screen = .pain } else { send(.pain, then: .pain) }
    }
    private func send(_ command: CommandName, then destination: Screen? = nil) {
        guard !isSending else { return }
        isSending = true; commandError = nil
        Task {
            defer { isSending = false }
            do {
                try await state.liveSession.send(command)
                if let destination { screen = destination }
            } catch { commandError = "Mira couldn’t send that action. Please try again when Mira is available." }
        }
    }
}

struct RatingCard: View {
    @Binding var value: Double
    let title: String
    let detail: String
    var minimum = "Easy"
    var maximum = "Very hard"
    var body: some View {
        MiraCard(padding: 24) {
            VStack(alignment: .leading, spacing: 16) {
                Text(title).font(MiraUI.font(20, "SemiBold"))
                HStack(alignment: .firstTextBaseline, spacing: 16) {
                    Text("\(Int(value))").font(MiraUI.font(64, "Light"))
                    Text(detail).font(MiraUI.font(24)).foregroundStyle(MiraUI.soft)
                }
                Slider(value: $value, in: 0...10, step: 1).tint(MiraUI.charcoal).accessibilityLabel(title)
                HStack { Text("0 · \(minimum)"); Spacer(); Text("10 · \(maximum)") }.font(MiraUI.font(15)).foregroundStyle(MiraUI.soft)
            }
        }
    }
}
