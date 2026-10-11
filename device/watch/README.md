# Mira Watch mockup

> The real Watch companion app now lives in `device/ios/MiraWatch/` (Plan 4: Ready/Stop, live reps, heart
> rate over HealthKit, server-driven haptics — see the README "Apple Watch companion" section and
> `device/ios/NETWORKING.md` for the WatchConnectivity relay). This mock stays as-is for design review and
> is not touched by that work.

A working SwiftUI concept while the final Watch designs are pending. Uses the
existing Mira mascot and bundled Inter fonts. This is a separate project from
the patient iPhone app, so it does not change your onboarding or saved profile.

## Try it without an Apple Watch

1. Open `device/Mira.xcworkspace` in Xcode. This shows both the iPhone and Watch
   projects in the left sidebar. Opening only `device/ios/Mira.xcodeproj` shows
   just the patient iPhone project.
2. In the top toolbar, select the **MiraWatchPreview** scheme. A scheme chooses
   which app Xcode runs; this one displays the Watch mockup on an iPhone.
3. Select an iPhone simulator, then press **Run** (the triangle or ⌘R).
4. Tap **I'm ready** inside the watch. Scroll inside its screen to see controls.
   **Demo rep** adds a rep. After eight reps, the finished screen opens.
5. Try **Pause**, **Resume**, and **Stop**. Tap Stop and confirm, or hold it for
   one second. Stop ends the local demo and shows the number completed.
6. Use **Preview screen** below the watch to jump to Ready, Live, Paused,
   Finished, or Stopped. **Restart preview** restores that screen's sample
   values. **Small watch** checks the narrower layout.

## Run as a watchOS app

In the same workspace, select **MiraWatchMock**, then an Apple Watch simulator.
If none is available, install watchOS through **Xcode → Settings → Components**.
The watch target requires watchOS 10 or later. Device deployment requires your
own signing team. Both targets compile the same `Shared/WatchMockView.swift`.
Xcode Canvas also has previews for Ready, Exercise, Paused, and Finished.

Verification: both targets compile. The iPhone simulator was used to check
Ready → Live, sample rep counting, Pause/Resume preserving the rep count,
tap-and-confirm Stop, and completion at eight reps. Large and small preview
layouts were inspected. The actual Watch app has not been run yet because this
Mac has no watchOS simulator runtime installed; hold-to-stop also needs a
hands-on check on a Watch or simulator.

## What is mocked

- The plan (squats, one set, eight reps) and fixed 84 BPM heart rate are examples.
- Reps change only when you tap Demo rep. No camera or sensor measures them.
- Ready, pause, resume, and stop only change this app's in-memory screen state.
- No HealthKit permissions, workouts, motion tracking, phone pairing, haptics,
  safety limits, emergency alerts, or server messages are implemented.
- Demo state resets when the app restarts. No health data is persisted.

This follows the companion's Ready/Stop and heart-rate concepts from spec 12a;
it is not a final Figma Watch design. Pause and manual sample reps are prototype
controls. A tap-and-confirm stop is provided alongside the spec's hold-to-stop
interaction so it can also be used with accessibility controls.

## Later integration

Connect real workout heart-rate samples through HealthKit, then WatchConnectivity
to the iPhone's existing networking layer. The server remains responsible for
rep counts, coaching, and care-team limits. Replace demo values and controls only
when those contracts exist; socket connectivity alone must not imply device
readiness. Add server-driven haptic coaching and handle disconnected/stale data
before treating the companion as ready for an exercise session.

The watch mockup follows Apple's [SwiftUI watchOS app structure](https://developer.apple.com/documentation/watchos-apps/building_a_watchos_app).
Fonts and license are reused from `../ios/Mira/Design/Fonts`; keep that directory
when sharing the project. The mascot is copied from the existing Figma export.
