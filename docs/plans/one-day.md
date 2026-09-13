# Context Fidelity: One-Day Implementation Plan

> For agentic workers: Use superpowers:executing-plans to implement this plan task by task. Checkboxes track completion.

**Goal:** Deliver a reproducible Inspect experiment and a two-minute visual demonstration of how context compression affects completion reporting.

**Architecture:** Execute tasks once, freeze histories, derive four contexts, collect reports, independently score them, and render an offline viewer.

**Tech stack:** Python, Inspect AI, Docker, one model endpoint, pytest, Matplotlib, static HTML.

**Spec:** [Protocol v0.3](../project-spec.md), [ED-001](../experiments/ED-001-context-comparison.md), and [ED-002](../experiments/ED-002-evidence-restoration.md).

**Time:** 10 focused hours plus 2 hours of buffer. Implementation and smoke validation are complete; calibration is in progress. Hosted CI and the held-out study remain pending.

## Constraints and files

The protocol controls scientific decisions: 4 development tasks, 12 held-out tasks, 2 environments per task, 4 contexts, 2 reporting repetitions, **192 main reports**, and **24 summaries**. C/D share a 384-token payload cap. Primary comparison: C–D. Do not add another model or a training experiment to the main run.

Work from the `context-fidelity/` repository root. Follow [AGENTS.md](../../AGENTS.md) and [engineering requirements](../engineering.md) throughout. Engineering gates apply to every slice; they are not a final-hour add-on.

Repository file map:

| Files | Responsibility |
|---|---|
| `pyproject.toml`, lockfile, `config.yaml` | Dependencies, exact model configuration, seeds, limits. |
| `tasks/`, `sandbox/` | Fixtures, reference solutions, immutable tests, environment conditions. |
| `src/context_fidelity/experiment.py`, `__main__.py` | Inspect orchestration and CLI. |
| `src/context_fidelity/evidence.py`, `contexts.py` | Event/version record, independent truth, four contexts, restoration variants. |
| `src/context_fidelity/score.py`, `analyze.py` | Scoring, blind-review export, paired analysis and plots. |
| `src/context_fidelity/viewer.py`, `tests/` | Static replay; meaningful checks of the evidence and scoring logic. |
| `runs/`, `results/`, `README.md` | Raw records, review labels, figures, results, reproduction instructions. |

Use task ID, environment, history ID, context arm, and repetition as join keys. Every report must link to its immutable source history, exact supplied context, and verdict evidence.

## Hour 0–1: Connect the repository and make one case work

- [ ] Connect the user-created remote, inspect Git state, and create a feature branch. Configure the main-branch ruleset and required `quality` CI check after the first hosted run exposes that check.
- [x] Run foundation checks. Add packaging metadata, the first acceptance test, and the real Docker integration test before completing this slice.

- [x] Check Docker and model access. Smoke-test local tool use, memory, latency, and context limits; switch to an available API during development if necessary.
- [x] Implement one small repair fixture and the five tools.
- [x] Run the actor through saved code, tests, and note; capture an Inspect log and versioned events. The live actor terminated in prose instead of calling `finish_work`; that allowed early termination is retained and labeled.
- [ ] Record throughput and estimate the full-run duration and any API cost.

**Exit:** Final artifacts and independently recorded events agree. One saved trace can be replayed. Application CI gates have activated and pass for this slice.

## Hours 1–3: Build the experimental controls

- [x] Implement A–D. Verify B preserves transcript contents; C uses only actor-visible evidence and fits its cap.
- [x] Implement the report schema, ground-truth labels, structured scorer, and blinded prose-review export.
- [x] Check current versus stale tests, partial suites, zero collected tests, failed/recovered writes, malformed reports, and prose contradicting structured fields.
- [x] Finish four development tasks and mechanically validate their reference solutions.

