import Foundation

enum TremorLevel: String, CaseIterable { case none, slight, mild, strong, unknown }

struct TremorInterval {
    var start: Date
    var end: Date
    var level: TremorLevel
}

/// One day for the Tremor Today screen and the `POST /me/tremor` upload.
struct TremorDaySummary: Equatable {
    var day: String
    var minutes: [TremorLevel: Int]
    var dyskinesia: String
    var source: String
    var headline: TremorLevel            // highest of slight/mild/strong with ≥ 10 min, else .none
    var headlineText: String             // "Slight for 3 h 10 min" | "None today"
    var extraMovementsText: String       // "Extra movements: few"
}

enum TremorSummarizer {
    static let headlineMinimumMinutes = 10

    static func summarize(day: String, intervals: [TremorInterval], dyskineticPercent: Double?,
                          source: String) -> TremorDaySummary {
        var seconds: [TremorLevel: Double] = [:]
        for i in intervals where i.end > i.start {
            seconds[i.level, default: 0] += i.end.timeIntervalSince(i.start)
        }
        var minutes: [TremorLevel: Int] = [:]
        for level in TremorLevel.allCases {
            minutes[level] = Int(((seconds[level] ?? 0) / 60).rounded())
        }
        let headline = [TremorLevel.strong, .mild, .slight].first { (minutes[$0] ?? 0) >= headlineMinimumMinutes } ?? .none
        let headlineText = headline == .none
            ? "None today"
            : "\(headline.rawValue.capitalized) for \(durationText(minutes[headline] ?? 0))"
        let bucket = dyskinesiaBucket(dyskineticPercent)
        return TremorDaySummary(day: day, minutes: minutes, dyskinesia: bucket, source: source, headline: headline,
                                headlineText: headlineText, extraMovementsText: "Extra movements: \(bucket)")
    }

    /// nil / < 1 % none, < 10 % few, < 25 % some, else many.
    static func dyskinesiaBucket(_ pct: Double?) -> String {
        guard let pct, pct >= 1 else { return "none" }
        if pct < 10 { return "few" }
        if pct < 25 { return "some" }
        return "many"
    }

    /// "25 min", "2 h", "3 h 10 min".
    static func durationText(_ minutes: Int) -> String {
        let h = minutes / 60, m = minutes % 60
        if h == 0 { return "\(m) min" }
        return m == 0 ? "\(h) h" : "\(h) h \(m) min"
    }
}

/// Deterministic stand-in for CMMovementDisorderManager (used unless `MIRA_TREMOR_REAL`).
/// Waking hours only (07:00–22:00 local), 10-minute blocks, mostly none/slight with some mild and rare strong.
enum SimulatedTremor {
    static let blockMinutes = 10
    static let wakeHour = 7
    static let sleepHour = 22

    static func intervals(for day: Date, seed: UInt64) -> [TremorInterval] {
        let cal = Calendar.current
        let start = cal.startOfDay(for: day)
        var rng = SplitMix64(seed: seed ^ UInt64(bitPattern: Int64(start.timeIntervalSince1970)))
        var out: [TremorInterval] = []
        let blocks = (sleepHour - wakeHour) * 60 / blockMinutes
        for b in 0..<blocks {
            guard let s = cal.date(byAdding: .minute, value: wakeHour * 60 + b * blockMinutes, to: start),
                  let e = cal.date(byAdding: .minute, value: blockMinutes, to: s) else { continue }
            let roll = rng.next() % 100
            let level: TremorLevel
            switch roll {
            case 0..<55: level = .none
            case 55..<80: level = .slight
            case 80..<92: level = .mild
            case 92..<96: level = .strong
            default: level = .unknown
            }
            out.append(TremorInterval(start: s, end: e, level: level))
        }
        return out
    }
}

/// Small seeded PRNG so simulated days are reproducible across launches.
struct SplitMix64 {
    private var state: UInt64
    init(seed: UInt64) { state = seed }
    mutating func next() -> UInt64 {
        state &+= 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }
}
