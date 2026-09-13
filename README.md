# Context Fidelity

**Can an agent still report its work accurately after its history has been compressed?**

Context Fidelity tests this with small coding tasks. An agent edits a file, writes a note, and runs tests. We freeze that work, give the same model four versions of its history, and check its final report against what actually happened.

The main comparison is a compact record of tool evidence versus an ordinary model-written summary. Native history and a complete external transcript provide controls. A second experiment tests whether restoring an omitted fact repairs a reporting error.

**Development status:** Eight histories and 32 reports completed with no technical failures in `dev-pilot-002`. The held-out study has not run. Full primary results require human review of prose and summary support; assistant judgments remain provisional.

Inspect View is the main interface for traces and scored comparisons. Matplotlib figures and blinded review records accompany the logs. The project uses Inspect’s evaluation tools rather than maintaining a separate dashboard.

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

GitHub Actions checks typing, tests, coverage, real Docker execution, and packaging. The checks have run locally; hosted CI is pending publication. Coverage must reach 90% of statements and branches overall, and 95% of each in the evidence, context, scoring, and analysis modules.

## Run an experiment

Follow [the reproduction guide](docs/reproduction.md) to install the pinned model and sandbox, cache the tokenizer, and check the endpoint. The default OpenAI-compatible endpoint is `http://127.0.0.1:18081/v1`; the tokenizer cache is `../.local-runtime/hf`. Set `DOCKER_HOST` if you use a dedicated Docker daemon. The [runtime record](docs/runtime.md) lists the measured environment.

```sh
uv run context-fidelity validate-tasks --tasks tasks/dev --output runs/dev-validation.json
uv run context-fidelity doctor
uv run context-fidelity prepare --split dev --run-id dev-example --run-dir runs/dev-example --validation runs/dev-validation.json
uv run context-fidelity collect --run-dir runs/dev-example
uv run context-fidelity report --run-dir runs/dev-example
uv run context-fidelity analyze --run-dir runs/dev-example --analysis-id dev-analysis-001 --output runs/analysis/dev-analysis-001
uv run inspect view --log-dir runs/analysis/dev-analysis-001/inspect --port 18575
```

Preparation hashes the plan and implementation. Collection saves the Inspect logs, exact requests and responses, histories, contexts, and independent final-state records. Reporting then uses those frozen inputs. Each phase runs once per run ID and preserves failures; existing artifacts cannot be overwritten.

Before held-out reporting, audit the ordinary summaries without viewing report outcomes. The [review rubric](docs/review-rubric.md) separates three questions: what happened, what the model could establish from its context, and whether its prose makes an error.

Development is on `feat/context-compression-eval`. Publication, hosted CI, and remote branch rules remain pending.
