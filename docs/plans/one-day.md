# Context Fidelity: one-day implementation plan

> Use `superpowers:executing-plans` for implementation. Check off work only after verification.

**Goal:** Deliver a reproducible Inspect experiment and a two-minute demo showing how context compression affects completion reports.

**Method:** Execute each task once per environment, freeze the history, derive four contexts, collect reports, and score them against independent evidence. Use Inspect View for traces and comparisons.

**Tools:** Python, Inspect AI and Inspect View, Docker, one model endpoint, pytest, Matplotlib, and blinded review JSON.

**Spec:** [Protocol v0.3](../project-spec.md), [ED-001](../experiments/ED-001-context-comparison.md), and [ED-002](../experiments/ED-002-evidence-restoration.md).

**Time budget:** 10 focused hours plus 2 hours of buffer. Development run `dev-pilot-002` completed eight histories and 32 reports with no technical failures. The held-out study now has 24 histories and 192 reports, all complete. Analysis and native Inspect exports are ready; human review, hosted CI, and video recording remain open.

## Constraints and files

The protocol fixes 4 development tasks, 12 held-out tasks, 2 environments per task, 4 contexts, and 2 reporting repetitions: **192 main reports** and **24 summaries**. C/D share a 384-token payload cap. C–D is the primary comparison. Keep additional models and training experiments outside the main run.

Work from the `context-fidelity/` repository root. Follow [AGENTS.md](../../AGENTS.md) and [engineering requirements](../engineering.md) at each step. Verify each slice before building on it.

| Files | Responsibility |
|---|---|
| `pyproject.toml`, lockfile, `config.yaml` | Dependencies, exact model configuration, seeds, limits. |
| `tasks/`, `src/context_fidelity/adapters/sandbox.py` | Fixtures, reference solutions, immutable tests, environment conditions. |
| `src/context_fidelity/experiment.py`, `__main__.py` | Inspect orchestration and CLI. |
| `src/context_fidelity/evidence.py`, `contexts.py` | Event/version record, independent truth, four contexts, restoration variants. |
| `src/context_fidelity/score.py`, `analyze.py`, `plots.py` | Scoring, blinded review export, paired analysis, and figures. |
| `src/context_fidelity/inspect_export.py`, `tests/` | Scored Inspect exports and checks of provenance, evidence, and scoring. |
| `runs/`, `results/`, `README.md` | Raw records, review labels, figures, results, reproduction instructions. |

Join records by task ID, environment, history ID, context arm, and repetition. Every report must identify its immutable history, exact supplied context, and verdict evidence. Include those keys in Inspect metadata. Save scored exports separately from raw logs.

## Hour 0–1: Run one case

- [x] Connect the user-created remote, inspect Git state, and create a feature branch.
- [ ] After publication and the first hosted run, configure the main-branch ruleset and required `quality` check.
- [x] Run foundation checks. Add packaging metadata, the first acceptance test, and the real Docker integration test before completing this slice.
- [x] Check Docker and model access. Smoke-test local tool use, memory, latency, and context limits; switch to an available API during development if necessary.
- [x] Implement one small repair fixture and the five tools.
- [x] Run the actor through saved code, tests, and note; capture an Inspect log and versioned events. The smoke-test actor ended in prose instead of calling `finish_work`. Keep and label that permitted early termination.
- [x] Record throughput and estimate the full-run duration and any API cost.

**Done when:** Final files agree with independently recorded events, a saved trace can be opened, and the applicable CI checks pass locally. Hosted CI remains a separate check.

## Hours 1–3: Build the experimental controls

- [x] Implement A–D. Verify B preserves transcript contents; C uses only actor-visible evidence and fits its cap.
- [x] Implement the report schema, ground-truth labels, structured scorer, and blinded prose-review export.
- [x] Check current versus stale tests, partial suites, zero collected tests, failed/recovered writes, malformed reports, and prose contradicting structured fields.
- [x] Finish four development tasks and mechanically validate their reference solutions.

