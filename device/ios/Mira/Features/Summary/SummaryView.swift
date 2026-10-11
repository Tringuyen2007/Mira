import AVFoundation
import SwiftUI

struct SummaryView: View {
    enum Screen: String { case results, flags, send }
    @Environment(AppState.self) private var state
    @Environment(\.miraDesignPreview) private var preview
    @Environment(\.dismiss) private var dismiss
    @State private var screen: Screen
    @State private var pain = 3.0
    @State private var effort = 6.0
    @State private var note = ""
    @State private var sharing = false
    @State private var selectedMoment = 0
    @State private var dictationHelp = false
    @State private var speech = AVSpeechSynthesizer()
    // Live data (sessionID != nil, outside the design preview).
    private let sessionID: String?
    @State private var detail: SessionDetail?
    @State private var loadError: String?
    @State private var reviewTimedOut = false
    @State private var player: AVPlayer?
    @State private var sharingDefaulted = false
    @State private var isSending = false
    @State private var sendError: String?

    /// Design preview / placeholder only.
    init(initialScreen: Screen = .results) {
        sessionID = nil
        _screen = State(initialValue: initialScreen)
    }
    /// A real session's summary: loads GET /sessions/{id} and waits for Mira's analysis.
    init(sessionID: String, initialScreen: Screen = .results) {
        self.sessionID = sessionID
        _screen = State(initialValue: initialScreen)
    }

    /// The analysis to show: the stored one, else the `session_summary` event for this session.
    private var shown: Shown? {
        guard !preview, let sessionID else { return nil }
        if let a = detail?.analysis {
            return Shown(formScore: a.formScore, goodReps: a.goodReps, repsTotal: a.repsTotal, spoken: a.spoken,
                         highlights: a.highlights, flaggedMoments: a.flaggedMoments, audioUrl: a.audioUrl)
        }
        if let m = state.liveSession.sessionSummary, m.sessionId == sessionID {
            return Shown(formScore: m.formScore, goodReps: m.goodReps, repsTotal: m.repsTotal, spoken: m.spoken,
                         highlights: m.highlights, flaggedMoments: m.flaggedMoments, audioUrl: m.audioUrl)
        }
        return nil
    }
    private var isLive: Bool { !preview && sessionID != nil }
    private var moments: [FlaggedMoment] { shown?.flaggedMoments ?? [] }
    private var therapistName: String? { state.me?.therapist?.name }

