# Apple Watch app — design (branch `tri-watch`)

Source design: Figma `MIRA` → page **AppleWatch Hi-FI** (node `181:2067`), 25 screens at 208×248 (two scroll to 208×300 / 208×360)
in five sections plus a component sheet. Builds on the Plan 4 companion already on `main` (Ready/Stop, live reps, HR relay,
three haptics) — see `docs/superpowers/plans/2026-10-11-plan4-watch.md`.

## Decisions (agreed 2026-10-11)

1. **No new server.** Watch ⇄ WatchConnectivity ⇄ iPhone ⇄ `/ws/events` + REST ⇄ the existing single-process FastAPI ⇄ Tiger DB.
   The watch never opens its own socket; "Phone out of reach" means the session carries on with cameras only.
2. **Tremor:** real `CMMovementDisorderManager` code behind the `MIRA_TREMOR_REAL` Swift compilation condition; without it the
   watch produces clearly-labelled simulated summaries (`source = "simulated"`). Therapist turns monitoring on per patient.
3. **Therapist dashboard:** stays on `therapist-page`. `tri-watch` adds only server endpoints/events the dashboard can consume and
   does not touch `device/dashboard/`. `PATCH /patients/{id}/limits` uses the exact contract from `therapist-page`
   (`{therapist_id, hr_limit}` → `{patient_id, hr_limit}`, `agent_actions` row `hr_limit_set`); when the branches merge, keep
   tri-watch's route (it also pushes live) and delete the duplicate in `server/api/dashboard.py`.
4. **Scope:** all five Figma sections.

## Screens (Figma → behaviour)

