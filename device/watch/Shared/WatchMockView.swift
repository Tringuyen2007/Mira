import SwiftUI

enum WatchMockPhase: String, CaseIterable, Identifiable {
    case ready, live, paused, finished, stopped
    var id: String { rawValue }
    var label: String { rawValue.capitalized }
}

// Deliberately local demo state: no HealthKit, phone connection, or server commands.
struct WatchMockView: View {
    @State private var phase: WatchMockPhase
    @State private var reps = 0
    @State private var confirmsStop = false
    private let targetReps = 8

    init(initialPhase: WatchMockPhase = .ready) {
        _phase = State(initialValue: initialPhase)
        _reps = State(initialValue: initialPhase == .finished ? 8 : initialPhase == .ready ? 0 : 3)
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                HStack {
                    Text("MIRA").font(font(12, "Medium")).tracking(2.5)
                        .foregroundStyle(WatchMockStyle.mint)
                    Spacer()
                    Text("DEMO").font(font(9, "Medium")).tracking(1)
                        .foregroundStyle(WatchMockStyle.pink)
                }
                switch phase {
                case .ready: ready
                case .live: live
                case .paused: paused
                case .finished, .stopped: summary
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
        }
        .id(phase)
        .background(Color.black)
        .foregroundStyle(.white)
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if phase == .live || phase == .paused {
                stopButton
                    .padding(.horizontal, 12)
                    .padding(.vertical, 4)
                    .background(Color.black)
            }
        }
        .confirmationDialog("Stop this demo set?", isPresented: $confirmsStop, titleVisibility: .visible) {
            Button("Stop demo", role: .destructive) { phase = .stopped }
            Button("Keep going", role: .cancel) {}
        }
    }

    private var ready: some View {
        VStack(spacing: 9) {
            Image("WatchMascot")
                .resizable().scaledToFit().frame(height: 70)
                .accessibilityHidden(true)
            Text("A little progress,\none rep at a time.")
                .font(font(17, "Medium")).multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
            Text("Sample plan · Squats\n1 set · 8 reps")
                .font(font(12)).foregroundStyle(WatchMockStyle.secondary)
                .multilineTextAlignment(.center)
            action("I'm ready", symbol: "checkmark", color: WatchMockStyle.mint) {
                reps = 0
                phase = .live
            }
            Text("Start a practice set")
                .font(font(10)).foregroundStyle(WatchMockStyle.secondary)
        }
    }

    private var live: some View {
        VStack(spacing: 10) {
            Text("Squats").font(font(18, "Medium"))
            ZStack {
                Circle().stroke(WatchMockStyle.card, lineWidth: 7)
                Circle().trim(from: 0, to: CGFloat(reps) / CGFloat(targetReps))
                    .stroke(WatchMockStyle.mint, style: StrokeStyle(lineWidth: 7, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                VStack(spacing: 0) {
                    Text("\(reps)").font(font(38, "SemiBold")).monospacedDigit()
                    Text("of \(targetReps) reps").font(font(11))
                        .foregroundStyle(WatchMockStyle.secondary)
                }
            }
            .frame(width: 106, height: 106)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(reps) of \(targetReps) sample repetitions")
            heartRate
            Text(reps == 0 ? "Take your time" : "Nice work. Keep it steady.")
                .font(font(12, "Medium")).foregroundStyle(WatchMockStyle.mint)
                .multilineTextAlignment(.center)
            action("Demo rep", symbol: "plus", color: WatchMockStyle.mint) {
                reps += 1
                if reps == targetReps { phase = .finished }
            }
            action("Pause", symbol: "pause.fill", color: WatchMockStyle.card, foreground: .white) {
                phase = .paused
            }
        }
    }

    private var paused: some View {
        VStack(spacing: 12) {
            Image(systemName: "pause.circle.fill")
                .font(.system(size: 38)).foregroundStyle(WatchMockStyle.pink)
                .accessibilityHidden(true)
            Text("Take a breather").font(font(18, "Medium"))
                .multilineTextAlignment(.center)
            Text("Your demo set is paused.\n\(reps) of \(targetReps) reps")
                .font(font(12)).foregroundStyle(WatchMockStyle.secondary)
                .multilineTextAlignment(.center)
            heartRate
            action("Resume", symbol: "play.fill", color: WatchMockStyle.mint) { phase = .live }
        }
    }

    private var summary: some View {
        VStack(spacing: 12) {
            Image(systemName: phase == .finished ? "checkmark.circle.fill" : "stop.circle.fill")
                .font(.system(size: 38)).foregroundStyle(WatchMockStyle.mint)
                .accessibilityHidden(true)
            Text(phase == .finished ? "Set complete!" : "Set stopped")
                .font(font(20, "Medium")).multilineTextAlignment(.center)
            Text("\(reps) / \(targetReps)").font(font(32, "SemiBold")).monospacedDigit()
            Text("Sample repetitions")
                .font(font(12)).foregroundStyle(WatchMockStyle.secondary)
            Text(phase == .finished ? "Every little step counts." : "Rest when you need to.")
                .font(font(12)).multilineTextAlignment(.center)
            action("Back to Mira", symbol: "arrow.uturn.backward", color: WatchMockStyle.mint) {
                reps = 0
                phase = .ready
            }
        }
    }

    private var heartRate: some View {
        HStack(spacing: 7) {
            Image(systemName: "heart.fill").foregroundStyle(WatchMockStyle.pink)
                .accessibilityHidden(true)
            Text("84").font(font(18, "SemiBold")).monospacedDigit()
            VStack(alignment: .leading, spacing: 0) {
                Text("BPM").font(font(10, "Medium"))
                Text("Sample").font(font(9)).foregroundStyle(WatchMockStyle.secondary)
            }
        }
        .frame(maxWidth: .infinity).padding(.vertical, 8)
        .background(WatchMockStyle.card, in: RoundedRectangle(cornerRadius: 16))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Sample heart rate, 84 beats per minute")
    }

    private var stopButton: some View {
        Button {
            guard phase == .live || phase == .paused else { return }
            confirmsStop = true
        } label: {
            Label("Stop", systemImage: "stop.fill")
                .font(font(14, "Medium"))
                .frame(maxWidth: .infinity, minHeight: 44)
                .foregroundStyle(WatchMockStyle.pink)
                .background(WatchMockStyle.card, in: Capsule())
        }
        .buttonStyle(.plain)
        .simultaneousGesture(LongPressGesture(minimumDuration: 1).onEnded { _ in
            confirmsStop = false
            phase = .stopped
        })
        .accessibilityHint("Tap to confirm or hold for one second to stop the demo")
    }

    private func action(_ title: String, symbol: String, color: Color, foreground: Color = .black, perform: @escaping () -> Void) -> some View {
        Button(action: perform) {
            Label(title, systemImage: symbol)
                .font(font(14, "Medium"))
                .frame(maxWidth: .infinity, minHeight: 44)
                .foregroundStyle(foreground)
                .background(color, in: Capsule())
        }
        .buttonStyle(.plain)
    }

    private func font(_ size: CGFloat, _ weight: String = "Regular") -> Font {
        .custom("Inter-\(weight)", size: size, relativeTo: .body)
    }
}

enum WatchMockStyle {
    static let mint = Color(red: 0.56, green: 0.85, blue: 0.73)
    static let pink = Color(red: 0.94, green: 0.68, blue: 0.81)
    static let card = Color(red: 0.10, green: 0.13, blue: 0.12)
    static let secondary = Color(red: 0.69, green: 0.73, blue: 0.71)
}

#Preview("Ready") { WatchMockView() }
#Preview("Exercise") { WatchMockView(initialPhase: .live) }
#Preview("Paused") { WatchMockView(initialPhase: .paused) }
#Preview("Finished") { WatchMockView(initialPhase: .finished) }
