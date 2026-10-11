import XCTest
@testable import MiraWatchCore

final class WatchFlowReducerTests: XCTestCase {
    private typealias Entry = WatchFlowState.PlanEntry

    private func doneSetup(plan: [Entry] = [], tremor: Bool = false) -> WatchFlowReducer {
        var r = WatchFlowReducer()
        r.reduce(.setup(firstName: "Maya", hrLimit: 120, hrLimitSetBy: "Dr. Lee", therapist: "Dr. Lee", tremor: tremor,
                        prefs: .init(), plan: plan, setupDone: true), now: 0)
        return r
    }

    private func snap(phase: String, rep: Int = 0, goodReps: Int? = nil, countdown: Int? = nil,
                      exerciseId: String? = nil, watch: Bool = false) -> WatchFlowInput {
        .snapshot(phase: phase, rep: rep, repsTarget: 10, set: 1, setsTarget: 3, goodReps: goodReps, countdown: countdown,
                  restRemaining: phase == "rest" ? 60 : nil, exerciseId: exerciseId, isWatchExercise: watch, hr: 98,
                  paused: false)
    }

    func testSetupWalksFiveScreensThenReady() {
        var r = WatchFlowReducer()
        r.reduce(.setup(firstName: "Maya", hrLimit: 120, hrLimitSetBy: "Dr. Lee", therapist: "Dr. Lee", tremor: false,
                        prefs: .init(), plan: [], setupDone: false), now: 0)
        XCTAssertEqual(r.route(now: 0), .paired)
        XCTAssertEqual(r.state.firstName, "Maya")
        r.reduce(.next, now: 0);                XCTAssertEqual(r.route(now: 0), .heartRate)
        r.reduce(.heartRateAnswered, now: 0);   XCTAssertEqual(r.route(now: 0), .whichArm)
        r.reduce(.armChosen("left"), now: 0);   XCTAssertEqual(r.route(now: 0), .syncWave); XCTAssertEqual(r.state.prefs.arm, "left")
        r.reduce(.sync(progress: 0.5, locked: false), now: 0); XCTAssertEqual(r.route(now: 0), .syncWave)
        XCTAssertEqual(r.state.syncProgress, 0.5)
        r.reduce(.sync(progress: 1, locked: true), now: 0);    XCTAssertEqual(r.route(now: 0), .learnTaps)
        r.reduce(.tapsLearned, now: 0);         XCTAssertEqual(r.route(now: 0), .ready)
        XCTAssertNil(r.state.setupStep)
    }

    func testSetupContextRepushDoesNotRestartSetup() {
        var r = WatchFlowReducer()
        r.reduce(.setup(firstName: "Maya", hrLimit: nil, hrLimitSetBy: nil, therapist: nil, tremor: false,
                        prefs: .init(), plan: [], setupDone: false), now: 0)
        r.reduce(.next, now: 0)
        r.reduce(.setup(firstName: "Maya", hrLimit: 130, hrLimitSetBy: "Dr. Lee", therapist: nil, tremor: false,
                        prefs: .init(), plan: [], setupDone: false), now: 1)
        XCTAssertEqual(r.route(now: 1), .heartRate)
        XCTAssertEqual(r.state.hrLimit, 130)
    }

    func testSetupDoneSkipsSetup() {
        let r = doneSetup()
        XCTAssertNil(r.state.setupStep)
        XCTAssertEqual(r.route(now: 0), .ready)
    }

    func testCountdownLiveFixFormSetDoneRecovery() {
        var r = doneSetup()
        r.reduce(snap(phase: "countdown", countdown: 3), now: 0);   XCTAssertEqual(r.route(now: 0), .countdown(3))
        r.reduce(snap(phase: "active", rep: 2, goodReps: 2), now: 1); XCTAssertEqual(r.route(now: 1), .goodRep)
        XCTAssertEqual(r.state.goodReps, 2)
        r.reduce(.formCue(text: "Push your knees out", rep: 3), now: 2)
        XCTAssertEqual(r.route(now: 2), .fixForm); XCTAssertEqual(r.route(now: 5.1), .goodRep)
        r.reduce(.haptic("set_done"), now: 6); r.reduce(snap(phase: "rest"), now: 6)
        XCTAssertEqual(r.route(now: 6), .setDone); XCTAssertEqual(r.route(now: 9.1), .recovery)
        r.reduce(.recovery(setIndex: 1, peak: 118, bpm60: 96, drop: 22), now: 7)
        XCTAssertEqual(r.state.recovery, .init(peak: 118, bpm60: 96, drop: 22, setIndex: 1))
    }

    func testBreatherOverridesLiveAndEndsBack() {
        var r = doneSetup(); r.reduce(snap(phase: "active"), now: 0)
        r.reduce(.breather(state: "start", bpm: 128, limit: 120), now: 1); XCTAssertEqual(r.route(now: 1), .breather)
        XCTAssertEqual(r.state.breather, .init(bpm: 128, limit: 120))
        r.reduce(.breather(state: "end", bpm: 112, limit: nil), now: 2);   XCTAssertNotEqual(r.route(now: 2), .breather)
    }

    func testHoldToStopThenResume() {
        var r = doneSetup(); r.reduce(snap(phase: "active"), now: 0)
        r.reduce(.holdBegan, now: 0); XCTAssertEqual(r.route(now: 0), .holdingToStop)
        r.reduce(.holdCancelled, now: 1); XCTAssertEqual(r.route(now: 1), .goodRep)
        r.reduce(.holdBegan, now: 2); r.reduce(.holdCompleted, now: 3.5); XCTAssertEqual(r.route(now: 3.5), .stopped)
        r.reduce(.resume, now: 4); XCTAssertNotEqual(r.route(now: 4), .stopped)
    }

