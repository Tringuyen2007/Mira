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
                    Text(preview ? "Good morning,\nMaya" : greeting)
                        .font(MiraUI.font(30, "Light")).foregroundStyle(MiraUI.title)
                    Text(preview ? "5 days in a row. Keep it up!" : streak >= 2 ? "\(streak) days in a row. Keep it up!" : "Let’s take your next step.")
                        .font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                }.frame(width: 230, alignment: .leading)
            }
            NavigationLink { WeekView() } label: {
                MiraCard {
                    VStack(alignment: .leading, spacing: 18) {
                        HStack {
                            Text("This week").font(MiraUI.font(20, "SemiBold")); Spacer()
                            Text(preview ? "5-day streak" : streak >= 2 ? "\(streak)-day streak" : "Your progress").font(MiraUI.font(15, "Medium"))
                            FigmaIcon(screen: "home", slot: "imgIconChevronRight", size: 16)
                        }
                        HStack(spacing: 8) {
                            ForEach(Array(["M", "T", "W", "T", "F", "S", "S"].enumerated()), id: \.offset) { index, day in
                                VStack(spacing: 8) {
                                    Text(day).font(MiraUI.font(13, "Medium")).foregroundStyle(MiraUI.soft)
                                    if preview && index < 6 {
                                        FigmaIcon(screen: "home", slot: index < 5 ? "imgDone" : "imgToday", size: 36)
                                    } else if !preview && weekDots[index] {
                                        FigmaIcon(screen: "home", slot: "imgDone", size: 36)
                                    } else if !preview && index == todayIndex {
                                        FigmaIcon(screen: "home", slot: "imgToday", size: 36)
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
                        } else if !prescriptions.isEmpty {
                            FigmaIcon(screen: "home", slot: "imgIconClock", size: 20)
                            Text("About \(Self.estimatedMinutes(prescriptions)) min").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
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
                    } else if !prescriptions.isEmpty {
                        ForEach(Array(prescriptions.enumerated()), id: \.element.id) { index, prescription in
                            HStack(spacing: 14) {
                                Text("\(index + 1)").font(MiraUI.font(17, "Medium")).frame(width: 32, height: 32).background(MiraUI.mist, in: Circle())
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(prescription.exerciseName).font(MiraUI.font(19, "SemiBold"))
                                    if let dose = prescription.doseText {
                                        Text(dose).font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                                    }
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
                    Image(systemName: "hand.raised").font(.system(size: 30)).foregroundStyle(MiraUI.soft).frame(width: 36, height: 36).accessibilityHidden(true)
                    Text("Or raise your hand to Mira when\nyou’re standing on your mat.").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                }.padding(.horizontal, 16)
            } else {
                if state.isLinked {
                    HStack(spacing: 14) {
                        Image(systemName: "hand.raised").font(.system(size: 30)).foregroundStyle(MiraUI.soft).frame(width: 36, height: 36).accessibilityHidden(true)
                        Text("Or raise your hand to Mira when\nyou’re standing on your mat.").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                    }.padding(.horizontal, 16)
                }
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
        .task { if !preview { await state.refreshMe() } }
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
    private var me: MeResponse? { state.me ?? state.store.me }
    private var prescriptions: [MePrescription] { me?.prescriptions ?? [] }
    private var streak: Int { PatientStore.streakDays(state.store.sessions, calendar: .current, today: .now) }
    private var weekDots: [Bool] { PatientStore.weekDots(state.store.sessions, calendar: .current, today: .now) }
    /// Mon = 0 … Sun = 6, matching `weekDots`.
    private var todayIndex: Int { (Calendar.current.component(.weekday, from: .now) + 5) % 7 }
    private var greeting: String {
        guard let name = me?.patient.firstName, !name.isEmpty else { return "Welcome\nto Mira" }
        let hour = Calendar.current.component(.hour, from: .now)
        let part = hour < 12 ? "morning" : hour < 17 ? "afternoon" : "evening"
        return "Good \(part),\n\(name)"
    }
    /// About 4 s per rep plus 30 s of rest per set, never less than 5 minutes.
    static func estimatedMinutes(_ prescriptions: [MePrescription]) -> Int {
        let seconds = prescriptions.reduce(0) { total, p in total + (p.sets ?? 1) * ((p.reps ?? 10) * 4 + 30) }
        return max(5, Int((Double(seconds) / 60).rounded()))
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
    @Environment(AppState.self) private var state
    @Environment(\.miraDesignPreview) private var preview
    var body: some View {
        MiraPage {
            MiraIntro(eyebrow: "YOUR PROGRESS", title: "This week", size: 34)
            MiraCard {
                VStack(alignment: .leading, spacing: 14) {
                    if preview {
                        Text("You’re bending further").font(MiraUI.font(20, "SemiBold"))
                        HStack { FigmaIcon(screen: "week", slot: "imgIconArrowUp", size: 40); Text("+8°").font(MiraUI.font(52, "Light")).foregroundStyle(MiraUI.accent) }
                        Text("More knee bend than last week.").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                    } else if let delta = kneeDelta {
                        let degrees = Int(delta.value.rounded())
                        Text(degrees > 0 ? "You’re bending further" : "Your knee bend").font(MiraUI.font(20, "SemiBold"))
                        HStack {
                            if degrees > 0 { FigmaIcon(screen: "week", slot: "imgIconArrowUp", size: 40) }
                            Text("\(degrees > 0 ? "+" : "")\(degrees)°").font(MiraUI.font(52, "Light")).foregroundStyle(MiraUI.accent)
                        }
                        Text(kneeDeltaCopy(degrees, vsLastWeek: delta.vsLastWeek)).font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                    } else {
                        Text("You’re bending further").font(MiraUI.font(20, "SemiBold"))
                        unavailable("Your knee-bend progress will appear after completed sessions.")
                    }
                }
            }
            MiraCard {
                VStack(alignment: .leading, spacing: 18) {
                    if preview {
                        Text("Your balance is more even").font(MiraUI.font(20, "SemiBold"))
                        balance("Last week", left: 58, improved: false)
                        balance("This week", left: 53, improved: true)
                        HStack { FigmaIcon(screen: "week", slot: "imgIconCheckCircle", size: 24); Text("You’re sharing the work more evenly.").font(MiraUI.font(17)).foregroundStyle(MiraUI.accent) }
                    } else if let pair = balancePair {
                        let improved = abs(pair.last.1 - 50) < abs(pair.first.1 - 50)
                        let even = abs(pair.last.1 - 50) <= 5
                        Text(improved || even ? "Your balance is more even" : "Your balance").font(MiraUI.font(20, "SemiBold"))
                        balance(pair.first.0.formatted(.dateTime.month(.abbreviated).day()), left: Int((100 - pair.first.1).rounded()), improved: false)
                        balance(pair.last.0.formatted(.dateTime.month(.abbreviated).day()), left: Int((100 - pair.last.1).rounded()), improved: true)
                        if improved || even {
                            HStack { FigmaIcon(screen: "week", slot: "imgIconCheckCircle", size: 24); Text(even ? "You’re sharing the work evenly." : "You’re sharing the work more evenly.").font(MiraUI.font(17)).foregroundStyle(MiraUI.accent) }
                        } else {
                            unavailable("Keep going — Mira will help you share the work more evenly.")
                        }
                    } else {
                        Text("Your balance is more even").font(MiraUI.font(20, "SemiBold"))
                        unavailable("Balance trends need session history from Mira.")
                    }
                }
            }
            Text("YOUR SETUP").font(MiraUI.font(17, "Medium")).tracking(3).foregroundStyle(MiraUI.accent)
            setupRow("Cameras", preview ? "Both working" : camerasStatus.text, good: preview || camerasStatus.good, camera: true)
            setupRow("Mira", preview ? "Online" : miraStatus.text, good: preview || miraStatus.good, camera: false)
        }.navigationTitle("Today").navigationBarTitleDisplayMode(.inline)
        .task { if !preview { await state.refreshMe() } }
    }

    private var me: MeResponse? { state.me ?? state.store.me }
    private var camerasStatus: (text: String, good: Bool) {
        me?.calibration?.calibrated == true ? ("Both working", true) : ("Not confirmed", false)
    }
    private var miraStatus: (text: String, good: Bool) {
        guard let online = state.liveSession.lcdOnline ?? me?.lcd?.online else { return ("Not confirmed", false) }
        return online ? ("Online", true) : ("Offline", false)
    }
    /// Change in the deepest knee bend: the latest value in the last 7 days against the
    /// latest value in the 7 days before that; without a previous week, against the
    /// first value of this week. `nil` when there aren't two points to compare.
    private var kneeDelta: (value: Double, vsLastWeek: Bool)? {
        guard let prescription = me?.prescriptions.kneePrescription,
              let progress = state.store.progress[prescription.exerciseId] else { return nil }
        let calendar = Calendar.current
        let today = calendar.startOfDay(for: .now)
        guard let weekStart = calendar.date(byAdding: .day, value: -6, to: today),
              let previousStart = calendar.date(byAdding: .day, value: -13, to: today) else { return nil }
        let trend = PatientStore.kneeTrend(progress)
        let thisWeek = trend.filter { $0.0 >= weekStart }
        let previousWeek = trend.filter { $0.0 >= previousStart && $0.0 < weekStart }
        guard let latest = thisWeek.last else { return nil }
        if let baseline = previousWeek.last { return (latest.1 - baseline.1, true) }
        guard thisWeek.count >= 2, let first = thisWeek.first else { return nil }
        return (latest.1 - first.1, false)
    }
    private func kneeDeltaCopy(_ degrees: Int, vsLastWeek: Bool) -> String {
        let period = vsLastWeek ? "than last week" : "than at the start of the week"
        if degrees > 0 { return "More knee bend \(period)." }
        if degrees < 0 { return "A little less knee bend \(period). That’s okay — keep going." }
        return "About the same knee bend \(period)."
    }
    /// First and last balance points (percent of load on the affected leg) of the last 14 days.
    private var balancePair: (first: (Date, Double), last: (Date, Double))? {
        guard let me else { return nil }
        let cutoff = Calendar.current.date(byAdding: .day, value: -13, to: Calendar.current.startOfDay(for: .now)) ?? .distantPast
        let series = state.store.balanceSeries(for: me).filter { $0.0 >= cutoff }
        guard series.count >= 2, let first = series.first, let last = series.last else { return nil }
        return (first, last)
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
    private func setupRow(_ title: String, _ value: String, good: Bool, camera: Bool) -> some View {
        HStack(spacing: 12) {
            if camera { FigmaIcon(screen: "week", slot: "imgIconCamera", size: 28) }
            Text(title).font(MiraUI.font(19, "Medium")); Spacer()
            Text(value).font(MiraUI.font(17)).foregroundStyle(good ? MiraUI.accent : MiraUI.soft)
        }.padding(18).background(.white, in: RoundedRectangle(cornerRadius: 20))
    }
}

// MARK: - Derived patient data shared by Home, Week and Progress

extension MePrescription {
    /// "3 sets of 10", or whichever half is known.
    var doseText: String? {
        switch (sets, reps) {
        case let (sets?, reps?): return "\(sets) sets of \(reps)"
        case let (nil, reps?): return "\(reps) reps"
        case let (sets?, nil): return "\(sets) sets"
        default: return nil
        }
    }
    /// The numeric target named by `targetKey` (e.g. `targets.knee_deg = 90`).
    var targetValue: Double? {
        guard let targetKey, case .object(let targets)? = targets, case .number(let value)? = targets[targetKey] else { return nil }
        return value
    }
    /// Whether `targetValue` is a knee-bend angle, i.e. comparable to `max_knee`.
    var hasKneeTarget: Bool { targetKey == "knee_deg" && targetValue != nil }
}

extension Array where Element == MePrescription {
    /// The prescription whose progress drives knee-bend summaries: the first with a
    /// knee-angle target, else the first prescription.
    var kneePrescription: MePrescription? { first(where: \.hasKneeTarget) ?? first }
}

extension PatientStore {
    /// Balance series (percent of load on the affected leg) for the first prescription,
    /// knee-target first, that has balance data; else any exercise's progress that does.
    func balanceSeries(for me: MeResponse) -> [(Date, Double)] {
        var ordered = me.prescriptions.filter(\.hasKneeTarget) + me.prescriptions.filter { !$0.hasKneeTarget }
        ordered.removeAll { progress[$0.exerciseId] == nil }
        for prescription in ordered {
            let series = Self.balanceSeries(progress[prescription.exerciseId]!, affectedSide: me.patient.affectedSide)
            if !series.isEmpty { return series }
        }
        for key in progress.keys.sorted() {
            let series = Self.balanceSeries(progress[key]!, affectedSide: me.patient.affectedSide)
            if !series.isEmpty { return series }
        }
        return []
    }
}
