# Context compression: results

**Status: NOT RUN.** Populate only after evaluation. [Protocol](../project-spec.md) · [ED-001](ED-001-context-comparison.md) · [ED-002](ED-002-evidence-restoration.md) · [One-day plan](../plans/one-day.md)

## Configuration and completeness

- Frozen protocol/configuration hash and timestamp:
- Model, revision, backend, precision, chat template, decoding, and seeds:
- Development / held-out task counts:
- Histories, planned reports, completed reports, technical failures, and retries:
- Compression cap and actual context lengths:
- Deviations from the frozen protocol:

Keep development findings separate from held-out estimates.

## Main finding

Not measured. State the observed effect, its uncertainty, and whether useful factual coverage was retained.

| Context | Histories / reports | Unreliable reports | Unsupported claims | Invalid outputs | Factual coverage | Context tokens |
|---|---|---|---|---|---|---|
| A — Native | — | — | — | — | — | — |
| B — Full external | — | — | — | — | — | — |
| C — Compact evidence | — | — | — | — | — | — |
| D — Ordinary summary | — | — | — | — | — | — |

Error components can overlap. Report numerators and denominators.

- Primary paired C–D difference and 95% task-cluster interval:
- Coverage difference and interval; can a loss above five percentage points be ruled out?
- Exploratory B–A difference:
- Per-task differences, false denials, uncertainty, and technical missingness:
- Floor/ceiling effects or insufficient precision:

## Evidence retention and restoration

- Summary omissions and fabricated claims, audited before report outcomes:
- World accuracy versus support in the actual supplied context:
- Eligible/selected histories and selection-rule compliance:
- Decisive-restoration versus matched-control effect, counts, and uncertainty:
- If skipped or incomplete, reason:

## Figures and replay cases

Link actual plots, data, and selected case IDs: unreliability/coverage, fact retention, and restoration if run. Disclose the case-selection rule.

## Interpretation

1. What was directly observed?
2. Which behavioral explanation is supported, and by which comparison?
3. What alternatives remain, including conservatism and summarizer errors?
4. What cannot be concluded about other models, task execution, or internal mechanisms?
5. What single follow-up would best resolve the remaining uncertainty?

## Reproduction

Link the frozen manifest, task split, Inspect logs, exact contexts, raw reports, review labels, analysis code, and verified commands. End with the narrowest conclusion justified by the data.
