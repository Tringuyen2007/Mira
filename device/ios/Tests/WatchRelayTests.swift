import XCTest
@testable import MiraNetworking

final class WatchRelayTests: XCTestCase {
    private func fixture(_ name: String) throws -> Data {
        let url = try XCTUnwrap(Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Fixtures"),
                                "Missing fixture \(name).json")
        return try Data(contentsOf: url)
    }

    private func live(phase: String = "active", exerciseId: String? = "squat", rep: Int = 3,
                      countdown: Int? = nil, caption: String? = nil, restRemaining: Int? = nil) -> LiveMessage {
        LiveMessage(t: 1, phase: phase, exerciseId: exerciseId, exerciseName: "Mini squats", rep: rep, repsTarget: 10,
                    set: 1, setsTarget: 3, countdown: countdown, mainAngle: nil, loadLPct: nil, loadConf: nil, hr: 98,
                    flags: [], cueId: nil, paused: false,
                    caption: caption.map { LiveCaption(text: $0, source: "cue", t: 1) }, restRemaining: restRemaining)
    }

    // MARK: map

    func testFixFormRepThenCueBecomesFormCue() {
        var r = WatchRelay()
        XCTAssertTrue(r.map(.rep(RepMessage(index: 4, set: 1, exerciseId: "squat", quality: "fix_form", flags: ["knee_valgus"]))).isEmpty)
        XCTAssertEqual(r.map(.cue(CueMessage(id: "knees_out", text: "Push your knees out", audioUrl: nil))),
                       [.caption(text: "Push your knees out"), .formCue(text: "Push your knees out", rep: 4)])
        XCTAssertEqual(r.map(.cue(CueMessage(id: "set_start", text: "Here we go", audioUrl: nil))), [.caption(text: "Here we go")])
    }

    func testFixFormIsForgottenOnSetChange() {
        var r = WatchRelay()
        _ = r.map(.rep(RepMessage(index: 4, set: 1, exerciseId: "squat", quality: "fix_form", flags: [])))
        _ = r.map(.countdown(CountdownMessage(n: 3, set: 2, repsTarget: 10, setsTarget: 3, exerciseId: "squat")))
        XCTAssertEqual(r.map(.cue(CueMessage(id: "set_start", text: "Here we go", audioUrl: nil))), [.caption(text: "Here we go")])
    }

    func testElbowDriftIsAlwaysAFormCue() {
        var r = WatchRelay()
        let out = r.map(.cue(CueMessage(id: "elbow_drift", text: "Keep your elbow at your side", audioUrl: nil)))
        XCTAssertTrue(out.contains(.formCue(text: "Keep your elbow at your side", rep: nil)))
        XCTAssertTrue(out.contains(.caption(text: "Keep your elbow at your side")))
    }

    func testElbowDriftCarriesLastRotationRep() throws {
        var r = WatchRelay()
        let rotation = try WireJSON.decoder().decode(SocketEvent.self, from: fixture("ws_events_rotation_rep"))
        XCTAssertEqual(r.map(rotation), [.rotationRep(exerciseId: "forearm_rotation", rep: 4, repsTarget: 10,
                                                      angle: 68, goal: 80, reached: false)])
        let out = r.map(.cue(CueMessage(id: "elbow_drift", text: "Keep your elbow at your side", audioUrl: nil)))
        XCTAssertEqual(out.filter { if case .formCue = $0 { return true }; return false },
                       [.formCue(text: "Keep your elbow at your side", rep: 4)])
    }

    func testGoodRepsCountAndResetOnCountdown() {
        var r = WatchRelay()
        _ = r.map(.rep(RepMessage(index: 1, set: 1, exerciseId: "squat", quality: "good", flags: [])))
        _ = r.map(.rep(RepMessage(index: 2, set: 1, exerciseId: "squat", quality: "fix_form", flags: [])))
        XCTAssertEqual(r.goodReps, 1)
        _ = r.map(.countdown(CountdownMessage(n: 3, set: 2, repsTarget: 10, setsTarget: 3, exerciseId: "squat")))
        XCTAssertEqual(r.goodReps, 0)
    }

    func testGoodRepsResetOnSetChange() {
        var r = WatchRelay()
        _ = r.map(.rep(RepMessage(index: 1, set: 1, exerciseId: "squat", quality: "good", flags: [])))
        _ = r.map(.rep(RepMessage(index: 2, set: 1, exerciseId: "squat", quality: "good", flags: [])))
        _ = r.map(.rep(RepMessage(index: 1, set: 2, exerciseId: "squat", quality: "good", flags: [])))
        XCTAssertEqual(r.goodReps, 1)
    }

