import SwiftUI
import Charts

struct ProgressViewScreen: View {
    @Environment(AppState.self) private var state
    @Environment(\.miraDesignPreview) private var preview
    @State private var section: String
    @State private var exercise = "Mini squats"
    @State private var monthOffset = 0
    init(initialSection: String = "Exercises") { _section = State(initialValue: initialSection) }
    var body: some View {
        MiraPage {
            MiraScreenHeading(title: "Your progress")
            MiraSegments(labels: ["Exercises", "Balance", "Goals", "Calendar"], selection: $section)
            switch section {
            case "Exercises": exercises
            case "Balance": balance
            case "Goals": goals
            default: calendar
            }
        }.navigationTitle("").toolbar(.hidden, for: .navigationBar)
        .task { if !preview { await state.refreshMe() } }
    }
    private var exercises: some View {
        VStack(spacing: 16) {
            if preview {
                MiraSegments(labels: ["Mini squats", "Heel slides", "Leg raises"], selection: $exercise)
            } else if prescriptions.count > 1 {
                MiraSegments(labels: prescriptions.map(\.exerciseName), selection: Binding(get: { selectedPrescription?.exerciseName ?? "" }, set: { exercise = $0 }))
            }
            if !preview, let prescription = selectedPrescription, let progress = state.store.progress[prescription.exerciseId], !PatientStore.kneeTrend(progress).isEmpty {
                kneeCard(prescription, trend: PatientStore.kneeTrend(progress))
            } else if preview && exercise == "Mini squats" {
                MiraCard {
                    VStack(alignment: .leading, spacing: 18) {
                        Text("Knee bend in mini squats").font(MiraUI.font(20, "SemiBold"))
                        HStack(alignment: .firstTextBaseline) { Text("84°").font(MiraUI.font(52, "Light")); Text("today").foregroundStyle(MiraUI.soft) }
                        badge("On track for 90° by Oct 28", screen: "trends", icon: "imgIconTrend")
                        Chart {
                            ForEach(Array([62.0, 66, 71, 75, 79, 84].enumerated()), id: \.offset) { index, value in
                                LineMark(x: .value("Day", index), y: .value("Knee bend", value)).foregroundStyle(MiraUI.accent)
                                PointMark(x: .value("Day", index), y: .value("Knee bend", value)).foregroundStyle(MiraUI.accent)
                            }
                            RuleMark(y: .value("Goal", 90)).lineStyle(StrokeStyle(dash: [5, 4])).foregroundStyle(MiraUI.border)
                                .annotation(position: .top, alignment: .leading) { Text("Goal 90°").font(MiraUI.font(16)) }
                            AreaMark(x: .value("Day", 5), yStart: .value("Low", 84), yEnd: .value("High", 84)).foregroundStyle(Color("MiraWelcomeSky").opacity(0.5))
                            AreaMark(x: .value("Day", 10), yStart: .value("Low", 86), yEnd: .value("High", 94)).foregroundStyle(Color("MiraWelcomeSky").opacity(0.5))
                            LineMark(x: .value("Day", 5), y: .value("Forecast", 84), series: .value("Type", "Forecast")).lineStyle(StrokeStyle(dash: [5, 4])).foregroundStyle(MiraUI.accent)
                            LineMark(x: .value("Day", 10), y: .value("Forecast", 90), series: .value("Type", "Forecast")).lineStyle(StrokeStyle(dash: [5, 4])).foregroundStyle(MiraUI.accent)
                        }.chartLegend(.hidden).chartYScale(domain: 60...100).chartXScale(domain: 0...10)
                            .chartYAxis { AxisMarks(position: .leading, values: [60, 80, 100]) { value in AxisGridLine(); AxisValueLabel { Text("\(value.as(Int.self) ?? 0)°") } } }
                            .chartXAxis { AxisMarks(values: [0, 5, 10]) { value in AxisValueLabel { Text(value.as(Int.self) == 0 ? "Sep 22" : value.as(Int.self) == 5 ? "Today" : "Oct 28") } } }
                            .frame(height: 220).accessibilityLabel("Sample knee bend trend, 62 to 84 degrees. Forecast 90 degrees.")
                        HStack { legend("You", color: MiraUI.accent); HStack(spacing: 8) { Rectangle().fill(MiraUI.accent).frame(width: 22, height: 2).overlay { Rectangle().stroke(.white, style: StrokeStyle(dash: [2, 3])) }; Text("Forecast") }; legend("Likely range", color: Color("MiraWelcomeSky")) }.font(MiraUI.font(16))
                    }
                }
            } else { MiraPendingCard(title: "Your exercise trends", detail: "Your measurements will appear here after session history is connected.") }
        }
    }
    private var balance: some View {
        Group {
            if !preview, let me, case let series = state.store.balanceSeries(for: me), !series.isEmpty {
                balanceCard(series, affectedSide: me.patient.affectedSide)
            } else if preview {
                MiraCard {
                    VStack(alignment: .leading, spacing: 20) {
                        Text("Weight on your right leg").font(MiraUI.font(20, "SemiBold"))
                        HStack(alignment: .firstTextBaseline) { Text("Even zone").font(MiraUI.font(42, "Light")); Text("this week").foregroundStyle(MiraUI.soft) }
                        badge("You are in the even zone now", screen: "balance", icon: "imgIconCheck")
                        Chart {
                            RectangleMark(xStart: .value("Start", 0), xEnd: .value("End", 3), yStart: .value("Low", 45), yEnd: .value("High", 55)).foregroundStyle(MiraUI.accent.opacity(0.14))
                            RuleMark(y: .value("Equal", 50)).lineStyle(StrokeStyle(dash: [5, 4])).foregroundStyle(MiraUI.border)
                            ForEach(Array([36.0, 40, 42, 47].enumerated()), id: \.offset) { index, value in
                                LineMark(x: .value("Week", index), y: .value("Weight", value)).foregroundStyle(MiraUI.accent)
                                PointMark(x: .value("Week", index), y: .value("Weight", value)).foregroundStyle(MiraUI.accent)
                            }
                        }.chartLegend(.hidden).chartYScale(domain: 30...60).chartXScale(domain: 0...3)
                            .chartYAxis { AxisMarks(position: .leading, values: [30, 40, 50, 60]) { value in AxisGridLine(); AxisValueLabel { Text("\(value.as(Int.self) ?? 0)%") } } }
                            .chartXAxis { AxisMarks(values: [0, 1, 2, 3]) { value in AxisValueLabel { Text(value.as(Int.self) == 3 ? "Now" : "Week \((value.as(Int.self) ?? 0)+1)") } } }
                            .frame(height: 240).accessibilityLabel("Sample right leg weight increased from 36 to 47 percent.")
                        HStack { legend("You", color: MiraUI.accent); legend("Even zone", color: MiraUI.accent.opacity(0.14)) }
                        Text("50% means both legs share the work equally. You started at 36%.").font(MiraUI.font(18)).foregroundStyle(MiraUI.soft)
                    }
                }
            } else { MiraPendingCard(title: "Weight balance", detail: "Your balance measurements will appear here when Mira provides session history.") }
        }
    }
    private var goals: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Milestones").font(MiraUI.font(24, "SemiBold"))
            if preview {
                ForEach(Array([("First full session", "Done · Sep 22"), ("7 days in a row", "Done · Oct 1"), ("Knee bend 80°", "Done · Oct 6"), ("Knee bend 90°", "Next · expected Oct 28"), ("Even weight on both legs", "Coming up")].enumerated()), id: \.offset) { index, item in
                    MiraCard(padding: 18) {
                        HStack(spacing: 16) {
                            if index < 4 { FigmaIcon(screen: "goals", slot: index < 3 ? "imgIconDone" : "imgIconNext", size: 32) }
                            else { Circle().stroke(MiraUI.border, style: StrokeStyle(dash: [4, 4])).frame(width: 32, height: 32) }
                            VStack(alignment: .leading, spacing: 4) { Text(item.0).font(MiraUI.font(20, "SemiBold")); Text(item.1).font(MiraUI.font(16)).foregroundStyle(index < 3 ? MiraUI.accent : MiraUI.soft) }
                        }
                    }.overlay { RoundedRectangle(cornerRadius: 22).stroke(index == 3 ? MiraUI.ink : index == 4 ? MiraUI.border : .clear, style: StrokeStyle(lineWidth: index == 3 ? 2 : 1, dash: index == 4 ? [5, 4] : [])) }
                }
            } else if !preview && (!doneMilestones.isEmpty || nextGoal != nil) {
                if let nextGoal {
                    MiraCard(padding: 18) {
                        HStack(spacing: 16) {
                            FigmaIcon(screen: "goals", slot: "imgIconNext", size: 32)
                            VStack(alignment: .leading, spacing: 4) { Text(nextGoal.title).font(MiraUI.font(20, "SemiBold")); Text(nextGoal.detail).font(MiraUI.font(16)).foregroundStyle(MiraUI.soft) }
                        }
                    }.overlay { RoundedRectangle(cornerRadius: 22).stroke(MiraUI.ink, lineWidth: 2) }
                }
                ForEach(doneMilestones, id: \.milestone.id) { item in
                    MiraCard(padding: 18) {
                        HStack(spacing: 16) {
                            FigmaIcon(screen: "goals", slot: "imgIconDone", size: 32)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(item.milestone.message).font(MiraUI.font(20, "SemiBold"))
                                Text(item.date.map { "Done · \($0.formatted(.dateTime.month(.abbreviated).day()))" } ?? "Done").font(MiraUI.font(16)).foregroundStyle(MiraUI.accent)
                            }
                        }
                    }
                }
            } else { MiraPendingCard(title: "Your next milestone", detail: "Link your therapist to load your goals. Completed milestones will appear here after sessions are connected.") }
            HStack(spacing: 16) { Image("MascotIdle").resizable().scaledToFit().frame(width: 52, height: 60); Text("Mira grows a new petal at every milestone.").font(MiraUI.font(17)) }.padding(16).frame(maxWidth: .infinity, alignment: .leading).background(MiraUI.mist, in: RoundedRectangle(cornerRadius: 24))
        }
    }
    private var calendar: some View {
        let calendar = Calendar(identifier: .gregorian)
        let anchor = preview ? calendar.date(from: DateComponents(year: 2026, month: 10, day: 10))! : Date()
        let month = calendar.date(byAdding: .month, value: monthOffset, to: anchor)!
        let start = calendar.date(from: calendar.dateComponents([.year, .month], from: month))!
        let offset = (calendar.component(.weekday, from: start) + 5) % 7
        let days = calendar.range(of: .day, in: .month, for: month)!.count
        let realDone = preview ? [] : PatientStore.doneDays(state.store.sessions, in: month, calendar: calendar)
        let elapsed = Self.elapsedDays(in: month, today: anchor, calendar: calendar, daysInMonth: days)
        let countText = preview ? (monthOffset == 0 ? "8 of 9" : "—") : elapsed > 0 ? "\(realDone.count) of \(elapsed)" : "—"
        return VStack(alignment: .leading, spacing: 20) {
            HStack {
                Button { monthOffset -= 1 } label: { FigmaIcon(screen: "calendar", slot: "imgIconPrevMonth") }.buttonStyle(.bordered).buttonBorderShape(.circle).controlSize(.large).accessibilityLabel("Previous month")
                Spacer(); Text(month.formatted(.dateTime.month(.wide).year())).font(MiraUI.font(22, "SemiBold")); Spacer()
                Button { monthOffset += 1 } label: { FigmaIcon(screen: "calendar", slot: "imgIconNextMonth") }.buttonStyle(.bordered).buttonBorderShape(.circle).controlSize(.large).accessibilityLabel("Next month")
            }
            HStack(alignment: .firstTextBaseline) { Text(countText).font(MiraUI.font(42, "Light")); Text(!preview && elapsed == days && monthOffset < 0 ? "days done" : "days done so far").font(MiraUI.font(18)).foregroundStyle(MiraUI.soft) }
            MiraCard(padding: 14) {
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 4), count: 7), spacing: 8) {
                    ForEach(Array(["M", "T", "W", "T", "F", "S", "S"].enumerated()), id: \.offset) { _, label in Text(label).foregroundStyle(MiraUI.soft).frame(height: 24) }
                    ForEach((0..<(offset + days)).map { "day-\($0)" }, id: \.self) { key in
                        let index = Int(key.dropFirst(4))!
                        let day = index - offset + 1
                        let done = preview ? monthOffset == 0 && [1, 2, 3, 5, 6, 7, 8, 9].contains(day) : realDone.contains(day)
                        let missed = preview ? monthOffset == 0 && day == 4 : !done && day > 0 && day < elapsed + (calendar.isDate(month, equalTo: anchor, toGranularity: .month) ? 0 : 1)
                        let today = calendar.isDate(calendar.date(byAdding: .day, value: day-1, to: start)!, inSameDayAs: anchor)
                        Text(day > 0 ? "\(day)" : "").font(MiraUI.font(18, done || today ? "SemiBold" : "Regular"))
                            .frame(maxWidth: .infinity).frame(height: 44).foregroundStyle(done ? .white : MiraUI.ink)
                            .background(done ? MiraUI.accent : .clear, in: Circle())
                            .overlay { if today { Circle().stroke(MiraUI.ink, lineWidth: 2) }; if missed { Circle().stroke(MiraUI.border, style: StrokeStyle(dash: [4, 4])) } }
                    }
                }
            }
            HStack { legend("Done", color: MiraUI.accent); legend("Today", color: MiraUI.ink); legend("Missed", color: MiraUI.border) }
            if !preview && state.store.sessions.isEmpty { Text("Your session calendar is waiting for history from Mira.").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft) }
        }
    }
    // MARK: - Real data

    private var me: MeResponse? { state.me ?? state.store.me }
    private var prescriptions: [MePrescription] { me?.prescriptions ?? [] }
    private var selectedPrescription: MePrescription? {
        prescriptions.first { $0.exerciseName == exercise } ?? prescriptions.first
    }
    private var doneMilestones: [(milestone: Milestone, date: Date?)] {
        state.store.milestones.map { ($0, WireDates.parse($0.time)) }
            .sorted { ($0.date ?? .distantPast) > ($1.date ?? .distantPast) }
    }
    /// The first knee-angle target the patient hasn't reached yet (latest `max_knee`
    /// below the target, or no measurements yet), with the forecast date if any.
    private var nextGoal: (title: String, detail: String)? {
        for prescription in prescriptions where prescription.hasKneeTarget {
            let target = prescription.targetValue!
            let trend = state.store.progress[prescription.exerciseId].map(PatientStore.kneeTrend) ?? []
            if let latest = trend.last?.1, latest >= target { continue }
            let title = "Knee bend \(Int(target.rounded()))°"
            if let forecast = PatientStore.forecast(trend, target: target), forecast.date > .now {
                return (title, "Next · expected \(forecast.date.formatted(.dateTime.month(.abbreviated).day()))")
            }
            return (title, "Next")
        }
        return nil
    }
    /// Days of `month` that have already started: all of them for past months, up to
    /// and including today for the current month, none for future months.
    static func elapsedDays(in month: Date, today: Date, calendar: Calendar, daysInMonth: Int) -> Int {
        if calendar.isDate(month, equalTo: today, toGranularity: .month) { return calendar.component(.day, from: today) }
        return month < today ? daysInMonth : 0
    }

    private func kneeCard(_ prescription: MePrescription, trend: [(Date, Double)]) -> some View {
        let target = prescription.hasKneeTarget ? prescription.targetValue : nil
        let latest = trend.last!
        let reached = target.map { latest.1 >= $0 } ?? false
        let forecast = target.flatMap { reached ? nil : PatientStore.forecast(trend, target: $0) }
        // Only draw the projection when it lands within ~3 months; further out it would flatten the chart.
        let drawnForecast = forecast.flatMap { f in f.date > latest.0 && f.date.timeIntervalSince(latest.0) < 90 * 86400 ? f : nil }
        let values = trend.map(\.1) + [target, drawnForecast.map { target! + $0.band }, drawnForecast.map { target! - $0.band }].compactMap { $0 }
        let low = (floor((values.min() ?? 0) / 10) * 10) - 10, high = (ceil((values.max() ?? 90) / 10) * 10) + 10
        let isToday = Calendar.current.isDateInToday(latest.0)
        return MiraCard {
            VStack(alignment: .leading, spacing: 18) {
                Text("Knee bend in \(prescription.exerciseName.lowercased())").font(MiraUI.font(20, "SemiBold"))
                HStack(alignment: .firstTextBaseline) { Text("\(Int(latest.1.rounded()))°").font(MiraUI.font(52, "Light")); Text(isToday ? "today" : "latest").foregroundStyle(MiraUI.soft) }
                if let target, reached {
                    badge("You reached your \(Int(target.rounded()))° goal", screen: "trends", icon: "imgIconTrend")
                } else if let target, let forecast {
                    badge("On track for \(Int(target.rounded()))° by \(forecast.date.formatted(.dateTime.month(.abbreviated).day()))", screen: "trends", icon: "imgIconTrend")
                }
                Chart {
                    ForEach(Array(trend.enumerated()), id: \.offset) { _, point in
                        LineMark(x: .value("Day", point.0), y: .value("Knee bend", point.1), series: .value("Type", "You")).foregroundStyle(MiraUI.accent)
                        PointMark(x: .value("Day", point.0), y: .value("Knee bend", point.1)).foregroundStyle(MiraUI.accent)
                    }
                    if let target {
                        RuleMark(y: .value("Goal", target)).lineStyle(StrokeStyle(dash: [5, 4])).foregroundStyle(MiraUI.border)
                            .annotation(position: .top, alignment: .leading) { Text("Goal \(Int(target.rounded()))°").font(MiraUI.font(16)) }
                    }
                    if let target, let f = drawnForecast {
                        AreaMark(x: .value("Day", latest.0), yStart: .value("Low", latest.1), yEnd: .value("High", latest.1), series: .value("Type", "Range")).foregroundStyle(Color("MiraWelcomeSky").opacity(0.5))
                        AreaMark(x: .value("Day", f.date), yStart: .value("Low", target - f.band), yEnd: .value("High", target + f.band), series: .value("Type", "Range")).foregroundStyle(Color("MiraWelcomeSky").opacity(0.5))
                        LineMark(x: .value("Day", latest.0), y: .value("Forecast", latest.1), series: .value("Type", "Forecast")).lineStyle(StrokeStyle(dash: [5, 4])).foregroundStyle(MiraUI.accent)
                        LineMark(x: .value("Day", f.date), y: .value("Forecast", target), series: .value("Type", "Forecast")).lineStyle(StrokeStyle(dash: [5, 4])).foregroundStyle(MiraUI.accent)
                    }
                }.chartLegend(.hidden).chartYScale(domain: low...high)
                    .chartYAxis { AxisMarks(position: .leading, values: .automatic(desiredCount: 3)) { value in AxisGridLine(); AxisValueLabel { Text("\(Int(value.as(Double.self) ?? 0))°") } } }
                    .chartXAxis { AxisMarks(values: .automatic(desiredCount: 3)) { _ in AxisValueLabel(format: .dateTime.month(.abbreviated).day()) } }
                    .frame(height: 220)
                    .accessibilityLabel("Knee bend trend, \(Int(trend.first!.1.rounded())) to \(Int(latest.1.rounded())) degrees.\(target.map { " Goal \(Int($0.rounded())) degrees." } ?? "")")
                HStack {
                    legend("You", color: MiraUI.accent)
                    if drawnForecast != nil {
                        HStack(spacing: 8) { Rectangle().fill(MiraUI.accent).frame(width: 22, height: 2).overlay { Rectangle().stroke(.white, style: StrokeStyle(dash: [2, 3])) }; Text("Forecast") }
                        legend("Likely range", color: Color("MiraWelcomeSky"))
                    }
                }.font(MiraUI.font(16))
            }
        }
    }

    private func balanceCard(_ series: [(Date, Double)], affectedSide: String?) -> some View {
        let latest = series.last!, first = series.first!
        let inZone = (45...55).contains(latest.1)
        let start = series.count > 1 ? first.0 : first.0.addingTimeInterval(-86400)
        let values = series.map(\.1)
        let low = min(40, floor((values.min() ?? 40) / 10) * 10), high = max(60, ceil((values.max() ?? 60) / 10) * 10)
        let side = affectedSide.map { "your \($0) leg" } ?? "your affected leg"
        return MiraCard {
            VStack(alignment: .leading, spacing: 20) {
                Text("Weight on \(side)").font(MiraUI.font(20, "SemiBold"))
                HStack(alignment: .firstTextBaseline) { Text(inZone ? "Even zone" : "\(Int(latest.1.rounded()))%").font(MiraUI.font(42, "Light")); Text("latest").foregroundStyle(MiraUI.soft) }
                if inZone { badge("You are in the even zone now", screen: "balance", icon: "imgIconCheck") }
                Chart {
                    RectangleMark(xStart: .value("Start", start), xEnd: .value("End", latest.0), yStart: .value("Low", 45), yEnd: .value("High", 55)).foregroundStyle(MiraUI.accent.opacity(0.14))
                    RuleMark(y: .value("Equal", 50)).lineStyle(StrokeStyle(dash: [5, 4])).foregroundStyle(MiraUI.border)
                    ForEach(Array(series.enumerated()), id: \.offset) { _, point in
                        LineMark(x: .value("Day", point.0), y: .value("Weight", point.1)).foregroundStyle(MiraUI.accent)
                        PointMark(x: .value("Day", point.0), y: .value("Weight", point.1)).foregroundStyle(MiraUI.accent)
                    }
                }.chartLegend(.hidden).chartYScale(domain: low...high)
                    .chartYAxis { AxisMarks(position: .leading, values: .automatic(desiredCount: 4)) { value in AxisGridLine(); AxisValueLabel { Text("\(Int(value.as(Double.self) ?? 0))%") } } }
                    .chartXAxis { AxisMarks(values: .automatic(desiredCount: 3)) { _ in AxisValueLabel(format: .dateTime.month(.abbreviated).day()) } }
                    .frame(height: 240)
                    .accessibilityLabel("Weight on \(side) went from \(Int(first.1.rounded())) to \(Int(latest.1.rounded())) percent.")
                HStack { legend("You", color: MiraUI.accent); legend("Even zone", color: MiraUI.accent.opacity(0.14)) }
                Text("50% means both legs share the work equally. You started at \(Int(first.1.rounded()))%.").font(MiraUI.font(18)).foregroundStyle(MiraUI.soft)
            }
        }
    }

    private func badge(_ text: String, screen: String, icon: String) -> some View {
        HStack(spacing: 10) { FigmaIcon(screen: screen, slot: icon, size: 26); Text(text).font(MiraUI.font(17, "Medium")) }.foregroundStyle(MiraUI.accent).padding(12).background(MiraUI.mist, in: RoundedRectangle(cornerRadius: 16))
    }
    private func legend(_ title: String, color: Color) -> some View { HStack(spacing: 8) { RoundedRectangle(cornerRadius: 3).fill(color).frame(width: 22, height: 8); Text(title) }.font(MiraUI.font(16)) }
}
