# Kiosk assets

Everything the LCD page draws comes from these local files. Nothing is loaded from another origin at runtime.
Source: Figma file `UwyMjFR4Vbi0Y1Ej38qTlR`, page "LCD Wireframe", hi-fi components `LCD Hi-Fi / *`.

## Mira (`mira/`)

| File | Figma component | Size | How it was made |
|---|---|---|---|
| `idle.png` | `24:138` Mood=Idle | 720×800 (2× of 360×400) | composed from layers, see below |
| `worried.png` | `38:282` Mood=Worried | 720×800 | composed from layers |
| `cheering.png` | `38:286` Mood=Cheering | 720×800 | composed from layers |
| `sleeping.png` | `38:290` Mood=Sleeping | 720×800 | composed from layers |
| `celebrating.png` | `38:294` Mood=Celebrating | 720×800 | composed from layers |

Draw each one at **360×400 CSS px** at the Mira slot (screens put the instance at x=25, y=40).

Each mood component has three layers, and each PNG holds all three on a transparent background, because the
screen frames (e.g. Greeting `24:232`, Rep `36:227`) show all three:

1. **Halo**: a 380×380 circle at (-10, 0), radial gradient #FFFCF7 @ 0.95 → #FFF7F2 @ 0.55 (50 %) → #FFF7F2 @ 0.
   The 10 px that overflows the 360 px slot on each side is cut off. Opacity there is under 6 %, so you can't
   see the cut.
2. **Inner ring**: a 330×330 circle at (15, 25), 1.5 px white stroke @ 0.85.
3. **Mascot**: 318×359 at (21, 22).
   - Idle: the raw PNG fill (420×474, palette + alpha), resampled to 636×718 with Lanczos.
   - Other moods: a raw JPEG colour fill (object-fit: contain) masked by a 636×718 alpha mask, as in Figma.
     The ground shadow and mood extras (motion marks, zZ, sparkles and petals) are part of these bitmaps.

Figma's PNG export flattens onto the canvas grey (#F4F1F3), so it can't be shipped as is. The layers were
downloaded with `download_assets` and composed with Pillow in a throwaway venv. As a check, the composed PNGs
were put on #F4F1F3 and compared with Figma's 2× export of each component: the mean difference is under
1/255 per channel, and the only differences are resampling at edges.

**For the designer:** the source bitmaps are smaller than the 2× slot, so the 2× PNGs are upscaled (Figma
upscales them the same way):

| Mood | Source bitmap | Upscale to the 2× slot |
|---|---|---|
| idle | 420×474 PNG | ×1.51 |
| worried | 183×206 JPEG | ×3.48 |
| cheering | 240×234 JPEG | ×2.65 |
| sleeping | 225×185 JPEG | ×2.83 |
| celebrating | 260×205 JPEG | ×2.45 |

The non-idle moods look soft. Uploading the 750×750 animation frames named in the component description would
fix this.

### Animation

`mascot.json` lists one still per mood. If you add a richer animation later, a mood can carry a frame list:

```json
"idle": {"src": "idle.png", "frames": ["idle/00.png", "idle/01.png"], "fps": 12, "loop": true}
```

## Icons (`icons/`)

These are SVGs from `download_assets`. `width`/`height` were removed in favour of `viewBox`, layer-name ids
were dropped, and clip-path ids were made unique (`mira-<name>-clip`) so the files can be inlined. Fills and
strokes are as in Figma.

| File | Figma node | viewBox | Colours / notes |
|---|---|---|---|
| `thumbs-up.svg` | `35:132` (Gesture Thumbs up `35:130`) | 60×60 | stroke #1C1A22, 4 px; drawn 60×60 in a 136 px white halo button |
| `raise-hand.svg` | `35:138` (Gesture Raise hand `35:136`) | 60×60 | stroke #1C1A22, 4 px |
| `sparkle.svg` | `24:135` Sparkle | 24×24 | fill white; component adds `drop-shadow(0 0 6px rgba(143,186,204,.7))` |
| `heart.svg` | `24:124` (Heart Rate Chip `24:123`) | 26×26 | fill #9F3462 |
| `voice.svg` | `24:129` Waveform (Caption Bar `24:127`) | 28×28 | fill white, last bar @ 0.6; same in screen instance `36:257` |
| `phone.svg` | `57:291` Icon · phone | 56×56 | fill white (drawn on the red call button) |
| `check.svg` | `57:288` (Balance bar `35:145`; same in `36:282`) | 22×22 | disc #0F6E56, tick white 2.4 px |
| `warning.svg` | `57:283` (Balance bar `35:153`; same in `36:218`) | 28×26 | #B42318 triangle, white mark |
| `pause.svg` | `36:349`/`36:350` bars in Paused halo `36:348` | 32×34 | Figma has no vector here, just two rectangles: 10×34, radius 5, gap 12, #1C1A22. Made from those specs. |

## Font (`fonts/`)

- `Inter-Variable.woff2`: `web/InterVariable.woff2` from the official **Inter v4.1** release,
  `https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip`.
  - Zip SHA-256: `9883fdd4a49d4fb66bd8177ba6625ef9a64aa45899767dde3d36aa425756b11e`. The release page publishes
    no checksum to compare against.
  - woff2 SHA-256: `693b77d4f32ee9b8bfc995589b5fad5e99adf2832738661f5402f9978429a8e3` (352,240 bytes).
- `OFL.txt`: the zip's `LICENSE.txt` (SIL Open Font License 1.1).