    var body: some View {
        MiraPage(top: 0) {
            switch screen {
            case .results: results
            case .flags: flags
            case .send: checkIn
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if screen != .results {
                ToolbarItem(placement: .topBarLeading) { Button("Back") { screen = screen == .send ? .flags : .results } }
            }
            if screen == .flags {
                ToolbarItem(placement: .topBarTrailing) { Text(momentCounter).font(MiraUI.font(17, "Medium")) }
            }
        }
        .task(id: sessionID) { await pollForAnalysis() }
        .onChange(of: state.liveSession.sessionSummary?.sessionId) { _, newID in
            guard isLive, let newID, newID == sessionID else { return }
            Task { await reload() }
        }
        .onAppear {
            guard isLive, !sharingDefaulted else { return }
            sharingDefaulted = true
            sharing = state.me?.patient.shareByDefault ?? false
        }
        .onDisappear {
            speech.stopSpeaking(at: .immediate)
            player?.pause()
        }
        .alert("Speak your note", isPresented: $dictationHelp) {
            Button("OK", role: .cancel) { }
        } message: {
            Text(isLive ? "Tap the note field, then the microphone on your iPhone keyboard to dictate."
                 : "Tap the note field, then the microphone on your iPhone keyboard to dictate. Nothing is sent until sharing is connected.")
        }
    }

    private var momentCounter: String {
        if preview { return "Moment \(selectedMoment+1) of 2" }
        return moments.isEmpty ? "No moments yet" : "Moment \(min(selectedMoment, moments.count - 1) + 1) of \(moments.count)"
    }

    // MARK: Results

    private var results: some View {
        VStack(spacing: 24) {
            FigmaIllustration(name: "results").padding(.horizontal, -24)
            MiraIntro(eyebrow: preview ? "SESSION DONE" : (shown != nil ? "SESSION DONE" : "YOUR SESSION"),
                      title: preview ? "Well done, Maya" : resultsTitle,
                      detail: preview ? "You showed up for yourself today." : resultsDetail, size: 38, centered: true)
            HStack(spacing: 12) {
                MiraCard(padding: 18) {
                    VStack(spacing: 12) {
                        Text("Form score").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                        ZStack {
                            if preview {
                                FigmaIcon(screen: "results", slot: "imgRingTrack", size: 104)
                                FigmaIcon(screen: "results", slot: "imgArc82", size: 104)
                            } else {
                                Circle().stroke(MiraUI.mist, lineWidth: 10).frame(width: 104, height: 104)
                                if let score = shown?.formScore {
                                    Circle().trim(from: 0, to: CGFloat(min(max(score, 0), 100)) / 100)
                                        .stroke(MiraUI.accent, style: StrokeStyle(lineWidth: 10, lineCap: .round))
                                        .rotationEffect(.degrees(-90)).frame(width: 104, height: 104)
                                        .animation(.easeOut(duration: 0.8), value: score)
                                }
                            }
                            Text(preview ? "82" : shown?.formScore.map(String.init) ?? "—").font(MiraUI.font(40, "Light"))
                        }
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel(preview ? "Form score 82" : shown?.formScore.map { "Form score \($0) of 100" } ?? "Form score not ready")
                    }.frame(maxWidth: .infinity)
                }
                MiraCard(padding: 18) {
                    VStack(spacing: 12) {
                        Text("Good reps").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                        Text(preview ? "26" : goodReps.map(String.init) ?? "—").font(MiraUI.font(56, "Light"))
                        Text(preview ? "of 30" : repsTotal.map { "of \($0)" } ?? "No results yet").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                    }.frame(maxWidth: .infinity, minHeight: 133)
                }
            }
            if isLive { reviewStatus }
            if let highlights = shown?.highlights, !highlights.isEmpty {
                MiraCard {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Highlights").font(MiraUI.font(20, "SemiBold"))
                        ForEach(Array(highlights.enumerated()), id: \.offset) { _, highlight in
                            HStack(alignment: .firstTextBaseline, spacing: 10) {
                                Image(systemName: "checkmark.circle")
                                    .foregroundStyle(MiraUI.accent)
                                Text(highlight.text).font(MiraUI.font(17)).foregroundStyle(MiraUI.ink)
                            }
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            MiraCard {
                VStack(alignment: .leading, spacing: 14) {
                    Button {
                        if preview {
                            if speech.isSpeaking { speech.stopSpeaking(at: .immediate) }
                            else { speech.speak(AVSpeechUtterance(string: previewSummary)) }
                        } else { playSummary() }
                    } label: {
                        HStack(spacing: 14) {
                            FigmaIcon(screen: "results", slot: "imgPlay", size: 52)
                            Text("Hear Mira’s summary").font(MiraUI.font(20, "SemiBold")).foregroundStyle(MiraUI.ink)
                        }
                    }.buttonStyle(.plain).disabled(!preview && shown == nil)
                    Text(preview ? "“\(previewSummary)”" : shown.map { "“\($0.spoken)”" } ?? "A spoken summary will be available once your completed session is connected.")
                        .font(MiraUI.font(17)).foregroundStyle(MiraUI.soft).lineSpacing(4)
                }
            }
            if let loadError { Text(loadError).font(MiraUI.font(15)).foregroundStyle(MiraUI.alert) }
            MiraButton(title: preview ? "See 2 moments to review" : reviewButtonTitle) { screen = .flags }
        }
    }
    private var resultsTitle: String {
        guard shown != nil else { return "Your session summary" }
        if let name = state.me?.patient.firstName { return "Well done, \(name)" }
        return "Well done"
    }
    private var resultsDetail: String {
        guard isLive else { return "Completed results will appear when\nMira supplies your session summary." }
        return detail?.exerciseName ?? (shown != nil ? "You showed up for yourself today." : "Your session has ended.")
    }
    private var goodReps: Int? { shown?.goodReps ?? detail?.summary?.goodReps }
    private var repsTotal: Int? { shown?.repsTotal ?? detail?.summary?.repsTotal }
    private var reviewButtonTitle: String {
        guard isLive, shown != nil else { return "Review session" }
        switch moments.count {
        case 0: return "Review session"
        case 1: return "See 1 moment to review"
        default: return "See \(moments.count) moments to review"
        }
    }
    @ViewBuilder private var reviewStatus: some View {
        if shown == nil {
            if !reviewTimedOut {
                HStack(spacing: 12) {
                    ProgressView()
                    Text("Mira is reviewing your session…").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                }.frame(maxWidth: .infinity).accessibilityElement(children: .combine)
            } else if let aiSummary = detail?.summary?.aiSummary, !aiSummary.isEmpty {
                MiraCard {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Mira’s last set summary").font(MiraUI.font(15, "Medium")).foregroundStyle(MiraUI.soft)
                        Text(aiSummary).font(MiraUI.font(17)).foregroundStyle(MiraUI.ink)
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
            } else {
                Text("Mira’s review isn’t ready yet. Check back in a moment.").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
            }
        }
    }
    private var previewSummary: String { "You’re bending your knee further and sharing the work more evenly. Let’s keep an eye on your right knee next time." }

    // MARK: Flags

    private var flags: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Moments to review").font(MiraUI.font(34, "Light")).foregroundStyle(MiraUI.title)
            if preview {
                FigmaIllustration(name: "flags").background(.white.opacity(0.5), in: RoundedRectangle(cornerRadius: 28))
                Text("Sample movement snapshot").font(MiraUI.font(15)).foregroundStyle(MiraUI.soft)
                HStack(spacing: 12) {
                    MiraButton(title: "Replay", secondary: true) {}.disabled(true)
                    MiraButton(title: "Slow", secondary: true) {}.disabled(true)
                }
                ForEach(0..<2) { index in
                    Button { selectedMoment = index } label: {
                        MiraCard(padding: 16) {
                            HStack(spacing: 12) {
                                FigmaIcon(screen: "flags", slot: "imgIconWarning", size: 28)
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(index == 0 ? "Leaned onto your left leg" : "Right knee moved inward").font(MiraUI.font(17, "SemiBold"))
                                    Text(index == 0 ? "Set 3 · Rep 7" : "Set 3 · Rep 9").font(MiraUI.font(15)).foregroundStyle(MiraUI.soft)
                                }
                                Spacer(); FigmaIcon(screen: "flags", slot: "imgIconChevron", size: 18)
                            }
                        }.overlay { RoundedRectangle(cornerRadius: 28).stroke(selectedMoment == index ? MiraUI.accent : .clear, lineWidth: 1.5) }
                    }.buttonStyle(.plain)
                }
            } else if !moments.isEmpty {
                MiraCard {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("3D movement view").font(MiraUI.font(20, "SemiBold"))
                        Text("Coming soon — Mira will replay your skeleton for each moment here.")
                            .font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
                HStack(spacing: 12) {
                    MiraButton(title: "Replay", secondary: true) {}.disabled(true)
                    MiraButton(title: "Slow", secondary: true) {}.disabled(true)
                }
                Text("3D replay coming soon").font(MiraUI.font(15)).foregroundStyle(MiraUI.soft)
                ForEach(Array(moments.enumerated()), id: \.offset) { index, moment in
                    Button { selectedMoment = index } label: {
                        MiraCard(padding: 16) {
                            HStack(spacing: 12) {
                                FigmaIcon(screen: "flags", slot: "imgIconWarning", size: 28)
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(moment.label ?? moment.ruleId).font(MiraUI.font(17, "SemiBold"))
                                    Text(momentPosition(moment)).font(MiraUI.font(15)).foregroundStyle(MiraUI.soft)
                                }
                                Spacer(); FigmaIcon(screen: "flags", slot: "imgIconChevron", size: 18)
                            }
                        }.overlay { RoundedRectangle(cornerRadius: 28).stroke(selectedMoment == index ? MiraUI.accent : .clear, lineWidth: 1.5) }
                    }.buttonStyle(.plain)
                }
            } else {
                MiraCard {
                    Text(isLive && shown != nil ? "No moments to review this time. Nice, steady work."
                         : "Movement clips will appear when Mira provides flagged moments from your completed session.")
                        .font(MiraUI.font(20)).foregroundStyle(MiraUI.soft)
                }
            }
            MiraButton(title: "Next") { screen = .send }
        }
    }
    private func momentPosition(_ moment: FlaggedMoment) -> String {
        switch (moment.set, moment.rep) {
        case let (set?, rep?): return "Set \(set) · Rep \(rep)"
        case let (set?, nil): return "Set \(set)"
        case let (nil, rep?): return "Rep \(rep)"
        default: return "During your session"
        }
    }

    // MARK: Check-in

    private var checkIn: some View {
        VStack(alignment: .leading, spacing: 24) {
            MiraIntro(eyebrow: "YOUR CHECK-IN", title: "How did it feel?", size: 36)
            RatingCard(value: $pain, title: "Pain", detail: pain < 4 ? "Mild" : pain < 7 ? "Moderate" : "Severe", minimum: "No pain", maximum: "Worst pain")
            RatingCard(value: $effort, title: "Effort", detail: effort < 4 ? "Easy" : effort < 7 ? "Moderate" : "Hard")
            VStack(alignment: .leading, spacing: 12) {
                Text(preview ? "Anything to tell Dr. Lee?" : "Anything to tell \(therapistName ?? "your therapist")?").font(MiraUI.font(20, "SemiBold"))
                TextEditor(text: $note).font(MiraUI.font(17)).scrollContentBackground(.hidden)
                    .frame(minHeight: 100).padding(12).background(.white, in: RoundedRectangle(cornerRadius: 20))
                    .overlay { RoundedRectangle(cornerRadius: 20).stroke(MiraUI.border, lineWidth: 1.5) }.accessibilityLabel("Note for therapist")
                Button { dictationHelp = true } label: {
                    HStack { FigmaIcon(screen: "send", slot: "imgIconMic", size: 24); Text("Speak instead").font(MiraUI.font(19, "Medium")) }
                        .frame(maxWidth: .infinity, minHeight: 60).foregroundStyle(MiraUI.ink).background(.white, in: Capsule()).overlay { Capsule().stroke(MiraUI.border, lineWidth: 1.5) }
                }.buttonStyle(.plain)
            }
            Toggle(isOn: $sharing) {
                VStack(alignment: .leading, spacing: 8) {
                    Text(preview ? "Share with Dr. Lee" : "Share with \(therapistName ?? "your therapist")").font(MiraUI.font(20, "SemiBold"))
                    Text("Only your scores and skeleton numbers. No video ever leaves your home.").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                }
            }.toggleStyle(.switch).tint(MiraUI.accent).padding(20).background(MiraUI.mist, in: RoundedRectangle(cornerRadius: 24))
            if !isLive {
                Text("Sharing isn’t connected yet. This check-in stays on this screen and won’t be sent.").font(MiraUI.font(15)).foregroundStyle(MiraUI.soft)
            }
            if let sendError { Text(sendError).font(MiraUI.font(15)).foregroundStyle(MiraUI.alert) }
            MiraButton(title: isSending ? "Sending…" : "Send to therapist") { sendCheckin() }.disabled(!isLive || isSending)
            MiraButton(title: "Not now", secondary: true) { dismiss() }
        }
    }

    // MARK: Actions

    private func pollForAnalysis() async {
        guard isLive else { return }
        let deadline = Date.now.addingTimeInterval(60)
        while !Task.isCancelled {
            await reload()
            if detail?.analysis != nil { return }
            if Date.now >= deadline { reviewTimedOut = true; return }
            try? await Task.sleep(for: .seconds(3))
        }
    }
    private func reload() async {
        guard let sessionID else { return }
        do {
            detail = try await state.loadSession(sessionID)
            loadError = nil
        } catch {
            loadError = "Mira couldn’t load this session. \(error.localizedDescription)"
        }
    }
    private func playSummary() {
        guard let shown else { return }
        if let player, player.rate > 0 { player.pause(); return }
        if speech.isSpeaking { speech.stopSpeaking(at: .immediate); return }
        if let path = shown.audioUrl, let url = state.serverURL(for: path) {
            try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio)
            let item = AVPlayer(url: url)
            player = item
            item.play()
        } else {
            speech.speak(AVSpeechUtterance(string: shown.spoken))
        }
    }
    private func sendCheckin() {
        guard isLive, let sessionID, !isSending else { return }
        isSending = true; sendError = nil
        Task {
            defer { isSending = false }
            do {
                try await state.checkin(sessionID, pain: Int(pain), effort: Int(effort), note: note)
            } catch {
                sendError = "Mira couldn’t send your check-in. \(error.localizedDescription)"
                return
            }
            if sharing {
                do { try await state.share(sessionID) } catch {
                    sendError = "Your check-in was saved, but sharing failed. \(error.localizedDescription)"
                    return
                }
            }
            dismiss()
        }
    }
}

/// The parts of a session analysis the Summary screen shows (stored or just delivered over the socket).
private struct Shown {
    let formScore: Int?
    let goodReps: Int?
    let repsTotal: Int?
    let spoken: String
    let highlights: [Highlight]
    let flaggedMoments: [FlaggedMoment]
    let audioUrl: String?
}