    func testServerEventsMap() throws {
        var r = WatchRelay()
        let rec = try WireJSON.decoder().decode(SocketEvent.self, from: fixture("ws_events_hr_recovery"))
        XCTAssertEqual(r.map(rec), [.recovery(setIndex: 2, peakBpm: 126, bpm60: 104, drop: 22)])
        let sync = try WireJSON.decoder().decode(SocketEvent.self, from: fixture("ws_events_sync_progress"))
        XCTAssertEqual(r.map(sync), [.sync(progress: 0.6, locked: false)])
        let breather = try WireJSON.decoder().decode(SocketEvent.self, from: fixture("ws_events_breather"))
        XCTAssertEqual(r.map(breather), [.breather(state: "start", bpm: 128, limit: 120)])
        let set = try WireJSON.decoder().decode(SocketEvent.self, from: fixture("ws_events_rotation_set"))
        XCTAssertEqual(r.map(set), [.rotationSet(exerciseId: "forearm_rotation", setIndex: 1, angles: [62, 70.5, 78, 82, 75],
                                                 best: 82, avg: 73.5, goal: 80)])
        XCTAssertEqual(r.map(.haptic(HapticMessage(pattern: "good_rep"))), [.haptic(pattern: "good_rep")])
        XCTAssertEqual(r.map(.greeting(GreetingMessage(text: "Hi Maya", audioUrl: nil))), [.caption(text: "Hi Maya")])
        let limits = try WireJSON.decoder().decode(SocketEvent.self, from: fixture("ws_events_limits_changed"))
        XCTAssertTrue(r.map(limits).isEmpty)
        XCTAssertTrue(r.map(.unknown(type: "future_thing", body: .object([:]))).isEmpty)
    }

    func testSetupRefreshNeeded() throws {
        let limits = try WireJSON.decoder().decode(SocketEvent.self, from: fixture("ws_events_limits_changed"))
        let features = try WireJSON.decoder().decode(SocketEvent.self, from: fixture("ws_events_watch_features_changed"))
        XCTAssertTrue(WatchRelay.setupRefreshNeeded(limits))
        XCTAssertTrue(WatchRelay.setupRefreshNeeded(features))
        XCTAssertFalse(WatchRelay.setupRefreshNeeded(.haptic(HapticMessage(pattern: "good_rep"))))
    }

    // MARK: setup

    func testSetupFromMeWatchAppliesDefaults() throws {
        let me = try WireJSON.decoder().decode(MeWatchResponse.self, from: fixture("rest_get_me_watch"))
        let s = WatchRelay.setup(me)
        XCTAssertEqual(s.firstName, "Maya")
        XCTAssertEqual(s.hrLimit, 120)
        XCTAssertEqual(s.hrLimitSetBy, "Dr. Lee")
        XCTAssertEqual(s.prefs.taps, "strong")
        XCTAssertEqual(s.plan.count, 2)
        XCTAssertEqual(s.plan[1].id, "forearm_rotation")
        XCTAssertEqual(s.plan[1].goalDeg, 80)
        XCTAssertTrue(s.plan[1].watch)
        XCTAssertFalse(s.plan[0].watch)

        let bare = MeWatchResponse(firstName: "Maya", hrLimit: nil, hrLimitSetByName: nil, therapistName: nil,
                                   tremorMonitoring: false, prefs: MeWatchPrefs(), plan: [])
        XCTAssertEqual(WatchRelay.setup(bare).prefs, WatchWire.WatchPrefs.defaults)
        let partial = MeWatchResponse(firstName: "Maya", hrLimit: nil, hrLimitSetByName: nil, therapistName: nil,
                                      tremorMonitoring: false, prefs: MeWatchPrefs(watchArm: "left"), plan: [])
        XCTAssertEqual(WatchRelay.setup(partial).prefs,
                       WatchWire.WatchPrefs(taps: "normal", arm: "left", doubleTap: true, captions: true))
    }

    // MARK: snapshot + captions

    func testSnapshotMarksWatchExercise() {
        let plan = [WatchWire.PlanItem(id: "forearm_rotation", name: "Forearm turns", sets: 3, reps: 10, doneSets: 0, watch: true, goalDeg: 80),
                    WatchWire.PlanItem(id: "squat", name: "Mini squats", sets: 3, reps: 12, doneSets: 0, watch: false, goalDeg: nil)]
        var r = WatchRelay()
        _ = r.map(.rep(RepMessage(index: 1, set: 1, exerciseId: "forearm_rotation", quality: "good", flags: [])))
        let s = r.snapshot(live(exerciseId: "forearm_rotation", countdown: 2, restRemaining: 30), plan: plan)
        XCTAssertEqual(s.isWatchExercise, true)
        XCTAssertEqual(s.goodReps, 1)
        XCTAssertEqual(s.exerciseId, "forearm_rotation")
        XCTAssertEqual(s.countdown, 2)
        XCTAssertEqual(s.restRemaining, 30)
        XCTAssertEqual(s.rep, 3)
        XCTAssertEqual(s.hr, 98)
        XCTAssertEqual(r.snapshot(live(exerciseId: "squat"), plan: plan).isWatchExercise, false)
        XCTAssertEqual(r.snapshot(live(exerciseId: "squat"), plan: []).isWatchExercise, false)
        XCTAssertEqual(r.snapshot(nil, plan: plan), .idle)
    }