| Section | Screen | Behaviour |
|---|---|---|
| 01 Setup | 1 Paired | Shown once after the iPhone is paired (`/me/watch` reachable). "Hi, {first name}!" → Next. |
| | 2 Heart Rate | Allow → HealthKit auth (HR read, workout share). "Not now" allowed; HR chip then shows "—". |
| | 3 Which Arm | Left/Right, stored in `device_prefs.watch_arm`. Asked again if a rotation exercise needs the other arm. |
| | 4 Sync Wave | Patient waves; ring fills with `sync_progress`; locks on `sync_progress{locked:true}`. Offset saved to the session. |
| | 5 Learn the Taps | Scrolls (208×300). Rows: 1 tap = good rep, 2 buzzes = fix form, long buzz = set done. "Try them" plays each. |
| 02 Ready & Stop | 1 Ready | Big round button: tap = ready (`watch_button ready`). Hold 1.5 s = stop. Double-tap gesture = ready (watchOS 11+, setting). |
| | 2 Countdown | 3-2-1 from the `countdown` phase; each number plays `countdown_tick`. Haptic chip "Ready". |
| | 3 Holding to Stop | Ring fills over 1.5 s while held; early release cancels. |
| | 4 Stopped | "Session stopped / Rest well, {name}." Resume → `watch_button resume` (= engine `command start`, which un-pauses the same set). |
| 03 Live | 1 Good Rep | Header "Set X of Y", haptic chip, big good-rep count "/ target", "GOOD REPS", HR chip. |
| | 2 Fix Form | Red warning card: triangle + "●● Fix form" chip + plain-words cue (from the form event's cue text). "Rep N" + HR chip. Never colour alone. |
| | 3 Set Done | Long buzz chip, mascot, "Set done!", "{n} good reps". Recovery follows automatically. |
| | 4 Recovery | Header "Rest". HEART RATE big bpm, recovery line, "Down {drop} in 1 min", "Next set in m:ss". From `hr_recovery` + rest timer. |
| | 5 Breather | Header "Paused". Mascot, "Let's take a breather", "⚠ {bpm} bpm · limit {limit}", End session button. Auto-resumes. |
| 04 Rotation | 1 Arm Exercises | List of today's watch exercises (forearm turns / shoulder turns / wrist bends) "N sets · R". Tap → start. |
| | 2 Start Position | Pose glyph + cue ("Elbow at your side, thumb up"); ring fills while still (1.5 s); zeroes heading. |
| | 3 Live Turn | "REP n OF N", semicircle gauge (green arc = current, dark tick = goal), big angle, "goal 80°", Down/Up labels, HR chip. |
| | 4 Elbow Drift | Warning card "Keep your elbow at your side", "The cameras saw it lift". 2 buzzes. |
| | 5 Set Summary | "BEST TURN" big angle, per-rep bars (goal reached = green with dot on top), "Average 75° · goal 80°". |
| 05 More | 1 Tremor Today | Only when `tremor_monitoring`. Level (None/Slight/Mild/Strong), bar, "Slight for 3 h 10 min", "Extra movements: few", "Shared with {therapist}". |
| | 2 Phone Out of Reach | When WC unreachable during a session. "The cameras keep counting." OK dismisses. |
| | 3 Smart Stack | WidgetKit accessoryRectangular: mascot, MIRA, next exercise, "k of n done". Tap opens Ready. |
| | 4 Settings | Scrolls (208×360). Taps (Normal/Strong), Heart limit (read-only "120 bpm · by Dr. Lee"), Watch arm, Double-tap, Captions. |

Components: Status Bar (title left in accent, time right), Button Primary (ink pill, white text), Button Secondary (outlined),
Heart Rate Chip (heart + bold bpm + "bpm"), Haptic Chip (Kind = Tap ●, Double ●●, Long ▬), halo/orbit background rings.
Colours/fonts are read from Figma with `get_design_context` per section during implementation (Inter, same palette as the
iPhone app's `MiraUI.swift` tokens).

## Server

### Migration `server/db/migrations/005_watch.sql` (idempotent)
- `ALTER TABLE wrist_motion ADD COLUMN IF NOT EXISTS patient_id text, ADD COLUMN IF NOT EXISTS accel real[]` (keep existing columns).
- `ALTER TABLE patients ADD COLUMN IF NOT EXISTS tremor_monitoring boolean NOT NULL DEFAULT false,
   ADD COLUMN IF NOT EXISTS hr_limit_set_by text` (therapist id).
- `ALTER TABLE sessions ADD COLUMN IF NOT EXISTS watch_clock_offset_ms int`.
- `hr_recovery(session_id, patient_id, set_index, peak_bpm, bpm_60s, drop_bpm, time)` PK `(session_id, set_index)`.
- `rotation_sets(session_id, patient_id, exercise_id, set_index, angles real[], best_deg, avg_deg, goal_deg, time)` PK `(session_id, exercise_id, set_index)`.
- `tremor_daily(patient_id, day date, none_min, slight_min, mild_min, strong_min, unknown_min, dyskinesia text, source text, updated_at)` PK `(patient_id, day)`.
- `GRANT SELECT` on the three new tables to `copilot_ro`; add them to `sql_guard` allow-list.

### Catalog
`server/exercises.py` gains `forearm_rotation` ("Forearm turns", goal `rotation_deg`), `shoulder_rotation` ("Shoulder turns"),
`wrist_flexion` ("Wrist bends"), each flagged `watch=True` (needs the watch's motion).

### REST
- `GET /me/watch` (patient scope) → `{first_name, hr_limit, hr_limit_set_by_name, prefs:{taps, watch_arm, double_tap, captions},
  tremor_monitoring, plan:[{exercise_id, name, sets, reps, done_sets, watch}], therapist_name}`.
- `POST /me/tremor` (patient scope, rate-limited) body `{days:[{day, none_min, …, dyskinesia, source}]}` → upsert, max 14 days.
- `PATCH /patients/{id}/limits` (full token) — therapist-page contract; also sets `hr_limit_set_by`, refreshes the cached
  profile in persistence/Guardian/Coach, and emits `limits_changed{patient_id, hr_limit, set_by_name}`.
- `PATCH /patients/{id}/watch-features` (full token) `{tremor_monitoring}`; emits `limits_changed`-style `watch_features_changed`.
- `GET /patients/{id}/watch` (full token) → `{hr_recovery:[last 20], rotation_sets:[last 20], tremor:[14 days]}`.
- Watch settings ride the existing `PUT /me` `device_prefs` (keys `watch_taps`, `watch_arm`, `watch_double_tap`, `watch_captions`).

### Events (all on `/ws/events`, per-patient filtered as today)
Client → server (add to both `CLIENT_EVENT_TYPES` sets): `wrist` (existing type, payload `{t, rotation_deg, quat_wxyz?, accel?}`),
`watch_sync {t, accel_mag[], t0, dt_ms}`; `watch_button` gains `resume` → `command start` (the engine resumes a paused session on `start`).

Server → clients: `haptic` patterns add `countdown_tick`, `breather`; `breather {state:"start"|"end", bpm, limit}`;
`hr_recovery {set_index, peak_bpm, bpm_60s, drop_bpm}`; `rotation_rep {exercise_id, rep, reps_target, angle_deg, goal_deg, reached}`;
`rotation_set {exercise_id, set_index, angles, best_deg, avg_deg, goal_deg}`; `sync_progress {progress 0..1, locked, offset_ms?}`;
`caption {text}`; `limits_changed {hr_limit, set_by_name}`; `watch_features_changed {tremor_monitoring}`.
Add the patient-relevant ones to the phone filter and `hr`/`breather` to `DEVICE_EVENT_TYPES` (LCD can show the breather).

### Agents
- **SafetyGuardian — breather = the existing HR incident.** Today HR > `hr_limit` for `HR_SUSTAIN_S` already opens an `hr`
  incident with `pause=True` (check-in → Retell escalation). Add: on an `hr` incident opening emit `breather{state:"start", bpm,
  limit}` + haptic `breather` and a therapist notification (kind `heart_rate`); while the incident is still pre-escalation, HR
  < `limit - 5` for `BREATHER_RESUME_S` (10 s) closes it with resolution `hr_recovered`, sends engine `command start` (resume)
  and `breather{state:"end"}`. Once escalated, existing behaviour is unchanged (no auto-resume). "End session" on the breather
  screen = `watch_button stop`.
- **RecoveryAgent** (`server/agents/recovery.py`): on `set_done` remember peak HR over the set; 60 s later (or at next set start,
  whichever first, then using the last reading) emit `hr_recovery` and write `hr_recovery`.
- **RotationCoach** (`server/agents/rotation.py`): active only when the current exercise is a watch exercise. Counts reps from the
  `wrist.rotation_deg` stream with hysteresis (rep = excursion ≥ 60 % of goal then return within 15° of zero), emits `rotation_rep`
  + `rep`-equivalent engine count (`haptic good_rep` when goal reached), `rotation_set` at set end, writes `rotation_sets`.
  Elbow drift: uses pose `elbow`/`shoulder` keypoints from the engine stream (or mock flag); drift > 0.08 m off torso for
  > 0.5 s → form event cue "Keep your elbow at your side" (reuses the form-event → `haptic fix_form` path).
- **ClockSync** (`server/agents/clock_sync.py`): on `watch_sync` windows, cross-correlates watch `accel_mag` with the camera wrist
  speed series over ±1 s lags; progress = best correlation / 0.7; locks when corr ≥ 0.7 for 2 windows, or after 8 s falls back to
  the RTT-estimated offset (`locked:true, method:"rtt"`). Writes `sessions.watch_clock_offset_ms`.
- **Persistence:** `wrist` samples → `wrist_motion` through the batched writer (≤ 10 Hz per patient).
- **SessionVoice:** every spoken line is also emitted as `caption {text}`.
- **SessionAnalyst / notifications:** analysis context adds hr_recovery rows, rotation_sets, and tremor_daily for the last 7 days.
- **Insight:** rule "tremor trend" — mild/strong minutes up ≥ 30 % week-over-week → memory_event + therapist notification.
- **Copilot:** can read the new tables (role grant + allow-list); prompt notes the tables.

### Scaling notes
Single process remains (in-memory session state). New load: wrist ≤ 10 Hz only during rotation/sync, HR every 3 s, sync windows
1 Hz — all under the 20 msg/s WS bucket. Wrist rows batch through `db/writer.py`. New tables are small regular tables; `wrist_motion`
stays a hypertable.

## iPhone (`device/ios/Mira`)
- `Net/WatchWire.swift`: new message kinds — to watch: `setup{firstName, hrLimit, hrLimitSetBy, prefs, tremorMonitoring, plan, therapist}`,
  `breather`, `recovery`, `rotationRep`, `rotationSet`, `sync`, `caption`, `formCue{text, rep}`, extended `haptic`;
  from watch: `wrist{t, deg, accel?}`, `syncWindow`, `tremor{days}`, `prefs{…}`, `button{ready|stop|resume}`.
  `LiveSnapshot` gains `exerciseId`, `goodReps`, `restRemaining`, `phase` values `rest`, `paused`.
- `Watch/WatchBridge.swift`: fetch `GET /me/watch` on launch, pairing, foreground and `limits_changed`/`watch_features_changed`;
  push as application context. Relay new events; forward wrist (downsample to 10 Hz), sync windows, tremor uploads
  (`POST /me/tremor`), prefs (`PUT /me` device_prefs merge).
- `Net/Models.swift` + `Net/LiveSocket.swift`: decode/encode the new event types.

## Watch (`device/ios/MiraWatch`)
- `Design/`: `WatchTheme` (Figma colours, Inter fonts bundled from `Mira/Design/Fonts`), `HaloBackground`, `WatchStatusBar`,
  `WatchPrimaryButton`, `WatchSecondaryButton`, `HeartRateChip`, `HapticChip`, `WarningCard`, `ProgressRing`, `RotationGauge`.
- `Core/` (pure Swift, no WatchKit — compiled into SwiftPM tests): `WatchRoute` + `WatchFlowReducer` (setup/ready/live/rotation/more
  state machine from snapshot + events), `RotationMath` (quaternion → twist about forearm axis, zeroing, rep detection
  mirror for live UI), `HapticPlan` (pattern → sequence, strength), `SyncWindowBuilder`, `TremorSummarizer` (+ simulated generator).
- `Screens/`: Setup/, Ready/, Live/, Rotation/, More/ matching the table above; `RootView` routes by `WatchFlowReducer`.
- Services: `MotionService` (CMMotionManager deviceMotion 50 Hz → 10 Hz out), `TremorService` (`#if MIRA_TREMOR_REAL`),
  `WorkoutHeartRate` (existing), haptics via `WKInterfaceDevice` with Strong = doubled pattern.
- Double-tap: `.handGestureShortcut(.primaryAction)` under `if #available(watchOS 11, *)`, gated by the setting.
- **MiraWatchWidget** target (WidgetKit, watchOS 10): Smart Stack `accessoryRectangular` + `accessoryCircular`; reads today's plan
  from App Group `group.com.mira.app` written by the watch app; tap deep-links `mira://ready`.

## Testing
- pytest: migration 005, `/me/watch`, `/me/tremor`, limits + live push + cache refresh, watch-features, `/patients/{id}/watch`,
  breather start/resume + escalation coexistence, recovery, rotation counting + elbow drift, clock sync (lock + RTT fallback),
  wrist persistence, captions, copilot allow-list, tremor insight rule.
- `swift test` (device/ios): WatchWire round-trips for every new kind, `WatchFlowReducer`, `RotationMath`, `HapticPlan`,
  `SyncWindowBuilder`, `TremorSummarizer`.
- `xcodebuild` builds `Mira`, `MiraWatch` (incl. widget) for simulators.
- Suites run once at the end; one final code review after every feature is in.

## Out of scope
Direct watch→server networking; dashboard UI panels (after the therapist-page merge); fall detection (spec 13b);
multi-process server.
