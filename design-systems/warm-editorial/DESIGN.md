# Warm Editorial

A serif-led magazine look: a terracotta accent on warm off-white paper. Good for long-form, editorial and brand-led pages.

## Overview
Warm, unhurried, magazine-like, like an interview column online. Generous whitespace, long-form readability, restrained chrome. Playful but never novelty. Lead with type and whitespace; borders and shadows are subtractive.

## Colour
Use the custom properties in `tokens.css`; never write a hex value that is not listed here.
- **Paper** `--bg` `#faf7f2`; **surface** `--surface` `#fffdf8` for raised cards only.
- **Ink** `--fg` `#1c1a17` (near black, slightly warm); **muted** `--muted` `#6f655c` for dates and metadata.
- **Accent** `--accent` `#c0512f` (terracotta) for links, the primary action and one hero element per page; `--accent-on` `#fffdf8` is the label on it.
- **Secondary** `--forest` `#2f5b4f` for dividers and tags only.
- **Border** `--border` `#e2d9cc`.
- **Status** `--success` `#3f7a4a`, `--warn` `#b87420`, `--danger` `#b33a3a`.
- Never pure black or pure white anywhere the reader sees.

## Typography
- Headings use `--font-display` (a system serif stack: Georgia first). Body uses `--font-body` (a system sans stack). Code uses `--font-mono`, for code only.
- Scale in px: 12, 14, 17, 20, 28, 42, 64, 88 (`--text-xs` to `--text-4xl`). Body is 17px.
- Line height 1.6 for body, 1.1 for display. Letter spacing -0.02em above 40px, normal elsewhere.
- Use the serif for numbers that matter (prices, statistics).
- Title case for H1 and H2; sentence case for H3 and below.

## Spacing and layout
- Spacing steps are multiples of 4px (`--space-1` to `--space-12`).
- 12-column grid, 1180px maximum width, 36px gutters on desktop, 24px on tablet, 16px on phone.
- Body sections have 112px above and below on desktop, 80px on tablet, 56px on phone.
- Heroes are at least 72vh on desktop (60vh tablet, 50vh phone), content toward the top, never centred vertically.
- Prefer one hero and three to five body sections over eight. Editorial means restraint.
- Corner radius is never below 8px or above 24px.

## Components
- **Buttons:** flat fill, 12px radius, 14px block and 20px inline padding. Primary is terracotta with a paper-coloured label; secondary is a 1px ink outline on a transparent fill.
- **Cards:** paper background, 1px border, 16px radius, 24 to 32px padding. No shadow except on hover (2px down, 16px blur, ink at 6%).
- **Inputs:** underline only, a 1px muted baseline that turns terracotta on focus, 16px vertical padding. No shadows.
- **Links:** terracotta with a 1px underline at 40% opacity; on hover the underline goes and a faint terracotta background appears.
- Depth has two levels, flat and raised. No gradients, no glass, no neumorphism. One accent element per page; when the hero is terracotta, other buttons are ink only.

## Motion
Slow and soft, in keeping with print. 150ms for hover, 240ms for reveals, with `--ease-standard`. Fade in or rise a few pixels; no bounce, parallax or looping animation. Respect `prefers-reduced-motion` by removing movement.

## Voice
Warm, literate and human, written in full sentences. Concrete over clever, no hype words, no emoji in product copy. A short headline on half the screen is correct. For "serious" briefs lean harder on the serif and whitespace; for "modern" or dense interfaces this is the wrong system.

## Rules for the designer
- Colour tokens are fixed: do not invent hex values. If a request needs one, use the closest token and leave a comment in the HTML saying so.
- Reference tokens with `var(--name)` and paste the `:root` block from `tokens.css` into the page's style.
