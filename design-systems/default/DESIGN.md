# Neutral Modern

A clean, product-oriented default for when the brief names no particular mood. Good for tools, dashboards and utility pages.

## Overview
Calm, functional, quietly confident. No ornament. Content first, chrome second. When in doubt, subtract: fewer boxes, less chrome, more space.

## Colour
Use the custom properties in `tokens.css`; never write a hex value that is not listed here.
- **Background** `--bg` `#fafafa`; **surface** `--surface` `#ffffff` for cards and dialogs.
- **Foreground** `--fg` `#111111`; **muted** `--muted` `#6b6b6b` for captions and secondary text.
- **Border** `--border` `#e5e5e5`.
- **Accent** `--accent` `#2f6feb` (cobalt) for the primary action, links and at most one hero element per screen; `--accent-on` `#ffffff` is the label on it.
- **Status** `--success` `#17a34a`, `--warn` `#eab308`, `--danger` `#dc2626`.
- Never pure black text and never a pure white page background.

## Typography
- Headings and body both use `--font-sans` (a system sans stack); headings weight 600, body 400. Code uses `--font-mono`.
- Scale in px: 12, 14, 16, 20, 24, 32, 48, 64 (`--text-xs` to `--text-4xl`).
- Line height 1.5 for body, 1.2 for headings. Letter spacing -0.01em on sizes of 32px and up.
- No more than three type sizes on one screen. Sentence-case headings; title case only for names.

## Spacing and layout
- Spacing steps are multiples of 4px (`--space-1` to `--space-12`).
- 12-column grid, 1200px maximum width, 24px gutters. Tablet (640 to 1023px): 8 columns, 16px gutters. Phone (under 640px): 4 columns, 12px gutters.
- Sections have 80px of space above and below on desktop, 48px on tablet, 32px on phone.
- The hero is 40 to 60vh and its content sits toward the top, never centred vertically.
- Whitespace is the main separator. Use a divider only between unrelated top-level sections.

## Components
- **Buttons:** 8px radius, 10px block and 16px inline padding. Primary is an accent fill with a white label; secondary is a 1px border on a transparent fill.
- **Cards:** white, 1px border, 12px radius, 20px padding, no shadow.
- **Inputs:** 1px border, 8px radius, 10px vertical padding; the border turns accent on focus. No shadow.
- **Links:** accent, no underline, underlined on hover.
- Depth has two levels only: flat (the default) and raised (menus, dialogs: 2px offset, 8px blur, foreground at 8%). No glass, no neumorphism, no gradients.

## Motion
Short and quiet. 150ms for hover and press, 240ms for panels and dialogs, with `--ease-standard`. Fade or move a few pixels; never bounce or spin. Respect `prefers-reduced-motion` by removing movement.

## Voice
Plain, direct and calm. Say what happened and what to do next, in the fewest words. Sentence case, no exclamation marks, no emoji in product text. Buttons name the action ("Save changes", not "Submit").

## Rules for the designer
- Use the accent sparingly: one hero accent and one call to action per screen.
- Reference tokens with `var(--name)` and paste the `:root` block from `tokens.css` into the page's style.
- If the request needs a colour outside this palette, use the closest token and leave a comment in the HTML saying so.
