# Onboarding implementation status

Welcome and privacy now match the combined Figma frame
[82:127](https://www.figma.com/design/UwyMjFR4Vbi0Y1Ej38qTlR/MIRA?node-id=82-127).
Profile, therapist-code entry and device connection now use the updated Hi-Fi
designs. Emergency contact and review now use the new Figma layouts, with inline
contact edits and Edit buttons returning directly to review.

Implemented: welcome and privacy → profile → therapist (deferred)
→ Mira device (deferred) → emergency contact and HR-limit placeholder → review.
Continue validates each required step; completion happens only after review.
Tapping Get started on the combined welcome/privacy screen acknowledges the
displayed privacy promise and moves to profile. It does not finish onboarding.
Existing drafts on the old privacy step show the same combined design. Draft
enum raw values stay stable. The four numbered design screens are followed by
emergency contact and review.
Profile displays feet/inches and pounds to match Figma. Edit activates inline feet/inches and pounds fields, without a measurement
sheet. Inputs and displayed values use whole numbers. Values convert internally to centimetres/kilograms for saved measurements. Phone validation checks format, not
whether the number exists or belongs to the contact. Users can move back, edit
the review, resume after relaunch, and reopen setup from Settings or Home.

The draft and completion flag are versioned local UserDefaults entries. Nothing
in this flow uploads the profile or contact, links a therapist, sends an alert,
or establishes a patient identity. The original welcome-only completion flag
does not skip the new flow. This is local prototype storage; a production data
retention/deletion and storage policy remains to be defined.

Backend work now in place: a documented therapist-code exchange (`POST /pair`,
README "Patient identity (phone)") that returns a patient identity and token —
never a code passed as `patient_id`; a typed prescription response including
targets and the care-team HR limit via `GET /me` (`me.patient.hr_limit`), with
404/410/429 handling for unknown/expired-or-used/rate-limited codes; and
profile/contact sync (`PUT /me`) with `alert_therapist` confirming emergency
alerts are configured. Emergency contact and `alert_therapist` now round-trip
live, not just at onboarding: `EmergencyContactView` calls `PUT /me` on every
edit once paired, and the HR limit shown there and on Care comes from
`GET /me/care`'s `hr_limit` (same value as `me.patient.hr_limit`, kept in sync
by the server) rather than a separate fetch — see README "Care API".

Backend work still needed:

- Documented `device_health` fields. Socket connectivity alone is not evidence
  that camera hardware is online.
- Calibration (vision) — `me.calibration` is still a placeholder
  (`{"status": "unknown", "calibrated": false}`) until Eric's engine wires it up.
  This is the only remaining gap from this list; everything else above
  (pairing, HR limit, emergency contact/alert sync) is now backed by the server.

Until these exist, the device step explicitly offers **Set up later**. Review
and Home say setup remains incomplete where relevant. HR limit comes from
`me.patient.hr_limit`; until a plan exists it displays **Not provided by your
care team yet** — the app never invents a limit.

Navigation: Setup is a dismissible modal; Live and Summary are dismissible
full-screen flows. Closing Live only dismisses its UI; it does not send Stop.
Progress and Care now have designed screens; their server data and full AR
calibration remain separate work. Summary
now has designed results, moments and check-in layouts, with unavailable states
for data and sharing that are not connected yet.

## Check it manually

1. Run Mira. A fresh installation starts at welcome; saved drafts resume at
   their existing step. To revisit welcome, use Settings → Review my details → Back. Completing the old welcome alone does not skip this flow.
2. Verify the welcome mascot, background, privacy card and Get started appear.
   Tap Get started and confirm profile opens with privacy acknowledged.
3. Enter height, mass, affected side and condition. Empty/invalid values block
   Continue. Height uses whole feet/inches (inches 0–11); weight uses whole pounds.
4. Enter an optional therapist code and choose Set up later on therapist/device.
5. Enter a contact name and phone. Confirm no HR number is fabricated.
6. On review, go back and edit a value. Quit and relaunch: the draft/step persist.
7. Yes, this is right. Reopen through Settings → Review my details and confirm
   values remain. Home continues to show pending therapist/device setup.
8. From Home, verify Setup presents a modal with Close; Live and Summary cover
   the tab shell and have Close buttons.

Automated validation/persistence tests run with the existing Swift package:

```sh
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
xcrun swift test --package-path device/ios
```

## Input regressions to check after design updates

- Height/weight values do not open a sheet. Tap the pencil Edit control, enter
  whole feet/inches or pounds inline, then use the single inline Done.
- No keyboard toolbar adds a second Done below Continue.
- Therapist entry has six cells, no overlapping placeholder, and a native
  caret positioned in the active cell. Check typing, backspace, arrow keys,
  tapping earlier cells, replacement and a six-character paste.
- Review Edit → Save changes returns directly to review and retains validation.

### Updated Figma flow

Welcome/privacy → profile (name, whole-number ft/in and lbs, condition) →
emergency contact → therapist → Mira → review. Review edits return to review
with Save changes. Existing stored measurements/contact details and step IDs
are preserved; older drafts without a name require entering one. The affected
side remains stored for compatibility but is absent from this design.
