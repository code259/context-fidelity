# Reproducing Context Fidelity

Use the [project specification](project-spec.md) for the experimental design and [runtime record](runtime.md) for the measured environment. Commands below run from the repository root. No OpenAI credential is needed for the selected local model.

## Install and verify

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy src scripts
uv run pytest -m "not docker and not live" --cov=src/context_fidelity --cov-branch --cov-report=json:coverage.json
uv run python scripts/check_coverage.py coverage.json
uv run pytest tests/integration/test_sandbox.py -m docker
uv build --no-sources
```

Python 3.12 and a working Docker daemon are required. The integration suite intentionally fails if Docker is absent. It executes generated fixture code inside isolated containers. Do not expose the Docker socket or host credentials inside those containers. If the daemon is in a dedicated VM, select it with `DOCKER_HOST` before running the CLI.

## Model endpoint

The experiment uses the same pinned Qwen model for acting, summarization, and reporting. On a suitable NVIDIA GPU, use an isolated Python environment with [the serving dependency lock](../config/serving-requirements.txt), then launch:

```sh
vllm serve Qwen/Qwen3-4B-Instruct-2507 \
  --revision cdbee75f17c01a7cc42f958dc650907174af0554 \
  --tokenizer-revision cdbee75f17c01a7cc42f958dc650907174af0554 \
  --dtype bfloat16 --max-model-len 16384 \
  --gpu-memory-utilization 0.85 --max-num-seqs 8 \
  --enable-auto-tool-choice --tool-call-parser hermes \
  --enforce-eager --host 127.0.0.1 --port 18080
```

Run this service under the host's process supervisor. If serving remotely, forward its localhost port to local port 18081 through an authenticated SSH tunnel with keep-alives. Check `/health` before collection. The client defaults to `http://127.0.0.1:18081/v1`; set `--endpoint` before the subcommand to change the port. The adapter accepts unauthenticated loopback HTTP only and supplies a nonsecret placeholder to the SDK.

The client needs only the pinned tokenizer, not local model weights. Fetch it once before offline collection:

```sh
uv run python - <<'PY'
from pathlib import Path
from context_fidelity.adapters.model import OfficialTokenizer
OfficialTokenizer.load(cache_dir=Path('../.local-runtime/hf'), local_files_only=False)
PY
```

Subsequent CLI runs require that cached revision and do not fetch tokenizer files. Exact generation budgets, seeds, and backend settings are recorded in each prepared plan and Inspect log.

```sh
uv run context-fidelity doctor
```

This command checks cached tokenization, the model's health and advertised ID, Docker, and the pinned sandbox image. It makes no generation calls and downloads nothing. Collection repeats the required checks before starting; reporting skips Docker because it uses frozen histories. The model listing cannot attest the server's checkpoint revision or precision. Verify those against the serving command and runtime record.

## Development run

Choose a new run ID and output path for every attempt:

```sh
uv run context-fidelity validate-tasks --tasks tasks/dev --output runs/dev-validation.json
uv run context-fidelity prepare --split dev --run-id dev-example --run-dir runs/dev-example --validation runs/dev-validation.json
uv run context-fidelity collect --run-dir runs/dev-example
uv run context-fidelity report --run-dir runs/dev-example
```

This plans eight histories and 32 reports. Source or protocol changes after preparation invalidate continued generation; start a separate development run after a diagnosed repair. Keep failed runs. A malformed or wrong model answer is data, not grounds for retrying.

## Held-out run and review

Finish development calibration, freeze both EDs and `config.yaml`, and validate the held-out fixtures before preparing a held-out run. Collection comes first. Audit every available ordinary summary and lock restoration eligibility before inspecting reporting outcomes. Pass the saved `SummaryAuditManifest` using `report --summary-audit PATH`.

For the complete primary outcome, a person must review prose and summary support under [the rubric](review-rubric.md). The [human-review guide](human-review.md) describes the 24-summary and 192-report handoff. Assistant labels remain provisional. Unknown review status must remain missing in primary analysis; it must never be treated as a reliable report.

Offline reanalysis must use immutable original run inputs and create a separately identified output directory. Preserve the original generation freeze and fingerprint the analysis implementation. A later scoring correction must not rewrite raw model responses or the original analysis.

## Analyze and inspect saved results

```sh
uv run context-fidelity analyze --run-dir runs/dev-example --analysis-id dev-analysis-001 --output runs/analysis/dev-analysis-001
uv run inspect view --log-dir runs/analysis/dev-analysis-001/inspect --host 127.0.0.1 --port 18575
```

Analysis runs offline. It verifies the original artifact hashes, computes paired estimates, writes plots, and adds scores to separate Inspect logs. No model endpoint, tokenizer, or Docker daemon is needed. `package-freeze.json` marks a complete export; a directory without it is incomplete. Choose a new analysis ID and destination after a failed export.

To add completed review records, pass `--summary-audit PATH` and `--prose-reviews PATH`. The first file contains a `SummaryAuditManifest`; the second contains an array of `ProseReview` records. Each review binds to the exact context and report. Changing review labels produces a new analysis, never a rewrite of the original.

Inspect shows structured errors, factual coverage, and primary unreliability as separate scores. `pending` means required review is missing. Use Samples for cross-log score columns; filter `logFile` with `contains` and `-C-`, for example, to select arm C. Tasks also exposes experiment tags after log headers load. Open a report and its score explanation to inspect evidence. Keep the blinded review items separate from their answer key and scored views until review is complete.

The export contains measured figure inputs, PNG/PDF figures, paired effects, review JSON, and a native-log index. Build a portable viewer through Inspect's own command:

```sh
uv run inspect view bundle --log-dir runs/analysis/dev-analysis-001/inspect --output-dir runs/analysis/dev-analysis-001-bundle
```

Use Inspect's local server for development. A separately hosted bundle requires HTTP Range support; Python's basic `http.server` does not provide it. [Inspect View documentation](https://inspect.aisi.org.uk/log-viewer.html)

## Reproduce the saved held-out results

The [provisional release](https://github.com/code259/context-fidelity/releases/tag/v0.1.0-provisional) contains `context-fidelity-evidence-2026-09-12.zip` and `context-fidelity-inspect-heldout-2026-09-12.zip`. The [artifact catalog](../results/heldout-002/artifact-catalog.json) records their download URLs, sizes, SHA256 checksums, and reproduction check. GitHub's uploaded-asset digests match those checksums. Human review remains pending.

The evidence archive contains the completed development and held-out runs, exact frozen held-out inputs, pre-report audits, native scored logs, figures, and analysis records. Extract it at the root of a fresh checkout with the locked environment installed, then run:

```sh
uv run context-fidelity analyze --run-dir runs/heldout-002 --analysis-id independent-analysis-001 --output runs/analysis/independent-analysis-001
uv run inspect view --log-dir runs/analysis/independent-analysis-001/inspect --host 127.0.0.1 --port 18575
```

A fresh-directory check using archived source `f8fa80c` and the evidence archive reproduced all 192 cells, arm counts, summary audits, planned pairs, effects, and intervals exactly. It used the existing locked Python environment and made no model calls. This verifies artifact portability and offline reanalysis; it does not constitute a fresh dependency installation or human review.

The smaller Inspect archive contains a portable viewer and 192 scored logs. Serve it through a host with HTTP Range support. Its records are unblinded. For human review, provide the evidence archive's `blinded-items.json` separately and withhold `blinded-key.json`, scored views, and comparison plots until judgments are saved.