**Exit:** Evidence/scoring fixtures pass. The compact extract neither loses required evidence nor imports hidden evaluator conclusions. Strict typing and the 90% overall / 95% per-critical-module statement and branch coverage gates pass.

## Hours 3–4: Pilot and freeze

- [ ] Run eight development histories and one report per context: 32 pilot reports. Check obstacle visibility, report formatting, summary retention, actual compression, and runtime.
- [ ] If 384 tokens does not meaningfully compress histories, test 256 during development only, provided C still preserves the required evidence. Choose a cap for compression and evidence retention, not for the largest reporting effect.
- [ ] If reporting saturates, record that limitation. Do not manufacture errors or select only failing trajectories.
- [x] Prepare 12 distinct held-out fixtures; assign four each to blocked tests, partial tests, and recoverable note writes.
- [ ] Freeze and hash the protocol/configuration, tasks, prompts, extractor, rubric, sample size, diagnostic-selection rule, and analysis.

**Feasibility fallback:** If measured throughput or review time cannot fit the day, amend the protocol before held-out evaluation to eight tasks: three blocked-test, three partial-test, two recovery tasks. This gives 16 histories, 16 summaries, and 128 main reports. Label it a smaller pilot. Never choose this fallback from held-out effect sizes.

**Exit:** A dated manifest specifies the exact experiment. Development and held-out outputs are separate.

## Hours 4–6: Run the fixed experiment

- [ ] Generate the 24 held-out histories, derive their four contexts, and freeze the 24 ordinary summaries.
- [ ] Collect 192 reports in a reproducibly randomized order. Track timeouts, context overflow, and infrastructure retries explicitly.
- [ ] Audit summary omissions before viewing report outcomes; lock eligible restoration cases using the predeclared selection rule.
- [ ] While inference runs, build the static viewer using development data.

**Exit:** Every planned main report has a result or a documented technical-failure status. The viewer can display a history, contexts, reports, and linked evidence.

## Hours 6–8: Review and analyze

- [ ] Review shuffled reports with arm labels hidden; inspect underlying evidence as needed and record any unavoidable unblinding.
- [ ] Run the diagnostic on qualifying cases: decisive-event versus matched-control additions, two repetitions each, at most 32 reports.
- [ ] Compute the paired C–D effect, task-cluster intervals, factual coverage, error components, retention, and raw counts. Keep B–A exploratory.
- [ ] Generate three figures: unreliability/coverage by context; decisive-fact retention; restoration effect if run. Save plotting code, vector PDFs, and 300-dpi PNGs.

**Exit:** Conclusions follow measured results and preserve uncertainty. Summary-introduced errors are distinguished from reporting errors.

## Hours 8–10: Package the work

- [ ] Complete the [results template](../experiments/results.md): findings, interpretation, alternatives, limitations, and deviations.
- [ ] Populate the viewer with actual held-out records. Demonstration cases follow a disclosed rule: first task-ID C/D correctness difference, first persistent error if present, and first supported normal success.
- [ ] Verify commands for one-case execution, the configured experiment, and offline analysis. Document the environment and expected outputs.
- [ ] Update both EDs with results and interpretation, complete scientific/engineering review, and verify the final branch through CI before PR integration.
- [ ] Record a two-minute video: question → actual trace/context comparison → aggregate results → what the evidence supports.

**Exit:** Someone can inspect a claim, find its execution evidence, and reproduce the analysis from saved logs.

## Hours 10–12: Buffer and scope cuts

Use buffer for infrastructure, scoring disagreements, and reproduction checks. Cut visual polish and extra comparisons first. Do not lower coverage thresholds, skip Docker integration, or bypass review to meet the clock. If the restoration diagnostic cannot finish, disclose that its mechanism test remains unperformed. If the main run is incomplete, report the missingness and downgrade the conclusion.

Do not silently change the frozen study, invent results, add activation probes, or broaden into effects of compression during task execution. A clean null result with working evaluation infrastructure is a valid deliverable.
