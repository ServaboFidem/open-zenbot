# Anti-slop rules

Checkable rules that separate a designed page from default model output.

## Never ship these

1. **Default indigo as the accent**: `#6366f1`, `#4f46e5`, `#4338ca`, `#3730a3`,
   `#8b5cf6`, `#7c3aed`, `#a855f7`. Take the accent from the design system or the
   brief; otherwise pick a colour with a reason.
2. **A two-stop "trust" gradient on the hero** (purple to blue, blue to cyan,
   indigo to pink). A flat surface and deliberate type beat it.
3. **Emoji as feature icons** (sparkles, rockets, targets, lightning, fire,
   bulbs) in headings, buttons or list items. Use 1.6 to 1.8 px monoline inline
   SVG with `currentColor`.
4. **A rounded card with a coloured left border.** Drop the radius or the border.
5. **Invented metrics**: "10x faster", "99.9% uptime", "3x more productive". Use
   real figures from the user or a clearly labelled placeholder.
6. **Filler copy**: lorem ipsum, "Feature one / two / three", "placeholder text".
   An empty section is a composition problem; solve it with layout, not words.
7. **Hardcoded system fonts on display text** (`system-ui`, Inter or Roboto alone
   on headings) when the brief or system names a display face.

## Fix if you can

- The stock sequence Hero, Features, Pricing, FAQ, CTA with no variation. Add one
  unexpected section: a full-bleed quote, pricing set against the status quo, a
  small inline demo.
- Placeholder image sites (unsplash, placehold.co, picsum). They also cannot load
  in the preview. Use a flat tinted block, an inline SVG or a picture file added with `design_add_image`.
- More than about 12 raw hex values outside `:root`: the tokens were ignored.
- `var(--accent)` used six or more times on one screen. Cap it at two.

## Polish

- Decorative blob or wave backgrounds that mean nothing.
- Perfect symmetry with no tension. Alternate dense and airy sections.
- Sections or elements with no `data-zen-id`, which the user cannot point at, and text elements with no id, which a writer cannot reach.

## Adding soul

Spend the last 20% on:

- One bold visual move: a type choice, one colour decision, an unexpected
  proportion.
- Voice. "Start tracking" beats "Get started".
- One micro-interaction worth remembering: a button that moves 2 px on press.
- One detail only someone who used the product would add, such as a keyboard
  shortcut hint or a status label in the product's own words.

Test: could a stranger tell which product this is from a screenshot? If not, it
is a template.
