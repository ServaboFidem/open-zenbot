# Humanizer: source and attribution

Vendored from **[nuwrldnf8r/book_editor_skill](https://github.com/nuwrldnf8r/book_editor_skill)**
(commit 55abf13) by Gavin (nuwrldnf8r), MIT. The full text is in [LICENSE](LICENSE).
This copy is the one adapted for the pydantic-dxl-crew harness on 2026-11-03
(version 3.1.0-dxl.1), so its instructions differ from upstream's.

This harness changed only the YAML frontmatter of `SKILL.md` (to the contract
`src/skills/loader.py` requires) and added the provenance comment under it.
`references/` is as vendored. `scripts/check_tells.py` has one change: the
double-hyphen pattern also catches a dash at a line edge and one attached to the
word before it (`Wait-- what?`), which it used to pass.
