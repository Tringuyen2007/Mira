# Plan 3 — Home, Progress, Care (+ messaging), Settings wired to the backend

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every remaining "not connected yet" card in the iPhone app shows real data: Home (greeting, streak, plan, setup status), Progress (trends, balance, milestones, calendar), Care (therapist, next visit, notes, highlights, two-way messages, emergency contact), Settings (reminders, device prefs sync, share default, data export, delete). Plus the ATS exception the demo phone needs to talk to the Vultr server over plain HTTP.

**Architecture:** One `PatientStore` (owned by `AppState`) loads `/me`, `/me/care`, `/patients/{id}/sessions`, `/patients/{id}/progress`, `/patients/{id}/milestones` and `/me/messages`, refreshes on foreground / pairing / session end, and keeps cached data visible on errors. Server adds a `messages` table with a `message` event, `/me/care`, `/me/messages`, `/patients/{id}/milestones`, `/me/test-alert`, and carries `device_prefs` on `/ws/live` so the kiosk can honour captions/volume. Screens replace their `MiraPendingCard`s with store-driven content; every `preview` branch stays.

**Tech Stack:** FastAPI/psycopg, SwiftUI + Swift Charts + UserNotifications, kiosk JS.

Spec: `docs/superpowers/specs/2026-10-11-wire-ios-backend-design.md` §6 (all), §8, §9. Plans 1–2 are on the branch: `Principal`/`require_principal`, `/me` + `build_me`, `api/identity_models.py`, `exercises.py`, `queries.patient_sessions`, `session_analyses` (highlights), `LiveSessionStore.sessionSummary/currentSessionID/lcdOnline`, `AppState.startSession/loadSession/checkin/share`.

## Global Constraints

- Branch `wire-tri`; commit per task; trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Add, never remove; admin/device/LCD behaviour unchanged; every iOS `preview` branch intact; no new image assets.
- SQL placement rules (writes.py `_safe` / queries.py `DBError`); idempotent migrations in `server/db/migrations/`.
- Patient routes use `require_principal` + ownership; admin may act for a patient with `?patient_id=` (as `/me` does).
- `DELETE /me` must also purge `messages` (add to `purge_patient_data`).
- iOS: `WireJSON` models, `Codable, Sendable`; cached data stays visible on errors; errors surface as one non-blocking banner line; fixtures byte-identical to `server/samples`.
- Tests focused while developing; full suites once at the end (Task 8).

---

## File structure

