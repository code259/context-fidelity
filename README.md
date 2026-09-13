# Context Fidelity

**Can an agent still report its work accurately after its history has been compressed?**

Context Fidelity tests this with small coding tasks. An agent edits a file, writes a note, and runs tests. We freeze that work, give the same model four versions of its history, and check its final report against what actually happened.

The main comparison is a compact record of tool evidence versus an ordinary model-written summary. Native history and a complete external transcript provide controls. A planned diagnostic asks whether restoring an omitted fact repairs reporting.

**Measured result:** All 24 held-out histories and 192 reports completed without a technical failure. Compact evidence produced 17/48 structured errors; ordinary summaries produced 11/48. The difference was +12.5 percentage points, with a 95% task-cluster interval of [−4.17, 27.08]. Factual coverage was lower with compact evidence. This does not demonstrate the predicted benefit.

The complete primary comparison still needs human review: 49 reports have definite errors and 143 verdicts remain unresolved. The restoration diagnostic had no eligible cases and did not run. Read the [results and interpretation](docs/experiments/results.md) for the evidence and limits.

[Download the evidence and Inspect bundles](https://github.com/code259/context-fidelity/releases/tag/v0.1.0-provisional) · [Implementation PR](https://github.com/code259/context-fidelity/pull/3) · [Human-review guide](docs/human-review.md)

![Held-out structured results, with human review pending](results/heldout-002/figures/arms.png)

Inspect View is the main interface for traces and scored comparisons. Matplotlib figures and blinded review records accompany the logs. The project uses Inspect’s evaluation tools rather than maintaining a separate dashboard.

The [two-minute walkthrough](docs/demo.md) follows actual reports from the run. It includes a useful distinction: the same false full-pass claim can arise when a reporter ignores correct evidence or when its summary already contains the error.

## Research

- [Project specification](docs/project-spec.md): motivation, cited literature, shared experimental contract.
- [ED-001: Context comparison](docs/experiments/ED-001-context-comparison.md).
- [ED-002: Evidence restoration](docs/experiments/ED-002-evidence-restoration.md).
- [One-day plan](docs/plans/one-day.md).
- [Engineering requirements](docs/engineering.md) and [agent instructions](AGENTS.md).
- [Results record](docs/experiments/results.md): study status, findings, and limitations.

## Development

Use Python 3.12 and the committed uv lockfile.

```sh
uv sync --locked
uv run pre-commit install
uv run pre-commit run --all-files
uv run pytest tests/engineering
```

GitHub Actions checks typing, tests, coverage, real Docker execution, and packaging. The [published baseline's hosted CI](https://github.com/code259/context-fidelity/actions/runs/34739191004) passed 526 offline tests and 11 Docker integration tests. The review-tracking fix adds 18 regression cases; all **544 offline tests** passed locally. Current statement coverage is 99.66% and branch coverage 98.71%. Gates require 90% of each overall, and 95% of each in evidence, contexts, scoring, and analysis. [PR checks](https://github.com/code259/context-fidelity/pull/3/checks) validate later revisions.

## Run an experiment

Follow [the reproduction guide](docs/reproduction.md) to install the pinned model and sandbox, cache the tokenizer, and check the endpoint. The default OpenAI-compatible endpoint is `http://127.0.0.1:18081/v1`; the tokenizer cache is `../.local-runtime/hf`. Set `DOCKER_HOST` if you use a dedicated Docker daemon. The [runtime record](docs/runtime.md) lists the measured environment.

```sh
uv run context-fidelity validate-tasks --tasks tasks/dev --output runs/dev-validation.json
uv run context-fidelity doctor
uv run context-fidelity prepare --split dev --run-id dev-example --run-dir runs/dev-example --validation runs/dev-validation.json
uv run context-fidelity collect --run-dir runs/dev-example
uv run context-fidelity report --run-dir runs/dev-example
uv run context-fidelity analyze --run-dir runs/dev-example --analysis-id dev-analysis-001 --output runs/analysis/dev-analysis-001
uv run inspect view --log-dir runs/analysis/dev-analysis-001/inspect --host 127.0.0.1 --port 18575
```

Preparation hashes the plan and implementation. Collection saves the Inspect logs, exact requests and responses, histories, contexts, and independent final-state records. Reporting then uses those frozen inputs. Each phase runs once per run ID and preserves failures; existing artifacts cannot be overwritten.

Before held-out reporting, audit the ordinary summaries without viewing report outcomes. The [review rubric](docs/review-rubric.md) separates three questions: what happened, what the model could establish from its context, and whether its prose makes an error.

Development is on `feat/context-compression-eval`, with [PR #3](https://github.com/code259/context-fidelity/pull/3) open against `main`. The [main-branch rules](https://github.com/code259/context-fidelity/rules/23134668) require a PR, successful `quality` CI, and resolved conversations; force pushes and deletion are blocked. Human review and video recording remain open.
