import SwiftUI

struct HomeView: View {
    @Environment(AppState.self) private var state
    @Environment(\.miraDesignPreview) private var preview
    @State private var showingSetup = false
    @State private var flow: FullScreenFlow?
    @State private var actionMessage: String?
    @State private var isWorking = false
    private enum FullScreenFlow: Identifiable {
        case live, summary(sessionID: String)
        var id: String {
            switch self {
            case .live: return "live"
            case .summary(let sessionID): return "summary-\(sessionID)"
            }
        }
    }
    var body: some View {
        MiraPage(top: 0) {
            ZStack(alignment: .trailing) {
                FigmaIllustration(name: "home").padding(.horizontal, -24)
                VStack(alignment: .leading, spacing: 8) {
                    Text(Date.now.formatted(.dateTime.weekday(.wide)).uppercased())
                        .font(MiraUI.font(17, "Medium")).tracking(3).foregroundStyle(MiraUI.accent)
                    Text(preview ? "Good morning,\nMaya" : "Welcome\nto Mira")
                        .font(MiraUI.font(30, "Light")).foregroundStyle(MiraUI.title)
                    Text(preview ? "5 days in a row. Keep it up!" : "Let’s take your next step.")
                        .font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                }.frame(width: 230, alignment: .leading)
            }
            NavigationLink { WeekView() } label: {
                MiraCard {
                    VStack(alignment: .leading, spacing: 18) {
                        HStack {
                            Text("This week").font(MiraUI.font(20, "SemiBold")); Spacer()
                            Text(preview ? "5-day streak" : "Your progress").font(MiraUI.font(15, "Medium"))
                            FigmaIcon(screen: "home", slot: "imgIconChevronRight", size: 16)
                        }
                        HStack(spacing: 8) {
                            ForEach(Array(["M", "T", "W", "T", "F", "S", "S"].enumerated()), id: \.offset) { index, day in
                                VStack(spacing: 8) {
                                    Text(day).font(MiraUI.font(13, "Medium")).foregroundStyle(MiraUI.soft)
                                    if preview && index < 6 {
                                        FigmaIcon(screen: "home", slot: index < 5 ? "imgDone" : "imgToday", size: 36)
                                    } else {
                                        Circle().stroke(MiraUI.border.opacity(0.5), style: StrokeStyle(lineWidth: 1.5, dash: [3,3])).frame(height: 36)
                                    }
                                }.frame(maxWidth: .infinity)
                            }
                        }
                    }
                }
            }.buttonStyle(.plain)
            MiraCard {
                VStack(alignment: .leading, spacing: 18) {
                    HStack {
                        Text("Today’s plan").font(MiraUI.font(20, "SemiBold")); Spacer()
                        if preview {
                            FigmaIcon(screen: "home", slot: "imgIconClock", size: 20)
                            Text("About 15 min").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                        }
                    }
                    if preview {
                        ForEach(Array([("Mini squats", "3 sets of 10"), ("Heel slides", "2 sets of 12"), ("Leg raises", "2 sets of 10")].enumerated()), id: \.offset) { index, exercise in
                            HStack(spacing: 14) {
                                Text("\(index + 1)").font(MiraUI.font(17, "Medium")).frame(width: 32, height: 32).background(MiraUI.mist, in: Circle())
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(exercise.0).font(MiraUI.font(19, "SemiBold"))
                                    Text(exercise.1).font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                                }
                            }
                        }
                    } else {
                        Text("Your exercises and targets will appear when your therapist is linked.").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                        Button("Complete your setup") { state.revisitOnboarding(at: .therapist) }.font(MiraUI.font(17, "Medium"))
                    }
                }
            }
            MiraButton(title: preview || state.isLinked ? "Start today’s session" : "Open live session") { start() }
                .disabled(isWorking)
            if preview {
                HStack(spacing: 14) {
                    FigmaIcon(screen: "home", slot: "imgIconThumbsUp", size: 36)
                    Text("Or give Mira a thumbs-up when\nyou’re standing on your mat.").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                }.padding(.horizontal, 16)
            } else {
                HStack {
                    Button("Set up your space") { showingSetup = true }
                    Spacer()
                    Button("Session summary") { openSummary() }.disabled(isWorking)
                }.font(MiraUI.font(15, "Medium"))
                if let actionMessage {
                    Text(actionMessage).font(MiraUI.font(15)).foregroundStyle(MiraUI.soft)
                }
                if state.connectionEnabled && !state.isPaired {
                    Text("Mira isn’t paired with this phone yet.").font(MiraUI.font(15)).foregroundStyle(MiraUI.soft)
                } else if state.connectionEnabled && state.liveSession.connectionState != .connected {
                    Text("Mira is unavailable right now.").font(MiraUI.font(15)).foregroundStyle(MiraUI.soft)
                }
            }
        }
        .navigationTitle("")
        .sheet(isPresented: $showingSetup) {
            NavigationStack { SetupView().toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { showingSetup = false } } } }
        }
        .fullScreenCover(item: $flow) { destination in
            NavigationStack {
                Group {
                    switch destination {
                    case .live: LiveView(onEnded: { sid in flow = .summary(sessionID: sid) })
                    case .summary(let sessionID): SummaryView(sessionID: sessionID)
                    }
                }.toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { flow = nil } } }
            }
        }
    }
    private func start() {
        guard !preview, state.isLinked else { flow = .live; return }
        guard !isWorking else { return }
        isWorking = true; actionMessage = nil
        Task {
            defer { isWorking = false }
            do {
                try await state.startSession(exerciseID: nil)
                flow = .live
            } catch {
                actionMessage = "Mira couldn’t start your session. \(error.localizedDescription)"
            }
        }
    }
    /// Always opens an ended session — /patients/{id}/sessions already filters to ended
    /// ones — and never the one currently in progress, even if that live session's id is
    /// known locally.
    private func openSummary() {
        guard state.isLinked else { actionMessage = "No sessions yet"; return }
        guard !isWorking else { return }
        isWorking = true; actionMessage = nil
        let liveSessionRunning = state.startedSessionID != nil || state.liveSession.live != nil
        Task {
            defer { isWorking = false }
            do {
                if let sid = try await state.latestEndedSessionID() {
                    flow = .summary(sessionID: sid)
                } else if liveSessionRunning {
                    actionMessage = "Your session is still running — finish it to see the summary."
                } else {
                    actionMessage = "No sessions yet"
                }
            } catch {
                actionMessage = "Mira couldn’t load your sessions. \(error.localizedDescription)"
            }
        }
    }
}

