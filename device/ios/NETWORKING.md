# Testing Mira networking

The iOS app now has REST requests, `/ws/live`, `/ws/events`, and `/ws/pose`,
automatic reconnect, persisted server settings, and a shared `LiveSessionStore`.
The current Live screen is a small integration surface; the full planned Live
experience (avatar, safety sheet, haptics, pain entry) is separate work.

Server entry, connection checks, stream status, message counts, and decoding
diagnostics are compiled into **Debug builds only** and require the explicit
launch argument `--network-debug`. With that argument, open Settings → Developer
connection tools. Without it, the app shows the user design. Diagnostics are
absent from Release builds. Home and Live show only a plain-language availability
message when needed, without server addresses or protocol details. A final
customer-facing pairing/provisioning flow is still needed to configure a server
on a fresh installation; hiding developer controls does not implement pairing.

## Quick manual test (Simulator)

From the repository root, start the development mock in a Terminal:

```sh
python3 tools/mira_network_mock.py
```

No Python packages are needed. Leave this Terminal running.

1. Open `device/ios/Mira.xcodeproj` in Xcode, select an iPhone simulator, and Run
   with the **Debug** build configuration. In Product → Scheme → Edit Scheme →
   Run → Arguments, add `--network-debug`. Remove design-preview arguments.
2. Complete onboarding. Open **Settings → Developer connection tools**.
3. Enter `http://localhost:8000`, then tap **Save & connect**.
4. Expect **Mira server online**, all three streams online, and an increasing
   **Messages received** count.
5. Tap **Test REST connection**. Expect **REST OK · calibration status received**.
6. Open **Home → Live session**. Expect changing angle/rep values.
7. Tap **Pause**. Expect **Paused**, the rep count to stop, and the cue
   **Mock received: pause**. The current paused UI leaves Resume disabled until the real server defines
   resume behavior; restart the mock and reopen Live for another run.
8. Press Ctrl-C in the mock Terminal. Expect **Mira is unavailable right now.**
   and **Live updates are paused.**, with the last rep count still visible.
   Debug Settings additionally shows **Mira server offline · reconnecting…**.
   Detection can take up to about 15 seconds for a silent connection failure.
9. Run the same Python command again. Expect reconnection without tapping any
   button. Retries back off from approximately 1 second to a maximum of 30.25 seconds.
10. Tap **Disconnect** in Settings. Restarting the mock must not reconnect until
    you tap **Save & connect**. Backgrounding the app suspends sockets; returning
    to the foreground reconnects if the connection is enabled.

To force a brief disconnect without restarting the mock:

```sh
curl -X POST http://localhost:8000/test/drop
```

The mock's `/test/state` route shows received commands and connection count.
These test routes exist only in this development mock.

## Testing on a real iPhone

Run the mock on your Mac with LAN access:

```sh
python3 tools/mira_network_mock.py --host 0.0.0.0
scutil --get LocalHostName
```

Connect the Mac and iPhone to the same trusted Wi-Fi. Use
`http://<LocalHostName>.local:8000` in Settings (replace the placeholder with the
command's output). `localhost` on a physical iPhone refers to the phone, not the
Mac. Allow Mira's Local Network permission and any macOS firewall prompt for
Python. Guest Wi-Fi/client isolation can prevent devices from reaching each other.
The app's Info.plist includes the local-network usage description and the local
HTTP ATS exception; HTTPS addresses automatically use secure WebSockets (`wss`).

## Automated tests

From the repository root, with the mock running:

```sh
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
MIRA_MOCK_URL=http://127.0.0.1:8000 \
xcrun swift test --package-path device/ios
```

This compiles the same networking and app-state sources used by the iOS app.
It checks JSON keys and every section-16 event family, unknown-event preservation,
invalid messages, address validation, offline command rejection, REST success,
HTTP errors, empty 204 responses, real streams, command delivery, disconnect,
retained data, reconnection, and explicit disconnect stopping retries.

Without `MIRA_MOCK_URL`, only the real-server integration test is skipped. Use a
dedicated development mock for this test: it intentionally disconnects its clients.
The `DEVELOPER_DIR` prefix is needed on machines whose active command-line tools
aren't set to Xcode; it does not change the machine's global configuration.

## Contract to confirm with Tri before H3

**This mock is a provisional fixture, not Tri's mock.** At implementation time,
the repository's `server/main.py`, `server/hub.py`, and REST route files were empty,
and no `server/samples/` files existed. The spec lists shapes but does not define
all field types or an event envelope.

The client currently expects:

- REST base address with no path suffix, such as `http://laptop.local:8000`.
- Untagged JSON objects on `/ws/live` and `/ws/pose`, with snake_case field names
  exactly as in section 16. Pose has 33 arrays of three numbers and 33 confidence
  values. `t` is numeric; percentages range conceptually from 0 to 100.
- Flat events on `/ws/events`, with a `type` discriminator. For example:

```json
{"type":"cue","id":"balance","text":"Even out your weight"}
```

- Commands on the same events socket:

```json
{"type":"command","name":"pause","payload":{}}
```

- String IDs, flags, rep quality, phases, gesture names, and alert kind/step.
  Nullable/missing `countdown`, `main_angle`, `cue_id`, and greeting `audio_id`.
- No authentication is specified yet. Confirm authentication, event wrapping,
  response bodies, and field types before connecting to a deployed server.

`Models.swift` has typed Codable models for every specified section-16 field
shape, including optional Watch messages and Copilot requests/responses. Events
with an unknown `type` are retained. A malformed known message is reported in
Settings and leaves the last valid data intact.

REST response schemas not given in the spec (sessions, calibration, progress,
prescriptions, proposals, frames), arbitrary command payloads, and unspecified
`summary_ready`/`device_health` bodies use lossless `JSONValue` until Tri supplies
schemas. These are not claims of finalized domain models. REST methods for all
section-16 routes plus the additional Josie routes are available in `APIClient`.

Before H3, obtain Tri's server address, startup command and sample JSON; compare
them with these assumptions; adjust `Models.swift`/`APIClient.swift`; then repeat
the manual connection, command, outage and recovery test against his actual mock.

## Behavior and limits

- A successful WebSocket ping marks a stream connected; 5-second heartbeats have
  a 10-second deadline. Reconnect uses bounded exponential backoff with jitter.
- Overall online status requires all three streams. Settings shows each stream
  individually; the REST test is a separate request, not an inferred health check.
- Automatic reconnect preserves data. Changing the saved server clears old data.
- Commands are rejected while offline and never queued or replayed. A successful
  socket send does not itself prove server execution; observe the returned state
  or event. REST mutations are also never automatically retried.
- This layer does not add Watch connectivity, audio playback, haptics, emergency
  notifications, Bonjour discovery, or a full safety workflow. Manual server
  entry is supported. No messages are sent to contacts by the mock.
