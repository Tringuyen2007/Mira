# Mira LCD — static web copy

A plain web page with the same frontend as the LCD kiosk (`device/kiosk/`, built from the Figma "LCD Hi-Fi"
frames), showing demo content instead of the live session. No server data, no login, nothing private, so it
can be shared and edited freely.

Served by the Mira server at `/lcd-web/` (on Vultr: http://45.76.255.253:8000/lcd-web/). Open a screen
directly with `?screen=greeting|gesture|countdown|cue|rep|paused|setDone|fall|idle`; use the buttons, the
←/→ keys, or Play to cycle through them.

## Editing

| What | Where |
|---|---|
| Layout and text of each screen | `index.html` (one `<section class="screen" data-screen="…">` per screen) |
| Look (positions, sizes, colours) | `css/kiosk.css`, colours/fonts in `css/tokens.css` |
| Demo copy and numbers (captions, reps, heart rate…) | `SCREENS` in `js/demo.js` |
| The switcher bar | `css/web.css`, buttons built in `js/demo.js` |
| Mira artwork | `assets/mira/` (`mascot.json` maps moods to images) |

`js/state.js`, `js/view.js` and `js/mascot.js` are copies of the kiosk's renderer; this folder doesn't change the
real LCD page. To preview locally: `cd device/lcd-web && python3 -m http.server 8766`, then open
http://127.0.0.1:8766/. To publish, merge to `main` and run `tools/deploy_vultr.sh`.