struct WeekView: View {
    @Environment(\.miraDesignPreview) private var preview
    var body: some View {
        MiraPage {
            MiraIntro(eyebrow: "YOUR PROGRESS", title: "This week", size: 34)
            MiraCard {
                VStack(alignment: .leading, spacing: 14) {
                    Text("You’re bending further").font(MiraUI.font(20, "SemiBold"))
                    if preview {
                        HStack { FigmaIcon(screen: "week", slot: "imgIconArrowUp", size: 40); Text("+8°").font(MiraUI.font(52, "Light")).foregroundStyle(MiraUI.accent) }
                        Text("More knee bend than last week.").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                    } else { unavailable("Your knee-bend progress will appear after completed sessions.") }
                }
            }
            MiraCard {
                VStack(alignment: .leading, spacing: 18) {
                    Text("Your balance is more even").font(MiraUI.font(20, "SemiBold"))
                    if preview {
                        balance("Last week", left: 58, improved: false)
                        balance("This week", left: 53, improved: true)
                        HStack { FigmaIcon(screen: "week", slot: "imgIconCheckCircle", size: 24); Text("You’re sharing the work more evenly.").font(MiraUI.font(17)).foregroundStyle(MiraUI.accent) }
                    } else { unavailable("Balance trends need session history from Mira.") }
                }
            }
            Text("YOUR SETUP").font(MiraUI.font(17, "Medium")).tracking(3).foregroundStyle(MiraUI.accent)
            setupRow("Cameras", preview ? "Both working" : "Not confirmed", camera: true)
            setupRow("Mira", preview ? "Online" : "Not confirmed", camera: false)
        }.navigationTitle("Today").navigationBarTitleDisplayMode(.inline)
    }
    private func unavailable(_ text: String) -> some View { Text(text).font(MiraUI.font(17)).foregroundStyle(MiraUI.soft) }
    private func balance(_ title: String, left: Int, improved: Bool) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
            HStack(spacing: 2) {
                Text("\(left)%").frame(maxWidth: .infinity).frame(height: 32).background(improved ? MiraUI.charcoal : MiraUI.border).foregroundStyle(.white)
                Text("\(100-left)%").frame(maxWidth: .infinity).frame(height: 32).background(improved ? MiraUI.accent : MiraUI.mist).foregroundStyle(improved ? .white : MiraUI.ink)
            }.font(MiraUI.font(15, "SemiBold")).clipShape(Capsule())
        }
    }
    private func setupRow(_ title: String, _ value: String, camera: Bool) -> some View {
        HStack(spacing: 12) {
            if camera { FigmaIcon(screen: "week", slot: "imgIconCamera", size: 28) }
            Text(title).font(MiraUI.font(19, "Medium")); Spacer()
            Text(value).font(MiraUI.font(17)).foregroundStyle(preview ? MiraUI.accent : MiraUI.soft)
        }.padding(18).background(.white, in: RoundedRectangle(cornerRadius: 20))
    }
}
