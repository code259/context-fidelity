# Implementation status

Updated September 12, 2026, in America/Los_Angeles. The [one-day plan](plans/one-day.md) defines the work; the [results record](experiments/results.md) separates findings from unfinished review.

## Built and verified

Context Fidelity now runs small coding tasks in Docker, freezes their histories, constructs four reporting contexts, and collects model reports through Inspect. Typed records connect every verdict to its task, context, source version, and tool evidence. Offline analysis preserves raw responses and exports scored copies to Inspect View.

The final source at `f8fa80c` passed **526 offline tests**. The **11 real Docker integration tests** also passed. Fresh prepublication tests passed again on `128baf4`, followed by the same application and Docker suites in [hosted CI](https://github.com/code259/context-fidelity/actions/runs/34739191004). Application coverage is 2,370/2,378 statements (99.66%) and 685/694 branches (98.70%). Evidence extraction, context construction, scoring, and paired analysis each have 100% statement and branch coverage. Ruff, mypy, locked dependencies, local documentation links, package builds, and pre-commit checks passed.

The architecture review removed the custom HTML renderer. Inspect View handles traces and scores; Matplotlib produces research figures. Runtime preflight, atomic phase records, and separate provisional/reviewed verdicts address the material engineering findings. See the [engineering review](engineering-review.md) for the changes and their evidence.

Preparing the human-review handoff exposed a review-tracking defect for malformed reports. The correction preserves supplied prose labels and completes review only when the required human judgments are present. All 544 offline tests passed after the fix, with 2,372/2,380 statements (99.66%) and 687/696 branches (98.71%) covered. It leaves all 192 original unreviewed cells and every reported effect unchanged. [PR checks](https://github.com/code259/context-fidelity/pull/3/checks) track hosted verification of the follow-up.

## Experiments

- All 16 development and held-out fixtures passed real Docker validation: each initial implementation fails and each reference solution passes its full supplied suite.
- `dev-pilot-001` failed because its SSH tunnel timed out. Its eight failed attempts remain available. Keep-alives fixed the transport; the replacement has a separate run ID.
- `dev-pilot-002` completed eight histories, eight summaries, and 32 reports without a technical failure. Its native scored exports and figures are complete. Human review remains pending.
- `heldout-001` was prepared but never generated data. A CLI startup fix changed the frozen source, so a recorded amendment replaced it with `heldout-002` before any held-out model calls.
- `heldout-002` was frozen against `8f8fc08`. All 24 histories, 24 summaries, and 192 reports completed without a technical failure. The pre-report audit found zero eligible restoration cases. Native scored exports, paired estimates, and figures are complete in `heldout-analysis-002`.

## Remaining work

The results and both EDs now record the measured interpretation. A short [Inspect walkthrough](demo.md) identifies the actual demonstration cases. Human prose and summary-support review remain necessary for the complete primary outcome: 143 verdicts are unresolved, and all 192 reports lack complete human review. Assistant judgments cannot satisfy that requirement. Video recording remains open.

The branch is public in [PR #3](https://github.com/code259/context-fidelity/pull/3), against foundation commit `caf7082` on `main`. Both verified archives are published in the [provisional release](https://github.com/code259/context-fidelity/releases/tag/v0.1.0-provisional); GitHub's asset digests match the local checksums. Hosted CI passed, the main-branch ruleset is active, and secret scanning and push protection are enabled. The PR remains unmerged.

The [human-review handoff](human-review.md) is prepared locally with 24 summaries, the unchanged 192 anonymous report items, and blank worksheets. No human judgments have been recorded. Completed labels will create a separate analysis.
