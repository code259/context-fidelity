# Context Fidelity

An Inspect AI research demo asking whether context compression changes the accuracy of an agent’s completion report.

**Status:** Research design and engineering foundation prepared. The evaluation is not implemented; experiments have not been run.

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

GitHub Actions currently checks this foundation. As soon as Python implementation files exist under `src/`, it also requires strict typing, application tests, coverage, real Docker integration, and a package build. A green foundation run is not evidence that the experiment works.

Repository name: **context-fidelity**. The local origin is configured as `https://github.com/code259/context-fidelity.git`; the foundation branch is `chore/engineering-foundation`. Surrounding application notes and resume material are outside this repository. The first push, hosted CI run, and remote branch rules remain pending.
