import Foundation

/// One second of accelerometer magnitude for the server's ClockSync (`watch_sync {t0, dt_ms, accel_mag}`).
struct SyncWindow: Equatable {
    var t0: Double
    var dtMs: Int
    var mags: [Double]
}

/// Buffers magnitudes and emits one window per second, on the sample that crosses the 1 s mark.
/// That sample starts the next window. At most `maxSamples` magnitudes per window (extra samples are dropped).
struct SyncWindowBuilder {
    let dtMs: Int
    let maxSamples: Int
    static let windowSeconds = 1.0

    private var t0: Double?
    private var mags: [Double] = []

    init(dtMs: Int = 20, maxSamples: Int = 50) {
        self.dtMs = dtMs
        self.maxSamples = maxSamples
    }

    mutating func add(t: Double, mag: Double) -> SyncWindow? {
        guard let start = t0 else {
            t0 = t; mags = [mag]
            return nil
        }
        if t - start >= Self.windowSeconds - 1e-9 {
            let window = SyncWindow(t0: start, dtMs: dtMs, mags: mags)
            t0 = t; mags = [mag]
            return window
        }
        if mags.count < maxSamples { mags.append(mag) }
        return nil
    }

    mutating func reset() { t0 = nil; mags = [] }
}
