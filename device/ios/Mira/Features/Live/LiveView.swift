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
    @State private var pulse = false
    private let onEnded: (String) -> Void
    init(initialScreen: Screen = .live, onEnded: @escaping (String) -> Void = { _ in }) {
        _screen = State(initialValue: initialScreen)
        self.onEnded = onEnded
    }
    // startedSessionID is this phone's own REST-started session and is cleared only after
    // a successful stop; prefer it so a late session_summary (or a relaunch of the socket
    // for the next session) can never substitute the wrong id for "end this session".
    private var sessionID: String? { state.startedSessionID ?? state.liveSession.currentSessionID }
    private var exerciseTitle: String {
        let live = state.liveSession.live
        return live?.exerciseName ?? live?.exerciseId ?? state.me?.prescriptions.first?.exerciseName ?? "Live session"
    }
    private var setLine: String {
        guard let live = state.liveSession.live else { return "Waiting for Mira" }
        if let set = live.set, let target = live.setsTarget { return "Set \(set) of \(target)" }
        return live.phase
    }
    private var hasFlags: Bool { !(state.liveSession.live?.flags.isEmpty ?? true) }
    private var online: Bool { preview || state.liveSession.eventSocket.state == .connected }
    private var notPaired: Bool { state.liveSession.connectionState == .notPaired || state.liveSession.connectionState == .rejected }
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
            if !online {
                Text(notPaired ? "Mira isn’t paired with this phone yet." : "Mira server offline · reconnecting…").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
            }
            if let alert = state.liveSession.alert {
                MiraCard {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Mira alert").font(MiraUI.font(20, "SemiBold"))
                        Text(alert.message ?? "\(alert.kind): \(alert.step)").font(MiraUI.font(17))
                        MiraButton(title: "I’m OK") { send(.imOK) }.disabled(!online || isSending)
                    }
                }
            }
        }.navigationBarTitleDisplayMode(.inline)
    }
    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 4) {
                Text(preview ? "Mini squats" : exerciseTitle).font(MiraUI.font(24, "SemiBold"))
                Text(preview ? "Set 2 of 3" : setLine).font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
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
                        Text("3D movement view").font(MiraUI.font(20, "SemiBold"))
                        Text("Coming soon — Mira will show your skeleton here once the camera engine is connected.")
                            .font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
                .opacity(pulse ? 0.7 : 1)
                .animation(.easeInOut(duration: 1.6).repeatForever(autoreverses: true), value: pulse)
                .overlay { RoundedRectangle(cornerRadius: 28).stroke(MiraUI.alert, lineWidth: hasFlags ? 2.5 : 0) }
                .onAppear { pulse = true }
                .accessibilityElement(children: .combine)
            }
            HStack(spacing: 12) {
                metric("Reps", preview ? "6" : state.liveSession.live.map { String($0.rep) } ?? "—", preview ? "of 10" : state.liveSession.live?.repsTarget.map { "of \($0)" } ?? "Waiting for Mira")
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
            // The server resumes a paused session on `start` (README "Engine reverse channel") and answers with `resumed`.
            MiraButton(title: "Resume") {
                if preview { screen = .live } else { send(.start) }
            }.disabled(!online || isSending)
            MiraButton(title: "Skip this exercise", secondary: true) { if !preview { send(.skip) } }.disabled(!online || isSending)
            MiraButton(title: "End session", secondary: true) { screen = .stop }
            Button("Report pain") { reportPain() }.font(MiraUI.font(17, "Medium")).disabled(!online || isSending)
        }.frame(maxWidth: .infinity)
    }
    private var painScreen: some View {
        VStack(spacing: 24) {
            MiraIntro(eyebrow: preview ? "MIRA HAS PAUSED" : "TELL MIRA", title: "How much does\nit hurt?", detail: "Move the dot to how you feel.\nThere’s no wrong answer.", size: 38)
            RatingCard(value: $pain, title: "Pain", detail: pain < 4 ? "Mild" : pain < 7 ? "Moderate" : "Severe", minimum: "No pain", maximum: "Worst pain")
            Text(preview ? "Mira will tell Dr. Lee how you’re feeling." : "Mira tells your care team how you’re feeling.")
                .font(MiraUI.font(17)).foregroundStyle(MiraUI.soft).padding(20).background(MiraUI.mist, in: RoundedRectangle(cornerRadius: 20))
            MiraButton(title: "Keep going gently") {
                if preview { screen = .live } else { sendPain(then: .live) }
            }.disabled(!preview && (!online || isSending))
            MiraButton(title: "Stop for today", secondary: true) {
                if preview { screen = .stop } else { sendPain(then: .stop) }
            }.disabled(!preview && isSending)
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
                if preview { dismiss() } else { endSession() }
            }.disabled(isSending || (!online && sessionID == nil))
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
        commandError = nil
        screen = .pain
    }
    private func sendPain(then destination: Screen) {
        guard !isSending else { return }
        isSending = true; commandError = nil
        Task {
            defer { isSending = false }
            do {
                try await state.liveSession.send(CommandMessage.pain(level: Int(pain)))
                screen = destination
            } catch {
                commandError = "Mira couldn’t send your pain level. Please try again when Mira is available."
                // Stopping must stay possible even when the pain report can't be delivered.
                if destination == .stop { screen = .stop }
            }
        }
    }
    private func endSession() {
        guard !isSending else { return }
        isSending = true; commandError = nil
        Task {
            defer { isSending = false }
            do {
                if let sid = sessionID {
                    try await state.stopSession(sid)
                    onEnded(sid)
                } else {
                    try await state.liveSession.send(.stop)
                }
            } catch {
                commandError = "Mira couldn’t end your session. Please try again when Mira is available."
            }
        }
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
