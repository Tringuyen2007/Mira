# Mira LCD — static web copy

A plain web page with the same frontend as the LCD kiosk (`device/kiosk/`, built from the Figma "LCD Hi-Fi"
frames), showing demo content instead of the live session. No server data, no login, nothing private, so it
can be shared and edited freely.

Served by the Mira server at `/lcd-web/` (on Vultr: http://45.76.255.253:8000/lcd-web/). Open a screen
directly with `?screen=greeting|gesture|countdown|cue|rep|paused|setDone|fall|calling|idle`; use the buttons,
the ←/→ keys, or Play to cycle through them. `calling` is the fall screen after the alert escalates: the live
call status line ("No response from Maya. Calling the emergency contact.") and the emphasised CALL 911 pill.

There is no audio. Whenever a screen with a caption is shown, Mira "speaks" for 2 s (`data-speaking="true"` on
`#stage`): the mascot bobs and the caption's voice bars move, as on the kiosk while a `say` line plays. The
gesture screen waves, and the rep / countdown numbers pop when they change between screens.

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
