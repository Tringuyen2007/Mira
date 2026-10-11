# Welcome artwork

Source: [Figma frame 82:127](https://www.figma.com/design/UwyMjFR4Vbi0Y1Ej38qTlR/MIRA?node-id=82-127).

Original SVG exports are preserved here. Their root dimensions are unchanged.
The app uses faithful 3x PNG renders in `Mira/Assets.xcassets`, made using
`@resvg/resvg-js`. The supplied mascot PNG is bundled without modification.
These are separate decorative assets, not a screenshot of the screen.

| Asset | Figma layer | Design geometry |
| --- | --- | --- |
| WelcomeAurora | 82:128 | Full background, 430 × 932 |
| WelcomeOrbits | 82:136 | Full background, 430 × 932 |
| WelcomeHalo | I82:155;29:126 | 273.6 square at (78.2, 86) |
| WelcomeInnerRing | I82:155;29:127 | 237.6 square at (96.2, 104) |
| WelcomeMascot | I82:155;29:128 | 228.96 × 258.48 at (100.52, 101.84) |
| WelcomeSparkle28 | 82:159 | 28 square at (300, 92) |
| WelcomeSparkle16 | 82:162 | 16 square at (320, 130) |
| WelcomeSparkle12 | 82:165 | 12 square at (104, 300) |
| WelcomeSparkle10 | 82:168 | 10 square at (92, 120) |
| WelcomeLock | 82:176 | 52 square inside privacy card |

The mascot is the supplied static idle frame. The full 15-frame animation sheet
was not included in this frame's asset response.

Inter 4.1 Light, Regular, Medium and SemiBold are bundled in `Mira/Design/Fonts`,
including the original license, from the [official Inter release](https://github.com/rsms/inter/releases/tag/v4.1).
Font sizes follow Dynamic Type. Brand colors are named asset-catalog tokens;
their values match the supplied Figma context. Native iOS draws the status bar
and home indicator. Profile and subsequent screens are outside this design pass.
