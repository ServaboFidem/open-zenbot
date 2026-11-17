# wondelai skills: source and attribution

The skills in this folder are vendored from
**[wondelai/skills](https://github.com/wondelai/skills)**, MIT, Copyright (c) 2025
Wondel.ai sp. z o.o. The full text is in [LICENSE](LICENSE). They reached this
repo by way of the pydantic-dxl-crew harness, not straight from upstream.

| Folder | Name here | Upstream version |
| --- | --- | --- |
| `contagious` | Contagious (word of mouth) | 1.4.0 |
| `design-everyday-things` | Design of Everyday Things | 1.4.0 |
| `drive-motivation` | Drive (motivation) | 1.4.0 |
| `hooked-ux` | Hooked (habit loops) | 1.5.1 |
| `hundred-million-offers` | $100M Offers | 1.4.0 |
| `improve-retention` | Behavior design (B=MAP) | 1.4.0 |
| `influence-psychology` | Influence (persuasion) | 1.4.1 |
| `made-to-stick` | Made to Stick | 1.4.0 |
| `mom-test` | The Mom Test | 1.4.0 |
| `obviously-awesome` | Obviously Awesome (positioning) | 1.4.1 |

## What changed

- The YAML frontmatter of each `SKILL.md`. Upstream's `name`, `description`,
  `license` and `metadata` keys are replaced with this harness's skill contract
  (`name`, `slug`, `use`, `access`, `steps`, `band_width`), which the loader in
  `src/skills/loader.py` requires. The `use` line is upstream's `description`,
  less the "see <skill>" pointers to skills that are not shipped here.
- A provenance comment under the frontmatter.
- `improve-retention` carries a local note at the top, written for the
  pydantic-dxl-crew harness: it reads "retention" as any asked-for action, and
  says the affiliate tags were stripped from the links at the bottom.

- Two corrections in `hundred-million-offers`, to re-apply after an update unless
  upstream has fixed them:
  - `references/value-equation.md`, Composite Value Score: the formula divides by
    `11 - score` for Time Delay and Effort. Upstream rates both with 10 as best and
    divides by the score itself, so a perfect offer scored 1, not the 100 it states.
  - `SKILL.md`, the diagnostic table: "Could a competitor offer the exact same
    thing?" became "Is the offer hard for a competitor to copy exactly?", so that
    yes is the good answer, as in every other row.

Everything else (the instructions and `references/`) is as it was vendored.

## Updating

Copy the newer upstream folder over, keep this harness's frontmatter, and
update the version in the table and in the provenance comment.
