# Context Fidelity: instructions for every coding task

These requirements apply throughout this repository. Read this file, [engineering requirements](docs/engineering.md), the [project spec](docs/project-spec.md), the relevant ED, and the active plan before editing.

## Required workflow

1. Inspect Git status and the diff; preserve existing work. Develop on a short-lived `feat/`, `fix/`, `refactor/`, or `research/` branch. Keep `main` releasable; use a PR for integration.
2. State the behavior, affected interfaces, risks, and evidence needed. Follow the active plan in independently testable slices.
3. For substantive logic, first demonstrate the missing behavior with a failing test or deterministic reproduction. Implement the smallest change, then refactor while checks pass.
4. Use typed, validated contracts and dependency injection at model, sandbox, clock, randomness, and storage boundaries. Keep evidence extraction/scoring/analysis pure where possible. Avoid speculative frameworks and broad exceptions.
5. Run focused tests after each slice and the required CI-equivalent checks before handoff. Review the final diff for scientific correctness and engineering quality separately.
6. Report fresh verification commands, results, coverage, and remaining gaps. Never present a skipped check or a docs-only CI pass as application validation.

## Skills to use when available

- Planning: `superpowers:writing-plans`; implementation: `superpowers:executing-plans` and `codex-engineering-guardrails:code-work`.
- Substantive new behavior: `superpowers:test-driven-development`; uncertain failures: `superpowers:systematic-debugging`.
- Verification/review: `codex-engineering-guardrails:code-verification` and `superpowers:verification-before-completion`.
- Isolated concurrent changes: `superpowers:using-git-worktrees`; use delegation only when separately authorized.
- Research design: `brainstorming-research-ideas`; publication figures: `academic-plotting`.
- Viewer work: applicable frontend implementation/testing skills if a frontend framework is introduced.

Announce first use and read the actual skill. Skill names are portable guidance; never pretend to have loaded a missing skill. Follow the concrete repository requirements below if a skill is unavailable, and report the gap.

## Non-negotiable checks

- At least **90% statement coverage and 90% branch coverage** across application code; at least **95% of each per critical module**: `evidence.py`, `contexts.py`, `score.py`, `analyze.py`.
- Include never-imported modules in coverage. Do not weaken thresholds, omit hard paths, delete useful tests, or add blanket ignores to obtain green CI.
- Test independent expected values, error/recovery paths, evidence provenance, stale/partial verification, arm equivalence, pairing, and isolation. Coverage percentages supplement behavioral review.
- PR CI is offline with respect to model providers: no paid calls or model credentials. Real Docker integration is required once implementation starts and must fail when unavailable.
- Lock dependencies; pin Actions by commit; update them through reviewed changes. Source belongs under `src/context_fidelity/`.
- Preserve immutable run IDs, configuration hashes, seeds, raw evidence, and explicit failures. No hidden truncation or retry-until-success.
- Freeze EDs before held-out runs; separate development results. Change hypotheses, exclusions, sample sizes, or scoring through documented amendments, never to improve an observed effect.
- Escape untrusted transcript/model content in the viewer. Keep sandbox workspaces separate; do not expose host credentials or the Docker socket to generated code.

Use [engineering requirements](docs/engineering.md) for commands, review criteria, architecture, and repository setup. Do not bypass a failing check to meet the one-day deadline.

## Writing

Write direct, active prose for a human reader. Start with the concrete question, behavior, or finding. Vary sentence length; remove filler, corporate language, and announcements about what a section will explain. Stop when the point is made. Avoid stock conclusions and these words in project prose: delve, testament, beacon, realm, leverage, underscore, strictly, pivot, resonate, foster, paradigm. Preserve necessary technical identifiers and verbatim evidence. Label development results, assistant judgments, uncertainty, and unfinished work accurately.