| File | Responsibility |
|---|---|
| `server/db/migrations/003_messages.sql` (+ `schema.sql`) | `messages` table |
| `server/db/writes.py` / `queries.py` | `insert_message`, `mark_messages_read`, `list_messages`, `care_notes`, `patient_milestones`, `latest_highlights`; `purge_patient_data` += messages |
| `server/api/care.py` (new) | `GET /me/care`, `GET /me/messages`, `POST /me/messages`, `POST /patients/{id}/messages`, `GET /patients/{id}/milestones`, `POST /me/test-alert` |
| `server/api/me.py`, `server/engine_bridge.py` | `device_prefs` → `LiveState.device_prefs` → `/ws/live`; `PUT /me` pushes it live |
| `server/devices.py`, `server/main.py` | `message` event in the device allow-list? **No** (LCD doesn't show messages); router registration; `device_prefs` observer |
| `device/kiosk/js/state.js`, `view.js`, `main.js` | honour `live.device_prefs.captions` (hide caption bar) and `volume` (AudioPlayer volume) |
| `device/ios/Mira/App/PatientStore.swift` (new), `AppState.swift` | the store |
| `device/ios/Mira/Net/Models.swift`, `APIClient.swift` | care/messages/milestones models + calls |
| `device/ios/Mira/Features/Home/HomeView.swift`, `Progress/ProgressViewScreen.swift`, `Care/CareView.swift`, `Settings/SettingsView.swift` | wiring |
| `device/ios/Mira/App/Reminders.swift` (new) | `UNUserNotificationCenter` scheduling from `MiraPreferences` |
| `device/ios/Mira/Info.plist` | ATS: allow the plain-HTTP demo server |
| `device/ios/Tests/PatientStoreTests.swift` (new), `ContractFixtureTests.swift` | tests |
| `server/tests/test_care_api.py` (new), `server/tests/test_device_prefs.py` (new) | tests |

---

### Task 1: `messages` table + care/messages/milestones routes (server)

**Files:** create `server/db/migrations/003_messages.sql`, `server/api/care.py`, `server/tests/test_care_api.py`; modify `server/db/schema.sql`, `writes.py`, `queries.py`, `server/main.py`, `server/api/me.py` (purge), `server/tests/test_me.py` (purge assertion), `server/tests/test_security.py` if needed (routes carry `require_principal`/`require_token`).

**Interfaces:**
- Migration (spec §6.4): `messages(id bigserial PK, patient_id text NOT NULL REFERENCES patients(id), sender text CHECK IN ('patient','therapist','mira'), kind text DEFAULT 'message' CHECK IN ('message','note','visit_highlight'), body text NOT NULL, created_at timestamptz DEFAULT now(), read_at timestamptz)` + index on `(patient_id, id)`.
- writes: `insert_message(db, patient_id, sender, kind, body) -> dict | None` (RETURNING row), `mark_messages_read(db, patient_id, sender_not: str) -> int | None` (marks the *other* side's messages read).
- queries: `list_messages(db, patient_id, kind="message", since_id=None, limit=100) -> list[dict]` (ascending by id), `care_notes(db, patient_id, limit=20)` (= `messages` kind='note' newest first), `patient_milestones(db, patient_id)` (= `memory_events` kind='milestone' newest first: `{id, time, message, evidence}`), `latest_highlights(db, patient_id, limit=3)` (= highlights jsonb of the most recent `session_analyses` row for a non-simulated session of this patient, plus the most recent 3 `memory_events` with kind in ('insight','depth_gain','asymmetry_worse','compensation','pain','missed_days') as `{kind, text, time, source: "ai"}`).
- Routes (all in `care.py`, patient-or-admin via `require_principal` + the `_patient_for(principal, patient_id)` helper — move that helper from `me.py` into `identity_models.py` or a tiny `server/api/scope.py` so both routers import it):
  - `GET /me/care` → `{therapist, next_visit_at, hr_limit, emergency_contact, alert_therapist, notes: [...], highlights: [...], unread_messages: n}`
  - `GET /me/messages?since_id=` → `{messages: [{id, sender, kind, body, created_at, read_at}]}`; also marks therapist/mira messages read.
  - `POST /me/messages {body: str 1..1000}` → `{message}`; emits `hub.emit("message", {patient_id, message: {...}})` and `notifications.create(kind="message", severity="normal", therapist_id, patient_id, title=f"Message from {first_name}", body=body[:140], link=f"/patients/{pid}#messages", source_table="messages", source_id=str(id), dedupe_key=f"message:{id}")`.
  - `POST /patients/{id}/messages {body, kind: message|note|visit_highlight = message}` (full only) → inserts as sender `therapist`, emits `message`.
  - `GET /patients/{id}/milestones` (owner or full) → `{patient_id, milestones: [...]}`.
  - `POST /me/test-alert` → creates a notification `kind="test", severity="low"` for the therapist; if `cfg.CALLS_DISABLED` is False **and** `cfg.ALLOW_TEST_CALLS` (new, default False) → `state.notifier.test_call(contact_phone)` if such a method exists, else just the notification; returns `{logged: true, called: bool, note: "Test alert logged. Calls are disabled on this server." | "Calling your emergency contact now."}`.
- `purge_patient_data`: add `messages` to the per-patient delete tuple; assert in `test_me.py`.
- iOS `/ws/events` gets the `message` event (full/patient scope; `_patient_accepts` already filters by `patient_id`).

- [ ] Steps: TDD (`test_care_api.py`: pair → `/me/care` has therapist + next_visit + empty notes; admin posts a note and a message → `/me/care.notes` has 1, `/me/messages` has 1 and `unread_messages` goes 1→0 after reading; patient posts → notification created and a `message` event on `/ws/events`; `/patients/bob/milestones` 403; `/me/test-alert` → `{logged: true, called: false}` with `CALLS_DISABLED=1`), implement, run `test_care_api.py test_me.py test_security.py test_migrate.py`, commit `"Care API: messages, notes, highlights, milestones, test alert"`.

---

### Task 2: `device_prefs` on `/ws/live`; kiosk honours captions & volume

**Files:** `server/engine_bridge.py` (`LiveState.device_prefs: dict`), `server/api/me.py` (`PUT /me` → `get_state().bridge.live.device_prefs = …` when the patient is the current session's; also set on session start from the profile → extend `queries.profile_row`/`PatientProfile` with `device_prefs`), `server/state.py` (`PatientProfile.device_prefs: dict = field(default_factory=dict)`), `server/db/persist.py` (`profile_from_row`), `device/kiosk/js/{state,view,main}.js` + tests, `server/tests/test_device_prefs.py`.

**Interfaces:** `/ws/live` payload gains `"device_prefs": {"captions": bool, "volume": 0..1, "slower_speech": bool}` (defaults `{}` → kiosk treats missing as captions on, volume 1). Kiosk: `view()` adds `captionsOn = live.device_prefs?.captions !== false`; `render` hides `#caption-bar` when off; `main.js` sets `audio.volume = clamp(live.device_prefs?.volume ?? 1)` (AudioPlayer gets a `volume` property applied to each clip).

- [ ] Steps: TDD (server: `PUT /me {device_prefs:{captions:false}}` while Maya's session is live → next `/ws/live` message has `device_prefs.captions == false`; kiosk node tests: caption bar hidden when captions false), implement, run `test_device_prefs.py test_live_state.py test_me.py` + `node --test`, commit `"Device prefs flow from the phone to the LCD over /ws/live"`.

---

### Task 3: Samples + iOS models/APIClient/PatientStore

**Files:** `server/samples/rest_get_me_care.json`, `rest_get_me_messages.json`, `rest_post_me_messages.json`, `rest_get_patient_milestones.json`, `ws_events_message.json` (+ `test_samples.py`, fixtures copies); `device/ios/Mira/Net/Models.swift`, `APIClient.swift`, `device/ios/Mira/App/PatientStore.swift` (new), `AppState.swift`, `Package.swift` (add `App/PatientStore.swift` to the library sources), `device/ios/Tests/PatientStoreTests.swift`, `ContractFixtureTests.swift`.

**Interfaces:**
- Models: `CareResponse { therapist: MeTherapist?, nextVisitAt: String?, hrLimit: Int?, emergencyContact: PairContact?, alertTherapist: Bool, notes: [CareMessage], highlights: [CareHighlight], unreadMessages: Int }`, `CareMessage { id: Int, sender: String, kind: String, body: String, createdAt: String, readAt: String? }`, `CareHighlight { kind: String?, text: String, time: String?, source: String? }`, `MessageList { messages: [CareMessage] }`, `MessageEvent { patientId: String?, message: CareMessage }` + `SocketEvent.message`, `MilestoneList { patientId, milestones: [Milestone] }`, `Milestone { id, time, message, evidence: JSONValue? }`, `TestAlertResponse { logged, called, note }`.
- APIClient: `care()`, `messages(sinceID:)`, `sendMessage(_:)`, `milestones(patientID:)`, `testAlert()`.
- `PatientStore` (`@MainActor @Observable`, in `App/` so the package tests cover it): `me`, `care`, `sessions: [SessionListItem]`, `progress: [String: PatientProgress]` (by exercise id), `milestones`, `messages`, `lastError: String?`, `isRefreshing`; `func refreshAll(api: APIClient, patientID: String)` (parallel `async let`, each failure recorded, successes applied), `refreshMessages`, `send(_ body:)`, `appendIncoming(_ event: MessageEvent)`; derived helpers (pure, unit-tested): `streakDays(from sessions, calendar, today) -> Int` (consecutive days ending today or yesterday with ≥1 ended session), `weekDots(...) -> [Bool]` (Mon..Sun of the current week), `doneDays(in month:)`, `kneeTrend(progress) -> [(Date, Double)]`, `forecast(points, target) -> (date: Date, slopePositive: Bool, band: Double)?` (least squares over ≤14 most recent days; nil when < 3 points or slope ≤ 0), `balanceSeries(progress, affectedSide) -> [(Date, Double)]` (percent on the affected leg = `affected_load_pct` when present, else `50 - avg_asymmetry`).
- `AppState`: `let store = PatientStore()`; `refreshMe()` becomes `refreshAll()` (keeps `me` in sync: `me = store.me`); call on foreground, after `pair()`, and when `liveSession.sessionSummary` changes; `LiveSessionStore` forwards `.message` events to `store.appendIncoming`.

- [ ] Steps: samples from a DB-backed TestClient (as before) + fixtures; Swift TDD for the pure helpers (streak with gaps, week dots, forecast positive/negative slope, balance series) and decode tests for the new fixtures; `swift test` green; commit `"iOS: PatientStore with care, messages, milestones, progress helpers"`.

---

### Task 4: Home + Progress screens

**Files:** `HomeView.swift`, `ProgressViewScreen.swift`.

- **Home** (spec §6.2): greeting `Good morning/afternoon/evening, {firstName}` (hour-based), subtitle `"\(streak) days in a row. Keep it up!"` when streak ≥ 2 else "Let's take your next step."; week strip from `weekDots` (filled `imgDone` for done days, `imgToday` for today, dashed otherwise); "This week" label `"\(streak)-day streak"` when ≥ 2; Today's plan rows from `me.prescriptions` (`exerciseName`, `"\(sets) sets of \(reps)"`), "About N min" (`max(5, Σ sets·(reps·4+30)/60)`); when no prescriptions keep the existing "Complete your setup" pending copy; Start button title "Start today's session" when linked; the thumbs-up hint shows when linked; keep "Set up your space" / "Session summary" links. **WeekView**: knee delta = last − first `max_knee` of the 7-day window vs the previous 7 days (progress for the first prescription; "+8°"-style with sign; fallback copy when < 2 points); balance rows = `balanceSeries` first vs last of 14 days; setup rows: Cameras = `me.calibration.calibrated ? "Both working" : "Not confirmed"`, Mira = `(liveSession.lcdOnline ?? me.lcd?.online) == true ? "Online" : "Offline"`.
- **Progress** (spec §6.3): exercise segments from `me.prescriptions` names (fallback to the three sample labels only in preview); knee chart from `kneeTrend` with `RuleMark` at the prescription target (`targets[targetKey]`) and the dotted forecast + band when `forecast` is non-nil (badge "On track for {target}° by {date}"); balance chart from `balanceSeries` with the even zone 45–55; Goals: milestones from `store.milestones` (done, newest first) + "next" card = first prescription target not yet reached (`max_knee < target`) with the forecast date if any; Calendar: `doneDays` for the shown month, "N of M days done" = done days / days elapsed this month (or days in month for past months). All pending cards remain for the no-data state with their current copy.

- [ ] Steps: implement; `xcodebuild` BUILD SUCCEEDED; `swift test` green; commit `"iOS: Home and Progress show the patient's real plan, streak, trends and milestones"`.

---

### Task 5: Care screen + messaging + emergency contact + ATS

**Files:** `CareView.swift`, `Info.plist`.

- Therapist card: `care.therapist.name` + `"Your therapist · \(clinic)"`, initials avatar from the name; next visit `nextVisitAt` formatted `"EEE, MMM d · h:mm a"` else "Not scheduled yet"; **Call** enabled when `therapist.phone` present → `UIApplication.shared.open(URL(string: "tel:\(digits)"))`; remove the "Calling is available after…" line when enabled.
- `CareDetailView`: `.visit` → `care.highlights` as `visitCard`s (warning style when `kind` ∈ {asymmetry_worse, compensation, pain}); `.notes` → `care.notes` (newest first, "New" badge when `readAt == nil`, "Mira reads it" on the first); `.messages` → `store.messages` bubbles (`sender == "patient"` → mine; author `"\(therapistName) · \(date)"` / `"You · \(date)"`), composer enabled, Send → `store.send(body)`, scroll to bottom, live append via the `message` event; `.videos` keep the placeholder; `.contact` → `EmergencyContactView`.
- `EmergencyContactView`: on Save (and on onboarding edits when `state.isLinked`) → `state.updateMe(MeUpdate(emergencyContact: PairContact(...), alertTherapist:))`; HR limit line shows `care.hrLimit` ("Heart-rate limit from your care team: 130 bpm"); "Send a test alert" → `APIClient.testAlert()` and show `note`; replace "Calls and alerts are not active yet." with "Mira calls this person if you fall and don't respond." when linked.
- `Info.plist`: under `NSAppTransportSecurity` add `<key>NSAllowsArbitraryLoads</key><true/>` with an XML comment `<!-- Demo server is plain HTTP on a public IP (no TLS chosen for the hackathon). Remove when the server gets HTTPS. -->` (IP literals can't be listed in `NSExceptionDomains`). Keep `NSAllowsLocalNetworking`.

- [ ] Steps: implement; build + `swift test`; commit `"iOS: Care shows the therapist, notes, highlights and two-way messages; ATS allows the demo server"`.

---

### Task 6: Settings — reminders, device prefs, privacy, export, delete

**Files:** `SettingsView.swift`, `App/Reminders.swift` (new), `AppState.swift`, `Info.plist` (no new keys needed for local notifications).

- `Reminders.swift`: `enum Reminders { static func sync(_ prefs: MiraPreferences) async }` — requests authorization (`.alert, .sound`) when `dailyReminder && phoneReminder`, removes pending requests with identifier prefix `mira.reminder.`, and schedules one `UNCalendarNotificationTrigger(repeats: true)` per selected weekday at `reminderHour:reminderMinute` ("Time for your Mira session" / "Your plan is ready. Step on the mat and give Mira a thumbs up."). Called from `AppState` whenever `preferences` changes (debounced via `Task`).
- Reminders screen: enable the Daily reminder toggle and Phone notification toggle; "Mira says hello" stays disabled with copy "Coming with the next Mira device update."; "Apple Watch tap" disabled until Plan 4 (`state.watchPaired`, default false). Footer: "Reminders arrive on this phone at the time you choose." Settings row detail: `dailyReminder ? "Daily at \(time)" : "Off"`.
- Voice: on change → `state.updateMe(MeUpdate(devicePrefs: .object(["voice":…, "volume":…, "slower_speech":…, "captions": …])))` (debounced); footer "Applies to your phone and Mira's screen volume. Mira's device uses the clinic voice." Captions: enable "On Mira's screen too" → `device_prefs.captions`.
- Privacy: share toggle bound to `me.patient.shareByDefault` → `updateMe(shareByDefault:)`; "Download my data" → `APIClient.exportMe()` → write `mira-export-<date>.json` to a temp URL → `ShareLink`/`UIActivityViewController` sheet; remove "…not available yet" copy.
- Delete: "Delete everything" enabled → confirmation `.alert` → `APIClient.deleteMe()` → `state.wipeLocalData()` (forget token, clear UserDefaults keys `onboardingDraft.v1`, `miraPreferences.v1`, `hasCompletedOnboarding.v2`, `patientID.v1`, `serverAddress` stays) → back to onboarding. Copy: "Deleting removes your sessions, scores and notes from Mira's server and this phone. Your therapist keeps the plan they prescribed."

- [ ] Steps: implement; build + `swift test`; commit `"iOS Settings: reminders, device prefs sync, share default, export and delete"`.

---

### Task 7: Docs
README (care API, device_prefs, test-alert flag), NETWORKING.md/ONBOARDING.md (store, ATS note), `.env.example` (`ALLOW_TEST_CALLS=0`). Commit `"Docs: care API, device prefs, reminders"`.

### Task 8: Verification (once)
`make test`; `swift test`; `xcodebuild … build`; `node --test device/kiosk/tests/*.test.mjs`; simulator smoke against the local mock server: Home shows Maya's plan and streak, Progress shows the squat trend from simulated history, Care shows Dr. Okafor + a message round-trip (`POST /patients/maya/messages` with the admin token appears live), Settings toggles persist to `/me`.

## Self-review notes
- §6.1 ✔ T3; §6.2 ✔ T4; §6.3 ✔ T4 (+ milestones route T1); §6.4 ✔ T1 + T5; §6.5 ✔ T2 + T6. ATS exception is an addition the spec missed (needed for any real device against the Vultr server).
- Deviations to record: `_patient_for` moves to a shared module; videos remain a placeholder (spec non-goal).
