# Accessibility baseline

Aim for WCAG 2.2 AA. It clears the WCAG 2.1 AA floor most laws cite.

## Contrast

| Pair | Minimum |
| --- | --- |
| Normal text | 4.5:1 |
| Large text (18 pt regular, about 24 px, or 14 pt bold, about 18.5 px) | 3:1 |
| UI components and graphics | 3:1 |
| Focus indicator against its surroundings | 3:1 |

Limits are inclusive but never rounded: 2.999:1 fails. 18 px regular text is not
"large"; it needs 4.5:1.

## Target size

AA needs 24 x 24 CSS px (2.5.8). Aim for 44 x 44 on primary actions (2.5.5, AAA).
Small targets pass only with 24 px of clear space around them.

## Focus

- Never `outline: none` without a replacement. Use `:focus-visible` with a
  visible ring of at least 2 px and 3:1 contrast.
- Tab order follows reading order. Never use `tabindex` above 0; fix the DOM.
- A modal traps focus until Escape or its close button. That is correct.

## Forms

- Every input has a visible `<label for>`. A placeholder is not a label.
- Wire hints and errors with `aria-describedby`; set `aria-invalid="true"` on a
  failed field; give the message `role="alert"`.
- Do not ask twice for data the user already gave in the same flow.

```html
<label for="email">Email</label>
<input id="email" type="email" required
       aria-describedby="email-hint email-error" aria-invalid="true">
<span id="email-hint">Used for receipts only.</span>
<span id="email-error" role="alert">Email must include @ and a domain.</span>
```

## Structure and keyboard

- `<html lang="...">` always; `lang` on inner elements that switch language.
- Use `<header>`, `<nav>`, `<main>`, `<aside>`, `<footer>`, not divs with roles.
- One `<h1>`; do not skip levels. Size and level are independent: style the level
  you mean.
- Native controls first. `<button>` for actions, `<a href>` for navigation. A bare
  `<a>` without `href` is not a link. `<div role="button">` means rebuilding
  everything a button gives for free.
- Everything reachable and operable by keyboard; no trap except modals.
- Images: `alt` text for content, `alt=""` for decoration, `aria-label` on
  icon-only buttons, a text alternative for every chart or data SVG.

## ARIA

Order of preference: a native element, then a native element under custom
visuals, then an ARIA authoring pattern verbatim. Never invent ARIA. Pages with
ARIA average more errors than pages without it.

## Motion

Honour `prefers-reduced-motion`. Never flash more than three times a second.

## Common mistakes

- Calling 44 x 44 the AA bar (it is AAA; AA is 24 x 24).
- Treating 18 px as large text.
- Fixing focus order with positive `tabindex`.
- Placeholder as the only label.
- Colour as the only signal for an error or status.
- Heading level chosen for its look.