**Done when:** Evidence and scoring tests pass. The compact extract preserves required evidence without adding hidden evaluator conclusions. Typing passes, as do the coverage gates: 90% of statements and branches overall, and 95% of each per critical module.

## Hours 3–4: Pilot and freeze

- [x] Collect eight development histories and one report per context: 32 pilot reports.
- [x] Finish calibration checks for obstacle visibility, report formatting, summary retention, actual compression, and runtime.
- [x] Calibration confirmed substantial compression at the 384-token cap. No lower-cap comparison was needed.
- [x] Reporting did not saturate in development or held-out results. Every planned history and report was retained.
- [x] Prepare 12 distinct held-out fixtures; assign four each to blocked tests, partial tests, and recoverable note writes.
- [x] Freeze and hash the protocol/configuration, tasks, prompts, extractor, rubric, sample size, diagnostic-selection rule, and analysis.

**Fallback:** If measured throughput or review time cannot fit the day, amend the protocol before held-out evaluation to eight tasks: three blocked-test, three partial-test, and two recovery tasks. That yields 16 histories, 16 summaries, and 128 main reports. Label it a smaller pilot. Held-out effect sizes must never determine this choice.

**Done when:** A dated manifest fixes the experiment, with development and held-out outputs kept separate.

## Hours 4–6: Run the fixed experiment

- [x] Generate the 24 held-out histories, derive their four contexts, and freeze the 24 ordinary summaries.
- [x] Audit summary omissions before viewing report outcomes; lock eligible restoration cases using the predeclared selection rule.
- [x] Collect 192 reports in a reproducibly randomized order. Record timeouts, context overflow, cancellations, and other technical failures. Each generation gets one attempt; automatic retries are disabled.
- [x] Use development data to verify Inspect View metadata, scored exports, and trace navigation while inference runs.

**Done when:** Every planned report has a result or a documented technical failure. Inspect View can display histories, contexts, reports, and scored evidence.

## Hours 6–8: Review and analyze

- [ ] Review shuffled reports with arm labels hidden; inspect underlying evidence as needed and record any unavoidable unblinding.
- [x] Resolve diagnostic eligibility before reporting. Zero histories qualified; no restoration reports were generated. ED-002 remains untested.
- [x] Compute the paired C–D effect, task-cluster intervals, factual coverage, error components, retention, and raw counts. Keep B–A exploratory.
- [x] Generate structured-error/coverage, fact-retention, and paired-effect figures as vector PDFs and 300-dpi PNGs. Primary-outcome figures await review; no restoration figure exists because no case qualified.

**Done when:** The analysis separates summary errors from reporting errors and makes its uncertainty visible. Complete primary outcomes require human review; assistant labels remain provisional.

## Hours 8–10: Package the work

- [x] Complete the [results record](../experiments/results.md): findings, interpretation, alternatives, limitations, and deviations.
- [x] Open all 192 scored held-out records in Inspect View. Verify the filtered eight-report comparison and source evidence. Demo cases follow the disclosed task/environment/repetition ordering.
- [x] Verify commands for one-case execution, the configured experiment, and offline analysis. Document the environment and expected outputs.
- [x] Update both EDs with results and interpretation; complete independent assistant reviews of scientific calculations and engineering findings.
- [ ] Run hosted CI and review the PR before integration. Local checks passed; publication is pending.
- [ ] Record a two-minute video: question → actual trace/context comparison → aggregate results → what the evidence supports.

**Done when:** A reader can trace a claim to its execution evidence and reproduce the analysis from saved logs.

## Hours 10–12: Buffer and scope cuts

Use the buffer for infrastructure, scoring disagreements, and reproduction checks. Cut visual polish and extra comparisons first. Keep coverage thresholds, Docker integration, and review requirements intact. If the restoration diagnostic cannot finish, state that the proposed explanation remains untested. If the main run is incomplete, show the missing data and narrow the conclusion.

Changes to the frozen study require a documented amendment. Do not invent results, add activation probes, or expand into compression during task execution. A well-measured null result with working evaluation tools is a valid deliverable.
