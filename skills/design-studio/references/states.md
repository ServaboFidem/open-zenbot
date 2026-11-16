# State coverage

Every surface that fetches, transforms or accepts data must render all five
states. Shipping only the populated one is the most common silent failure.

## The five states

| State | When | Must contain |
| --- | --- | --- |
| Loading | Data in flight | Skeleton, spinner or shell, plus a "taking longer than expected" note at 15 s |
| Empty | No records, or no results | Headline, plain explanation, primary action |
| Error | Fetch or validation failed | Cause in plain words, a recovery action, the user's input kept |
| Populated | Data present | What the design was drawn for |
| Edge | Huge volume, long strings, missing optional fields, RTL text | A layout that does not break |

Edge scenarios to test: a 200-character title, a missing avatar, all optional
fields empty, all required fields at maximum length, a single-character search,
thousands of rows.

## Forms

Add three states: untouched (no messages), dirty and valid (helper text stays, no
success colouring), submitted and pending (button shows loading, fields locked).
Validate on blur, not the first keystroke. For live fields such as passwords,
validate on each keystroke only after the first blur. Clear an error the moment
the input is valid.

## Empty states

- First use: illustration, headline, a sentence of value, primary action.
- No results: echo the query and suggest alternatives. Never a blank.
- Cleared: warm wording and an optional next step.
- Never fold an error into an empty state.

## Errors

Answer three things in order: what happened ("Your card was declined"), why if
known ("Insufficient funds"), what to do (retry, another path, support). Keep the
user's input. Match severity to scope: field, form, section, page, app. A field
slip does not need a page error.

Retry: the first retry is immediate; later ones back off 2, 4, 8 s. After three
failures swap "Retry" for "Contact support" with a copyable error ID, and show
"Last attempted: Xs ago".

## Loading thresholds

| Duration | Indicator |
| --- | --- |
| Under 300 ms | None |
| 0.3 to 2 s | Subtle spinner or skeleton |
| 2 to 10 s | Skeleton matching the layout, or a labelled spinner |
| 10 to 30 s | Determinate progress with cancel |
| 30 to 60 s | Progress with cancel; do not repeat the 15 s notice |
| Over 60 s | Stop animating; show retry, cancel or continue |

Never leave a spinner running without a timeout.

## Announcing changes

| Change | ARIA | Focus |
| --- | --- | --- |
| Inline error on submit | `role="alert"` | Move to first error field |
| Toast or confirmation | `role="status"` | Leave focus |
| Critical or destructive | `role="alertdialog"` | Move into the dialog |
| Loading starts | `role="status"` ("Loading...") | Leave focus |
| Loading ends | none | Move to content if the user asked for it |

Live regions must exist in the DOM before content is injected.

## Common mistakes

- Only the populated state exists.
- Empty state is "No data" with no action.
- "Something went wrong" with no cause or recovery.
- A submit that clears the form on failure.
- Validation firing on the first keystroke.
- A full-page loader when one section is fetching.
- Toasts that move between positions, or cannot be paused (WCAG 2.2.1).
- Colour as the only error signal.
