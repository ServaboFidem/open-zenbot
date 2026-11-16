# Typography rules

The design system decides which fonts; these rules decide how they behave.

## Scale

Use a 1.2 or 1.25 ratio and at most 6 to 8 sizes in the file.

| Role | Size |
| --- | --- |
| Display | 48 to 72 px |
| H1 | 32 to 48 px |
| H2 | 24 to 32 px |
| H3 | 20 to 24 px |
| Body | 15 to 18 px |
| Small | 13 to 14 px |
| Caption | 11 to 12 px |

## Line height

| Size | Latin | CJK |
| --- | --- | --- |
| 32 px and up | 1.0 to 1.2 | 1.3 to 1.4 |
| Body, 15 to 18 px | 1.5 to 1.6 | 1.7 to 1.8 |
| 14 px and under | 1.5 | 1.7 |

CJK glyphs fill the em box, so Latin leading makes lines touch. This applies to
every heading, the cover title included. Never use negative tracking on CJK
text. When an English kicker sits over a Chinese headline, give the tight Latin
values to the Latin element only.

## Letter-spacing

The most skipped rule, and the most reliable tell.

| Context | Tracking |
| --- | --- |
| Body, 14 to 18 px | `0` |
| Small, 11 to 13 px | `0.01em` to `0.02em` |
| UI labels and buttons | `0.02em` |
| ALL CAPS | `0.06em` to `0.1em`, always |
| Headings 32 px and up | `-0.01em` to `-0.02em` |
| Display 48 px and up | `-0.02em` to `-0.03em` |

## Pairing and weight

- At most two typefaces: display and body, or one variable face.
- Always end `font-family` with a fallback chain. Fonts must be inline `data:`
  fonts or system fonts; the preview loads nothing from the network.
- Never set `system-ui` alone on a heading.
- Use three weights: read (400), emphasise (500 to 550), announce (590 to 600).
  Weight 700 and above is rarely needed.

## Measure

Keep body lines to 50 to 75 characters: `max-width: 65ch`.

## Common mistakes

- ALL CAPS with tracking under `0.06em`.
- Display text with no negative tracking (Latin only).
- CJK display text at leading of 1.2 or less, or with negative tracking.
- More than three type sizes above the fold.
- Serif and slab mixed on one screen with no clear role split.
- Justified body text. It makes rivers.