    func testLiveCaptionOncePerDistinctText() {
        var r = WatchRelay()
        XCTAssertNil(r.caption(from: nil))
        XCTAssertNil(r.caption(from: live()))
        XCTAssertNil(r.caption(from: live(caption: "")))
        XCTAssertEqual(r.caption(from: live(caption: "Nice and slow")), .caption(text: "Nice and slow"))
        XCTAssertNil(r.caption(from: live(caption: "Nice and slow")))
        XCTAssertEqual(r.caption(from: live(caption: "Last one")), .caption(text: "Last one"))
    }

    func testCueCaptionIsNotRepeatedByLiveCaption() {
        var r = WatchRelay()
        _ = r.map(.cue(CueMessage(id: "knees_out", text: "Push your knees out", audioUrl: nil)))
        XCTAssertNil(r.caption(from: live(caption: "Push your knees out")))
    }

    // MARK: watch → phone

    func testWristGateIs10Hz() {
        var g = RateGate(minInterval: 0.1)
        let kept = stride(from: 0.0, to: 1.0, by: 0.02).filter { g.allow($0) }
        XCTAssertEqual(kept.count, 10)
    }

    func testWristGateRecoversWhenClockGoesBackwards() {
        var g = RateGate(minInterval: 0.1)
        XCTAssertTrue(g.allow(100))
        XCTAssertTrue(g.allow(5))
        XCTAssertFalse(g.allow(5.05))
    }

    func testWatchToSocket() throws {
        XCTAssertEqual(WatchRelay.socketEvent(.button(name: "resume"))?.type, "watch_button")
        XCTAssertEqual(WatchRelay.socketEvent(.syncWindow(t0: 1, dtMs: 20, mags: [0.1]))?.type, "watch_sync")
        XCTAssertNil(WatchRelay.socketEvent(.setupDone))
        XCTAssertNil(WatchRelay.socketEvent(.prefs(taps: "strong", arm: nil, doubleTap: nil, captions: nil)))

        let wrist = try XCTUnwrap(WatchRelay.socketEvent(.wrist(t: 1791662259.706, quat: [0.99, 0.01, 0.05, 0.02],
                                                                 accel: [0.02, -9.79, 0.15], deg: 41.5)))
        let wristJSON = try JSONSerialization.jsonObject(with: WireJSON.encoder().encode(wrist)) as? [String: Any]
        XCTAssertEqual(wristJSON?["type"] as? String, "wrist")
        XCTAssertEqual(wristJSON?["rotation_deg"] as? Double, 41.5)

        let sync = try XCTUnwrap(WatchRelay.socketEvent(.syncWindow(t0: 1791662259, dtMs: 20, mags: [0.1, 0.4])))
        let syncJSON = try JSONSerialization.jsonObject(with: WireJSON.encoder().encode(sync)) as? [String: Any]
        XCTAssertEqual(syncJSON?["dt_ms"] as? Int, 20)
        XCTAssertEqual(syncJSON?["accel_mag"] as? [Double], [0.1, 0.4])
    }

    func testPrefsMergeKeepsOtherKeys() {
        let merged = WatchRelay.mergedDevicePrefs(.object(["voice": .string("calm")]), taps: "strong", arm: nil, doubleTap: false, captions: nil)
        XCTAssertEqual(merged, .object(["voice": .string("calm"), "watch_taps": .string("strong"), "watch_double_tap": .bool(false)]))
        XCTAssertEqual(WatchRelay.mergedDevicePrefs(nil, taps: nil, arm: "left", doubleTap: nil, captions: true),
                       .object(["watch_arm": .string("left"), "watch_captions": .bool(true)]))
        XCTAssertEqual(WatchRelay.mergedDevicePrefs(.object(["watch_arm": .string("right"), "volume": .number(0.7)]),
                                                    taps: nil, arm: "left", doubleTap: nil, captions: nil),
                       .object(["watch_arm": .string("left"), "volume": .number(0.7)]))
    }

    func testTremorUploadKeepsLast14Days() throws {
        let days = (1...20).reversed().map { d in
            WatchWire.TremorDay(day: String(format: "2026-10-%02d", d), noneMin: 420, slightMin: 190, mildMin: 25,
                                strongMin: 0, unknownMin: 60, dyskinesia: "few", source: "simulated")
        }
        let upload = WatchRelay.tremorUpload(days)
        XCTAssertEqual(upload.days.count, 14)
        XCTAssertEqual(upload.days.first?.day, "2026-10-07")
        XCTAssertEqual(upload.days.last?.day, "2026-10-20")
        XCTAssertEqual(upload.days.last?.noneMin, 420)

        let body = try JSONSerialization.jsonObject(with: WireJSON.encoder().encode(WatchRelay.tremorUpload(Array(days.prefix(1)))))
        let first = try XCTUnwrap(((body as? [String: Any])?["days"] as? [[String: Any]])?.first)
        XCTAssertEqual(first["none_min"] as? Int, 420)
        XCTAssertEqual(first["dyskinesia"] as? String, "few")
    }
}
