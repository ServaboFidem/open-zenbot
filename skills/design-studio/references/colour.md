# Colour rules

The design system supplies the palette; these rules cover how to use it.

## Palette layers

Plan all four before writing CSS.

| Layer | Share of pixels | Tokens |
| --- | --- | --- |
| Neutrals | 70 to 90% | `--bg`, `--surface`, `--fg`, `--muted`, `--border` |
| Accent (one) | 5 to 10% | `--accent` only; never a second accent |
| Semantic | 0 to 5% | `--success`, `--warn`, `--danger` |
| Effect | under 1% | gradients, glows; rarely justified |

## Accent discipline

Accent overuse is the biggest readability failure in generated pages.

- At most **two** visible uses of `--accent` per screen, such as one eyebrow chip
  and one primary button.
- Links count. If a CTA is on screen, set links in `--fg` with an underline.
- Hover and focus rings count too.

## Contrast gates

| Pair | Minimum |
| --- | --- |
| Body text (up to 16 px) on its background | 4.5:1 |
| Large text (over 18 px, or 14 px bold) | 3:1 |
| UI components against neighbouring surfaces | 3:1 |

If the brand colour fails as text, use a darker 600-level shade for text and keep
the bright one for fills.

## Dark themes

Avoid pure black and pure white; both vibrate.

| Token | Dark | Light |
| --- | --- | --- |
| Background | `#0f0f0f` | `#fafafa` |
| Foreground | `#f0f0f0` | `#111111` |

On dark surfaces use a semi-transparent white border, `rgba(255,255,255,0.08)`,
over a solid dark one.

## Naming

Name tokens by purpose, never by hue: `--accent`, `--success`, not `--blue-500`.
A hue name blocks theming.

## Anti-defaults

- Indigo `#6366f1` is the surest generated-page tell. Use it only if the user asks.
- A purple-to-blue hero gradient is the second. Use a flat surface and type.
- Gradients should separate levels (header from body, primary from secondary
  action), not fill empty space.
