---
name: Design studio
slug: design-studio
use: When asked to design a web page, screen, layout or UI as a single HTML file shown in the studio preview.
inputs:
  - brief: what to design and who it is for
  - design_system: colours, type and spacing to follow (optional)
access:
  - files
steps:
  - Read the brief. If the audience, tone or content is open and no answers have come back yet, ask once with a question form, then stop.
  - Choose a type scale, a palette (neutrals, one accent, semantic colours) and a layout before writing any markup.
  - Write one HTML file with everything inline and a stable data-zen-id on every meaningful element.
  - Check the file against the references below before attaching it.
validation:
  - No external requests of any kind; styles, scripts and fonts are inline, and pictures are files added with `design_add_image`.
  - Every meaningful element has a data-zen-id, and ids survive revisions.
  - Contrast, focus, labels and the five states hold up (see the references).
return: The attached design file, with a short note on what was made or changed.
band_width: 0.1
---
# Design studio

Craft rules for single-file HTML designs. Read the reference that matches the
decision you are making; do not load all five for a small tweak.

| Reference | Read it when |
| --- | --- |
| `references/anti-slop.md` | Starting a design, or checking it looks made rather than generated |
| `references/typography.md` | Choosing sizes, leading, tracking and fonts |
| `references/colour.md` | Choosing a palette, an accent, dark or light themes |
| `references/accessibility.md` | Before attaching: contrast, focus, labels, keyboard, structure |
| `references/states.md` | Any list, form, table or panel that fetches or takes input |

Working rules:

- The preview blocks all network access. Inline every style, script and font.
  Keep pictures as files: add a photo with `design_add_image` and reference it
  as `<img src="images/<name>">` (with the quotes); never write a picture as
  `data:` text, and read `attach_file`'s note on any picture not embedded.
  Draw icons and illustrations as inline SVG with `currentColor`.
- Put tokens on `:root` (`--bg`, `--surface`, `--fg`, `--muted`, `--border`,
  `--accent`) and use them everywhere. A design system in context overrides the
  defaults in the references.
- Give each section, heading, button, card, image and nav item a kebab-case
  `data-zen-id`, and keep it when you revise.
- Aim for about 80% proven pattern and 20% one distinctive choice.
