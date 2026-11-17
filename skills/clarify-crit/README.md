# Clarify (CRIT)

**Clarify** is a lightweight request-refinement skill for AI models, built on the **CRIT** method. It helps a model understand a user's request before acting, while minimising questions, visible framework structure, and total interaction cost.

## Quick start

1. Add the `clarify-crit` folder to your AI environment's skills location (see [Installation](#installation) below).
2. In a chat, type: `Use CRIT. <your request>` or `Use Clarify. <your request>`.
3. That's it — the skill activates and produces either a direct answer or a short brief with the minimum questions needed.

CRIT stands for:

- **Context** — recover the information that already exists;
- **Role** — use the smallest useful professional perspective;
- **Interview** — ask only what materially changes the work;
- **Task** — form an executable internal representation or portable brief.

## Design principle

> Use the least interaction and least visible structure required to remove material uncertainty and produce a correct, executable result.

CRIT is designed to prevent two common failures:

1. executing the wrong interpretation too quickly; and
2. turning every request into a long intake process.

## What is included

```text
clarify-crit/
├── SKILL.md
├── README.md
├── CHANGELOG.md
├── CONTRIBUTING.md
├── LICENSE
└── references/
    ├── response-patterns.md
    ├── examples-and-edge-cases.md
    └── failure-modes.md

tests/
├── evaluation-tests.md
├── regression-prompts.json
└── results/
```

`SKILL.md` contains the complete runtime procedure. References are optional and should be consulted only when needed. The `tests` directory is for development and benchmarking; it is not part of normal runtime loading.

## Key behaviours

CRIT:

- reuses conversation and file context before asking questions;
- distinguishes clear, ambiguous, incomplete, undefined, and conflicted requests;
- routes unknowns through reuse, research, asking, defaults, or omission;
- aims for zero questions and usually no more than one clarification turn;
- keeps internal classification and reasoning out of the visible response;
- executes directly when a visible brief would be redundant;
- creates a portable brief when handoff, approval, or formal specification requires one;
- avoids companion-skill dependencies and works as a standalone release.

## Installation

Skill formats differ between AI products. The most portable method is:

1. copy the `clarify-crit` directory into the location your AI environment uses for skills or custom instructions;
2. ensure `SKILL.md` is available to the model;
3. preserve the relative `references/` paths if your environment supports linked files;
4. do not place `tests/` in the runtime context unless you are evaluating the skill.

Some environments recognise only `name` and `description` in frontmatter. If your loader rejects the optional `version` field, remove that field without changing the skill behaviour.

## Usage

Explicit invocation examples:

```text
Use CRIT before you build this.
```

```text
Use CRIT to turn these notes into a brief another AI can execute.
```

```text
Apply CRIT, ask only what materially matters, then complete the task.
```

CRIT may also activate automatically when unresolved material uncertainty could substantially alter the result. It should not activate automatically merely because a task is technical or long.

## Example

Request:

```text
Use CRIT. Build a dashboard for my business.
```

A good CRIT response would identify the smallest direction-changing decision, such as whether the dashboard is primarily for sales, finances, operations, or executive oversight. It would not begin with a long generic discovery form.

Request:

```text
Use CRIT and write this email from the complete facts below.
```

When the facts are sufficient, CRIT should refine internally and write the email directly rather than displaying a redundant framework report.

## Compatibility

The reasoning protocol is model-agnostic. Exact behaviour may vary because AI environments differ in:

- automatic skill activation;
- support for relative reference files;
- web or tool access;
- treatment of custom frontmatter;
- hidden reasoning and response controls.

Please report the model, version, and environment when sharing feedback.

**Known limitation:** most of CRIT's decision logic (request-state classification, unknown routing, the quality gate) is designed to stay invisible in the response. This works well on strong models but has not yet been broadly verified on smaller or less capable ones, which may apply the logic less consistently while still producing a plausible-looking response. If you test on a smaller model, this is the single most useful thing to report on.

## Evaluation

The project includes a paired evaluation guide and machine-readable regression prompt set. The main measures are:

- correct activation and non-activation;
- necessary and unnecessary questions;
- clarification turns;
- material blockers missed;
- visible framework overhead;
- total conversation tokens;
- final-output quality;
- portability of visible briefs;
- user corrections required.

Do not claim a percentage token saving without recording the model, setup, loaded files, and comparison method.

## Feedback

This public release is intended for testing and improvement. Useful feedback includes:

```text
Model and version:
Environment:
Was CRIT invoked explicitly or automatically?
Original request:
CRIT response:
Did it ask unnecessary questions?
Did it miss a necessary question?
Did it preserve the objective?
Was the visible structure excessive?
Was the final brief or output usable?
Approximate tokens or response length:
Suggested improvement:
```

Bug reports and test cases are especially valuable when they include the complete prompt and output.

## Clarify and CRIT+

This repository contains **Clarify (CRIT)**, the standalone public edition. It does not depend on named companion capabilities.

A future **CRIT+** edition may add capability-aware orchestration and handoff between verified specialist skills. CRIT+ should remain separate so the public Clarify release stays portable and dependency-free.

## Licence

Clarify (CRIT) is released under the MIT License. See [`LICENSE`](LICENSE).