    func testPhoneOutOfReachOnlyDuringSessionAndDismissable() {
        var r = doneSetup(); r.reduce(.reachability(false), now: 0); XCTAssertEqual(r.route(now: 0), .ready)
        r.reduce(snap(phase: "active"), now: 0); XCTAssertEqual(r.route(now: 0), .phoneOutOfReach)
        r.reduce(.dismissOutOfReach, now: 1);    XCTAssertEqual(r.route(now: 1), .goodRep)
        // Coming back and dropping again shows the card again.
        r.reduce(.reachability(true), now: 2); r.reduce(.reachability(false), now: 3)
        XCTAssertEqual(r.route(now: 3), .phoneOutOfReach)
    }

    func testRotationFlow() {
        var r = doneSetup(plan: [.init(id: "forearm_rotation", name: "Forearm turns", sets: 3, reps: 10, doneSets: 0, watch: true, goalDeg: 80)])
        r.reduce(.open(.armExercises), now: 0);           XCTAssertEqual(r.route(now: 0), .armExercises)
        r.reduce(.chooseExercise("forearm_rotation"), now: 0)
        XCTAssertEqual(r.route(now: 0), .startPosition)
        r.reduce(snap(phase: "active", exerciseId: "forearm_rotation", watch: true), now: 1); XCTAssertEqual(r.route(now: 1), .startPosition)
        r.reduce(.startPositionHeld, now: 2);             XCTAssertEqual(r.route(now: 2), .liveTurn)
        r.reduce(.rotationRep(exerciseId: "forearm_rotation", rep: 1, repsTarget: 10, angle: 70, goal: 80, reached: false), now: 2.5)
        XCTAssertEqual(r.state.rotationRep?.rep, 1)
        r.reduce(.formCue(text: "Keep your elbow at your side", rep: nil), now: 3); XCTAssertEqual(r.route(now: 3), .elbowDrift)
        XCTAssertEqual(r.route(now: 6.1), .liveTurn)
        r.reduce(.rotationSet(exerciseId: "forearm_rotation", angles: [70, 82], best: 82, avg: 76, goal: 80), now: 10)
        r.reduce(snap(phase: "rest", exerciseId: "forearm_rotation", watch: true), now: 10); XCTAssertEqual(r.route(now: 10), .rotationSummary)
        XCTAssertEqual(r.state.rotationSet?.best, 82)
        // Next set starts: summary cleared, already zeroed → Live Turn.
        r.reduce(snap(phase: "active", exerciseId: "forearm_rotation", watch: true), now: 70)
        XCTAssertEqual(r.route(now: 70), .liveTurn)
        XCTAssertNil(r.state.rotationSet)
    }

    func testIdleSnapshotsKeepChosenExerciseAndZero() {
        var r = doneSetup(plan: [.init(id: "forearm_rotation", name: "Forearm turns", sets: 3, reps: 10, doneSets: 0, watch: true, goalDeg: 80)])
        r.reduce(snap(phase: "active", exerciseId: "squat"), now: 0)
        r.reduce(snap(phase: "done", exerciseId: "squat"), now: 1)
        r.reduce(.open(.armExercises), now: 2); r.reduce(.chooseExercise("forearm_rotation"), now: 2)
        r.reduce(.startPositionHeld, now: 3)
        r.reduce(snap(phase: "idle", exerciseId: "squat"), now: 4)          // periodic idle snapshot
        XCTAssertEqual(r.route(now: 4), .startPosition)
        r.reduce(snap(phase: "active", exerciseId: "forearm_rotation", watch: true), now: 5)
        XCTAssertEqual(r.route(now: 5), .liveTurn)
    }

    func testArmExercisesOnlyWhenIdle() {
        var r = doneSetup(); r.reduce(snap(phase: "active"), now: 0)
        r.reduce(.open(.armExercises), now: 0)
        XCTAssertEqual(r.route(now: 0), .goodRep)
    }

    func testTremorOverlayOnlyWhenMonitoring() {
        var r = doneSetup(tremor: false)
        r.reduce(.open(.tremorToday), now: 0); XCTAssertEqual(r.route(now: 0), .ready)
        var m = doneSetup(tremor: true)
        m.reduce(.open(.tremorToday), now: 0); XCTAssertEqual(m.route(now: 0), .tremorToday)
        m.reduce(.close, now: 1); XCTAssertEqual(m.route(now: 1), .ready)
    }

    func testOpenIgnoresNonOverlayRoutes() {
        var r = doneSetup()
        r.reduce(.open(.goodRep), now: 0)
        XCTAssertNil(r.state.overlay)
    }

    func testDeepLinkReadyClosesOverlay() {
        var r = doneSetup()
        r.reduce(.open(.settings), now: 0); XCTAssertEqual(r.route(now: 0), .settings)
        r.reduce(.deepLinkReady, now: 1); XCTAssertEqual(r.route(now: 1), .ready)
    }

    func testSessionEndClearsTransientState() {
        var r = doneSetup()
        r.reduce(snap(phase: "active"), now: 0)
        r.reduce(.formCue(text: "Slow down", rep: 2), now: 1)
        r.reduce(snap(phase: "done"), now: 2)
        XCTAssertNil(r.state.formCue)
        XCTAssertEqual(r.route(now: 2), .ready)
    }

    func testHapticFixFormAloneDoesNotChangeRoute() {
        var r = doneSetup(); r.reduce(snap(phase: "active"), now: 0)
        r.reduce(.haptic("fix_form"), now: 1)
        XCTAssertEqual(r.route(now: 1), .goodRep)
    }
}
