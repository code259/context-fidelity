# Engineering requirements

These are project requirements, enforced through [AGENTS.md](../AGENTS.md), tests, configuration, and CI. The deadline can reduce experiment scope or presentation polish; it does not justify unreliable scoring or untested execution.

## Version control and review

The local origin is configured for `code259/context-fidelity`; foundation commit `caf7082` is preserved on `chore/engineering-foundation`, and implementation is on `feat/context-compression-eval`. No public push has completed. Inspect the current base and work on a short-lived feature branch. Use focused commits with behavior and verification in the message; keep unrelated refactors separate. Inspect status before switching branches and preserve user changes.

Integrate through a PR describing the problem, resulting behavior, relevant ED, tests, coverage, and limitations. Review scientific validity and implementation quality as separate passes. A fresh reviewer is preferable when available; if working alone, record a separate self-review rather than claiming independent approval. Resolve material findings before merging. Use worktrees only for changes that need isolation. Do not force-push shared history.

After the remote exists, configure a ruleset for `main`: require PRs and the **quality** status check, require resolved conversations, block force pushes/deletion, and enable secret scanning/push protection where available. Require an independent approval when an actual collaborator is available. These remote settings cannot be enforced by committing a YAML file and are currently pending.

## Architecture and design patterns

Use a small package under `src/context_fidelity/`:

- `contracts.py`: validated, versioned records for tasks, tool events, histories, contexts, reports, and verdicts.
- `evidence.py`: append-only provenance and deterministic final-state interpretation.
- `contexts.py`: four context strategies operating on immutable histories.
- `score.py`, `analyze.py`: pure scoring and paired statistical calculations.
- `experiment.py`: orchestration through Inspect; explicit lifecycle transitions.
- `adapters/`: model and sandbox I/O, injected behind narrow interfaces.
- `viewer.py`, `__main__.py`: presentation and CLI.

Apply a functional core with imperative I/O boundaries, adapter/strategy patterns for real alternatives, and explicit state transitions for execution → frozen history → reporting → scoring. Prefer functions and typed records over inheritance trees, global mutable state, or a custom plugin framework. Add an ADR only for a consequential choice with alternatives, such as changing the evidence contract.

Validate external data at entry, version persisted schemas, fail clearly on incompatible records, and keep artifact paths local to their run. Bound concurrency, time, retries, and output size. Distinguish technical failure, invalid model output, and valid-but-wrong behavior.

## Testing and coverage

| Layer | Required evidence |
|---|---|
| Unit | Independently specified examples for truth labels, scope/version checks, formatting, and effect calculations. |
| Property | Event ordering, irrelevant-event invariance, immutable transformations, deterministic seeding, and paired aggregation invariants. |
| Contract | Provider/tool responses, malformed payloads, truncation, cancellation, and retry limits. |
| Integration | Real filesystem and Docker boundaries, failed/recovered operations, immutable tests, isolated workspaces, and cleanup. |
| End to end | One deterministic fixture through execution, four contexts, scoring, saved artifacts, and offline replay. A fake provider may exercise orchestration; it is not a model result. |
| Research validation | Reference solutions and an independently checked scoring oracle; task clustering and interval calculations checked against small hand-computed cases. |

Application thresholds are **90% statements and 90% branches** overall, and **95% each per critical module** (`evidence.py`, `contexts.py`, `score.py`, `analyze.py`). CI measures the entire source directory. The coverage checker separately enforces statements and branches; a combined percentage cannot hide missing branch coverage. Modules without branches pass that component vacuously, not as an empirical claim.

Mock external services only at real boundaries. Do not mock sandbox execution in the Docker integration test. Never retry a flaky test into a pass. Coverage exceptions require a specific documented rationale and review; lowering thresholds is not an implementation shortcut.

## Tooling and CI

The committed workflow runs on pushes and PRs with read-only permissions, immutable Action references, dependency-lock validation, Ruff formatting/lint, strict mypy, engineering-tool tests, and local documentation-link checks.

When `src/**/*.py` appears, it automatically adds:

```sh
uv run mypy src
uv run pytest -m "not docker and not live" --cov=src/context_fidelity --cov-branch --cov-report=json:coverage.json --cov-report=xml
uv run python scripts/check_coverage.py coverage.json
docker info
uv run pytest tests/integration/test_sandbox.py -m docker
uv build --no-sources
```

The first code slice must add packaging metadata and a real Docker integration test so these gates pass. There is no zero-test success fallback. Live model smoke tests are explicitly invoked during development, use the chosen budget, and record results separately from offline CI.

Pre-commit runs the same basic static checks locally; CI is authoritative because hooks can be skipped. Dependabot proposes weekly Action/dependency updates. Add runtime dependencies and regenerate `uv.lock` when their first feature lands.

GitHub’s [Action security guidance](https://docs.github.com/en/actions/reference/security/secure-use) supports immutable pins and least privilege; Astral’s [uv workflow guidance](https://docs.astral.sh/uv/guides/integration/github/) documents the installation and locked-environment setup.

## Definition of done

A slice is done when its acceptance behavior is implemented, meaningful tests and applicable gates pass on the final state, both review passes are complete, and its docs/ED references match the code. Record unavailable checks as unverified.

A held-out run additionally requires a frozen manifest, validated fixtures, reproducible raw records, explicit missingness, and a completed ED result/analysis section. Never fabricate illustrative results or claim a neural mechanism from behavioral evidence.
