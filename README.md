# Context Fidelity

An Inspect AI research demo asking whether context compression changes the accuracy of an agent’s completion report.

**Status:** Inspect execution, sandboxing, four contexts, scoring, paired analysis, and offline replay are implemented. Real model/Docker smoke tests pass. Development calibration is underway; no held-out findings are available yet.

An agent’s final answer should distinguish what it changed, what it verified, and what remains uncertain. We compare native history, a full external transcript, a compact evidence record, and an ordinary summary. A separate evidence-restoration experiment tests whether omitted facts contribute to reporting errors.

## Start here

- [Project specification](docs/project-spec.md): motivation, cited literature, shared experimental contract.
- [ED-001: Context comparison](docs/experiments/ED-001-context-comparison.md).
- [ED-002: Evidence restoration](docs/experiments/ED-002-evidence-restoration.md).
- [One-day plan](docs/plans/one-day.md).
- [Engineering requirements](docs/engineering.md) and [agent instructions](AGENTS.md).
- [Results template](docs/experiments/results.md): no measured results yet.

## Development

Use Python 3.12 and the committed uv lockfile.

```sh
uv sync --locked
uv run pre-commit install
uv run pre-commit run --all-files
uv run pytest tests/engineering
```

The GitHub Actions workflow requires strict typing, application tests, coverage, real Docker integration, and a package build. These checks have run locally; the first hosted run is pending publication. Coverage gates separately require 90% statements and branches overall and 95% of each for evidence, contexts, scoring, and analysis.

## Run an experiment

Use the pinned model and sandbox in [the runtime record](docs/runtime.md). The model endpoint is local and OpenAI-compatible; the default address is `http://127.0.0.1:18081/v1`. Set `DOCKER_HOST` when using a dedicated Docker daemon. The tokenizer must already be cached in `../.local-runtime/hf`.

```sh
uv run context-fidelity validate-tasks --tasks tasks/dev --output runs/dev-validation.json
uv run context-fidelity prepare --split dev --run-id dev-example --run-dir runs/dev-example --validation runs/dev-validation.json
uv run context-fidelity collect --run-dir runs/dev-example
uv run context-fidelity report --run-dir runs/dev-example
```

Preparation freezes the plan and implementation hashes. Collection saves Inspect logs, exact model messages, execution histories, context payloads, and independent truth records. Reporting consumes frozen collected inputs. Each phase runs once per run ID, preserves technical failures, and refuses to overwrite existing artifacts. Held-out reporting additionally requires a pre-report summary audit. Use the [review rubric](docs/review-rubric.md) to keep world truth, supplied-evidence support, and human prose judgments separate.

Repository name: **context-fidelity**. The local origin is configured as `https://github.com/code259/context-fidelity.git`; implementation is on `feat/context-compression-eval`. Surrounding application notes and resume material are outside this repository. The first push, hosted CI run, and remote branch rules remain pending.
