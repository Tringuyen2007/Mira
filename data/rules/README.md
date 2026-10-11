# Exercise rule defaults

These JSON files define initial camera-only thresholds, in degrees and seconds.
They are starting values for the current camera B setup, not validated clinical
cutoffs. An `ExerciseCounter` is created with an exercise name and optional
patient overrides, then called for each `FrameResult` after `angles` and optional
`PhysicsResult` are populated. Call `finish_set()` to collect the set result and
reset. The caller should create a separate counter for each person and exercise.

Squats use the mean left/right interior knee angle: smaller means deeper. For
two-camera 3D, the 155° ready / 145° entry / 110° target / 155° finish thresholds
provide hysteresis. For one-camera fitting, the counter records the person's
current upright knee angle and hip height. An attempt starts after 15° of knee
bend, reaches depth after 72° of knee bend **and** 30 cm of hip descent, and
requires the camera image to show a knee angle below 108° plus hip travel of
at least 55% of the standing torso size for 0.05 seconds. This guards
against the 3D fit mistaking a weight shift or bend for a deep squat. The rep
finishes when both knee and hip return near standing. It only acquires a ready
posture above 115° knee angle and 55 cm hip height. These one-camera numbers
were chosen using the labeled 20261011-082404 session and require independent
validation before use as general or clinical cutoffs. Patient overrides can set
thresholds after reviewing that person's movement and camera fit.
Bridges use mean interior hip angle: larger means straighter. A bridge requires
floor posture from the physics estimator or shoulder/hip heights in the mat
frame. Single-leg stance requires a current physics result with one grounded
foot and positive load confidence. It has 0.25 s entry and 0.2 s exit dwell.

All form rules require their condition continuously for `duration_s`, then emit
one event per `cooldown_s` while it remains true. The `form:<rule_id>` flag is
active after the duration is met and clears when the condition recovers. Missing
measurements suspend the corresponding rule; lost, measuring and `pose_held`
frames interrupt partial reps and holds. A time gap over 0.5 s does the same.

The score is a heuristic 0–100 integer. Dynamic reps lose up to 30 points for
ROM below the configured target, 20 for tempo outside the configured interval,
15 for left/right angle asymmetry above 10°, and 35 for sustained form
violations (17.5 per distinct rule). Holds lose up to 45 for duration below
target, 20 for side lean above 5°, and 35 for sustained violations. Rejected
attempts are returned and included in `SetResult.attempts`, but do not increase
the accepted `rep` count. Their displayed quality is capped at 49 so a failed
depth or return check cannot appear as a high-quality rep. A set's mean quality
uses accepted reps only.

These rules have synthetic coverage. The 20261011-082404 camera B recording has
three confirmed clean deep squats; the revised one-camera rule counts all three
and rejects nine subsequent shallow/weight-shift attempts. This is same-session
development and replay, not independent validation. A later live demo still
counted weight shifts/bends, so the image cross-check was added. That live run
was not recorded; its exact false-positive frames remain unverified. Score
validity is unproven.
