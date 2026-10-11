import SwiftUI
import Charts

struct ProgressViewScreen: View {
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
    }
    private var exercises: some View {
        VStack(spacing: 16) {
            MiraSegments(labels: ["Mini squats", "Heel slides", "Leg raises"], selection: $exercise)
            if preview && exercise == "Mini squats" {
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
            if preview {
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
        return VStack(alignment: .leading, spacing: 20) {
            HStack {
                Button { monthOffset -= 1 } label: { FigmaIcon(screen: "calendar", slot: "imgIconPrevMonth") }.buttonStyle(.bordered).buttonBorderShape(.circle).controlSize(.large).accessibilityLabel("Previous month")
                Spacer(); Text(month.formatted(.dateTime.month(.wide).year())).font(MiraUI.font(22, "SemiBold")); Spacer()
                Button { monthOffset += 1 } label: { FigmaIcon(screen: "calendar", slot: "imgIconNextMonth") }.buttonStyle(.bordered).buttonBorderShape(.circle).controlSize(.large).accessibilityLabel("Next month")
            }
            HStack(alignment: .firstTextBaseline) { Text(preview && monthOffset == 0 ? "8 of 9" : "—").font(MiraUI.font(42, "Light")); Text("days done so far").font(MiraUI.font(18)).foregroundStyle(MiraUI.soft) }
            MiraCard(padding: 14) {
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 4), count: 7), spacing: 8) {
                    ForEach(Array(["M", "T", "W", "T", "F", "S", "S"].enumerated()), id: \.offset) { _, label in Text(label).foregroundStyle(MiraUI.soft).frame(height: 24) }
                    ForEach((0..<(offset + days)).map { "day-\($0)" }, id: \.self) { key in
                        let index = Int(key.dropFirst(4))!
                        let day = index - offset + 1
                        let done = preview && monthOffset == 0 && [1, 2, 3, 5, 6, 7, 8, 9].contains(day)
                        let today = calendar.isDate(calendar.date(byAdding: .day, value: day-1, to: start)!, inSameDayAs: anchor)
                        Text(day > 0 ? "\(day)" : "").font(MiraUI.font(18, done || today ? "SemiBold" : "Regular"))
                            .frame(maxWidth: .infinity).frame(height: 44).foregroundStyle(done ? .white : MiraUI.ink)
                            .background(done ? MiraUI.accent : .clear, in: Circle())
                            .overlay { if today { Circle().stroke(MiraUI.ink, lineWidth: 2) }; if preview && monthOffset == 0 && day == 4 { Circle().stroke(MiraUI.border, style: StrokeStyle(dash: [4, 4])) } }
                    }
                }
            }
            HStack { legend("Done", color: MiraUI.accent); legend("Today", color: MiraUI.ink); legend("Missed", color: MiraUI.border) }
            if !preview { Text("Your session calendar is waiting for history from Mira.").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft) }
        }
    }
    private func badge(_ text: String, screen: String, icon: String) -> some View {
        HStack(spacing: 10) { FigmaIcon(screen: screen, slot: icon, size: 26); Text(text).font(MiraUI.font(17, "Medium")) }.foregroundStyle(MiraUI.accent).padding(12).background(MiraUI.mist, in: RoundedRectangle(cornerRadius: 16))
    }
    private func legend(_ title: String, color: Color) -> some View { HStack(spacing: 8) { RoundedRectangle(cornerRadius: 3).fill(color).frame(width: 22, height: 8); Text(title) }.font(MiraUI.font(16)) }
}
