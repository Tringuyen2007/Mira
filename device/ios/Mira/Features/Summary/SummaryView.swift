import AVFoundation
import SwiftUI

struct SummaryView: View {
    enum Screen: String { case results, flags, send }
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
    init(initialScreen: Screen = .results) { _screen = State(initialValue: initialScreen) }

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
                ToolbarItem(placement: .topBarTrailing) { Text(preview ? "Moment \(selectedMoment+1) of 2" : "No moments yet").font(MiraUI.font(17, "Medium")) }
            }
        }
        .onDisappear { speech.stopSpeaking(at: .immediate) }
        .alert("Speak your note", isPresented: $dictationHelp) {
            Button("OK", role: .cancel) { }
        } message: { Text("Tap the note field, then the microphone on your iPhone keyboard to dictate. Nothing is sent until sharing is connected.") }
    }
    private var results: some View {
        VStack(spacing: 24) {
            FigmaIllustration(name: "results").padding(.horizontal, -24)
            MiraIntro(eyebrow: preview ? "SESSION DONE" : "YOUR SESSION", title: preview ? "Well done, Maya" : "Your session summary", detail: preview ? "You showed up for yourself today." : "Completed results will appear when\nMira supplies your session summary.", size: 38, centered: true)
            HStack(spacing: 12) {
                MiraCard(padding: 18) {
                    VStack(spacing: 12) {
                        Text("Form score").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                        ZStack {
                            if preview {
                                FigmaIcon(screen: "results", slot: "imgRingTrack", size: 104)
                                FigmaIcon(screen: "results", slot: "imgArc82", size: 104)
                            } else { Circle().stroke(MiraUI.mist, lineWidth: 10).frame(width: 104, height: 104) }
                            Text(preview ? "82" : "—").font(MiraUI.font(40, "Light"))
                        }
                    }.frame(maxWidth: .infinity)
                }
                MiraCard(padding: 18) {
                    VStack(spacing: 12) {
                        Text("Good reps").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                        Text(preview ? "26" : "—").font(MiraUI.font(56, "Light"))
                        Text(preview ? "of 30" : "No results yet").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                    }.frame(maxWidth: .infinity, minHeight: 133)
                }
            }
            MiraCard {
                VStack(alignment: .leading, spacing: 14) {
                    Button {
                        guard preview else { return }
                        if speech.isSpeaking { speech.stopSpeaking(at: .immediate) }
                        else { speech.speak(AVSpeechUtterance(string: previewSummary)) }
                    } label: {
                        HStack(spacing: 14) {
                            FigmaIcon(screen: "results", slot: "imgPlay", size: 52)
                            Text("Hear Mira’s summary").font(MiraUI.font(20, "SemiBold")).foregroundStyle(MiraUI.ink)
                        }
                    }.buttonStyle(.plain).disabled(!preview)
                    Text(preview ? "“\(previewSummary)”" : "A spoken summary will be available once your completed session is connected.")
                        .font(MiraUI.font(17)).foregroundStyle(MiraUI.soft).lineSpacing(4)
                }
            }
            MiraButton(title: preview ? "See 2 moments to review" : "Review session") { screen = .flags }
        }
    }
    private var previewSummary: String { "You’re bending your knee further and sharing the work more evenly. Let’s keep an eye on your right knee next time." }
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
            } else {
                MiraCard {
                    Text("Movement clips will appear when Mira provides flagged moments from your completed session.")
                        .font(MiraUI.font(20)).foregroundStyle(MiraUI.soft)
                }
            }
            MiraButton(title: "Next") { screen = .send }
        }
    }
    private var checkIn: some View {
        VStack(alignment: .leading, spacing: 24) {
            MiraIntro(eyebrow: "YOUR CHECK-IN", title: "How did it feel?", size: 36)
            RatingCard(value: $pain, title: "Pain", detail: pain < 4 ? "Mild" : pain < 7 ? "Moderate" : "Severe", minimum: "No pain", maximum: "Worst pain")
            RatingCard(value: $effort, title: "Effort", detail: effort < 4 ? "Easy" : effort < 7 ? "Moderate" : "Hard")
            VStack(alignment: .leading, spacing: 12) {
                Text(preview ? "Anything to tell Dr. Lee?" : "Anything to tell your therapist?").font(MiraUI.font(20, "SemiBold"))
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
                    Text(preview ? "Share with Dr. Lee" : "Share with your therapist").font(MiraUI.font(20, "SemiBold"))
                    Text("Only your scores and skeleton numbers. No video ever leaves your home.").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                }
            }.toggleStyle(.switch).tint(MiraUI.accent).padding(20).background(MiraUI.mist, in: RoundedRectangle(cornerRadius: 24))
            Text("Sharing isn’t connected yet. This check-in stays on this screen and won’t be sent.").font(MiraUI.font(15)).foregroundStyle(MiraUI.soft)
            MiraButton(title: "Send to therapist") {}.disabled(true)
            MiraButton(title: "Not now", secondary: true) { dismiss() }
        }
    }
}
