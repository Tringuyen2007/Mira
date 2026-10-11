# Updated iPhone UI

Source: [MIRA iPhone Hi-Fi](https://www.figma.com/design/UwyMjFR4Vbi0Y1Ej38qTlR/MIRA?node-id=1-3), inspected October 10, 2026.

Implemented layouts: welcome/privacy; profile; therapist; device; five setup
steps; Home and weekly progress; Live, paused, pain and end-session states;
results, moments to review and check-in; Progress trends/balance/goals/calendar;
Care and its five detail screens; Settings and its six detail screens; onboarding
review and emergency contact. Inter stays bundled. Decorative assets
are Figma exports, rendered from their original SVGs at 3× where needed. Original
exports and their source URLs are in `DesignSources/iPhone`; `AssetMap.json` maps
Figma slots to asset-catalog names. `Illustrations.json` records only decorative
layer geometry; text, controls and navigation are SwiftUI.

Run the **Mira** scheme from `device/Mira.xcworkspace` for the normal user flow.
Your saved profile is preserved. The Watch targets are unchanged. The latest designs for Progress, Care, Settings, emergency contact and review
are integrated.

## Preview every design without changing your profile

In Xcode: Product → Scheme → Edit Scheme → Run → Arguments → Arguments Passed
On Launch. Add `--design-preview` and one route from the list below as separate
arguments. Run Debug. Remove both arguments to return to the normal user flow.
Design routes use a separate defaults suite and do not connect to a server.
These routes do not exist in Release.

```
welcome  profile  therapist  device
findmat  tripods  fixes  body  save
home  week
live  paused  pain  stop
results  flags  send
trends  balance  goals  calendar
care  visit  notes  messages  videos  contact  safety  review
settings  rerun  voice  captions  reminders  privacy  delete
```

Preview numbers, Maya, Dr. Lee and sample exercises are design fixtures.
The normal app shows pending/unavailable states instead of these sample values.
Preview summary playback uses local text-to-speech. Replay is disabled because
no recorded movement timeline exists yet. Check-in sliders, note and consent
are local to the screen; Send remains disabled and does not upload anything.

## Behavior still requiring integration

- Therapist-code exchange, patient identity, typed prescriptions and HR limits.
- Hardware pairing and a defined `device_health` schema.
- AR mat recognition, tripod transforms, body framing and saved calibration.
  Surface scanning retains the existing ARKit implementation. The new setup
  guide is explicitly illustrations only and cannot mark calibration complete.
- Pose coordinate contract and movement renderer; session summary/history data.
- Resume semantics, rated pain reports, movement replay and therapist sharing.

Existing Start/Pause/Skip/Stop/Pain/I'm OK commands remain wired to the event
socket. Resume does not send an invented command. Sending a command is not
presented as confirmation that the server has applied it. Closing a Live flow
only dismisses its UI and does not send Stop.

## Verify

Build Debug and Release. Run the existing Swift package tests. In the simulator,
check the routes above, then remove launch arguments and verify normal profile
validation, persistence, pending states, modal Setup and full-screen Live/Summary.
Use the mock server described in `NETWORKING.md` to verify connected metrics,
commands, and the offline/reconnection state.

## Latest update

Profile Edit controls use the new pencil icons and retain inline whole-number
ft/in and lbs entry. No bottom keyboard Done is added. Therapist entry retains
its single native six-cell input and mapped caret. Review Edit returns directly
to review after Save changes. Existing draft encoding remains compatible.

Voice/volume/speed and caption choices persist locally, independently of the
saved profile. Voice preview uses on-device speech; Live captions respect the
caption visibility and size preference. Contact name/phone/relationship save
locally. Calendar month navigation works; history/trends/milestones use fixtures
only in design previews. The normal app shows pending data.

Reminders, device preference sync, calling, messages, videos, sharing, data
export and server deletion still need integrations. Unsupported actions are
disabled; their screens do not claim success. No data is deleted by the preview.
Developer networking tools now require the explicit Debug launch argument
`--network-debug` and remain absent from Release.

## October 10 follow-up

Profile now includes a locally saved name and side-by-side height/weight fields.
Editing measurements expands the fields inline; feet, inches and pounds stay
whole numbers. The surgery-side value is retained in saved drafts but is no
longer requested by this Figma screen. Condition retains its menu and custom
entry, with the new pencil affordance.

Onboarding presents five numbered steps: welcome/privacy, profile, emergency
contact, therapist, Mira, followed by Review. Persisted step IDs remain unchanged.
Old drafts still decode; drafts without a name must add one before completion.
Emergency contact has its own onboarding heading and spacing; `safety` previews
that screen, while `contact` previews the Care version. Review shows Name and
orders contact before therapist and device, with the new icon-disc colors.
The volume slider uses the updated outlined mist track and supports dragging
and VoiceOver adjustments. The previous input, alert-toggle and completion
fixes are retained. Alerts and pairing still require server integration.
