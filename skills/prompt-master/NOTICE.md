# Prompt master: source and attribution

This skill is vendored, unmodified apart from its frontmatter, from
**[nidhinjs/prompt-master](https://github.com/nidhinjs/prompt-master)** by
**Nidhin Joseph Nelson**.

- Upstream version: 1.8.2
- Upstream commit: [`49b3a72469b1e7287d66387004ccfcca90e5968a`](https://github.com/nidhinjs/prompt-master/commit/49b3a72469b1e7287d66387004ccfcca90e5968a) (2026-10-22)
- License: MIT, Copyright (c) 2026 Nidhin Joseph Nelson. The full text is in [LICENSE](LICENSE).

## What is upstream's, unchanged

- [README.md](README.md): the project's own README, kept verbatim. Its install
  steps are for claude.ai and Claude Code; in this harness the skill loads from
  `skills/prompt-master/` like any other.
- [LICENSE](LICENSE)
- [references/templates.md](references/templates.md) and [references/patterns.md](references/patterns.md)
- The body of [SKILL.md](SKILL.md), everything below the provenance comment.

## What this harness changed

Only the YAML frontmatter of `SKILL.md`. Upstream's `name`, `version` and
`description` keys are replaced with this harness's skill contract (`name`,
`slug`, `use`, `inputs`, `access`, `steps`, `validation`, `return`,
`band_width`), which the loader in `src/skills/loader.py` requires. The `use`
line keeps upstream's trigger: only when the user explicitly asks for a prompt.

## Updating

Re-vendor from a newer upstream commit: copy the files above verbatim, keep this
harness's frontmatter, and update the version, commit and date here and in the
provenance comment at the top of `SKILL.md`.
