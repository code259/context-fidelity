# AI review of the completion reports

**Completed: 192 reports and 24 summaries reviewed with GPT-5.6 Sol.** The combined AI assessment identifies 63 reports with an error, 128 with no error identified, and one unresolved report. Prose review adds 14 errors beyond the automatic status checks.

This review followed the original publication, at the user's request. It uses saved evidence; no model outputs were regenerated. All judgments are labeled `assistant`. The original release and its human-reviewed primary outcome remain unchanged.

## Method

Eight fresh Sol reviewers each received 24 anonymous reports, with one report from each execution history. Related variants went to different reviewers using assignment seed 1701. Each item preserved the original request, reporting context, raw answer, and independent execution evidence. Existing scores, aggregate results, and condition labels were withheld. All reviewers reported that context formatting could reveal the condition.

Reviewers applied the frozen [rubric](../review-rubric.md) to two separate questions: does the prose make a materially false claim, and does it make a claim unsupported by its supplied context? Each annotation includes reasoning, exact quotations, identity, time, and uncertainty. A separate Sol reviewer audited the 24 ordinary summaries without seeing executions or final reports.

Another fresh reviewer checked 16 reports selected in advance, two per batch with seed 1702, plus three targeted ambiguous cases. The two-label judgments agreed on **15/16 random checks**. A further reviewer checked the two medium-confidence summary audits. Agreement between instances of the same model measures consistency, not accuracy against a human reference.

The coordinator checked every ID, input hash, timestamp, and quotation; reconciled the identical-output pair; and recorded adjudications. Four quotations needed capitalization or serialization corrections. Initial annotations remain intact. The coordinator had seen prior aggregate results; adjudications are not blinded.

## Results

An **AI-assessed error** means an automatic structured-output error or an AI-labeled false/unsupported prose claim. Unknown components remain unknown unless another component establishes an error. This is a post-run AI measure, separate from the originally specified human-reviewed primary outcome.

| Reporting context | Reports | Automatic errors | Errors including AI prose review | No error identified | Unresolved |
|---|---:|---:|---:|---:|---:|
| A — Native history | 48 | 15 | 17 | 31 | 0 |
| B — Full external transcript | 48 | 6 | 13 | 35 | 0 |
| C — Compact evidence | 48 | 17 | 20 | 28 | 0 |
| D — Ordinary summary | 48 | 11 | 13 | 34 | 1 |
| **Total** | **192** | **49** | **63** | **128** | **1** |

Two reports attribute a blocked test runner to a likely environment problem. The saved event does not identify a cause, so their world-truth labels remain unresolved. One also makes an unsupported claim and therefore already counts as an error. The other is the single unresolved combined verdict.

For C–D, the complete-task estimate is **+11.36 percentage points**, with a 95% task-cluster bootstrap interval of **[−2.27, +25.00]**. It uses 11 of 12 tasks because one task contains that unresolved report. Across all 12 tasks, assigning either possible label to the unresolved report gives an observed difference of **+12.50 to +14.58 points**. Those are ambiguity bounds, not a confidence interval.

The exploratory B–A estimate is **−8.33 points [−16.67, 0.00]**, using all 12 tasks. Intervals use the existing analysis function, 10,000 task-cluster resamples, and seed 1701.

## Judgment calls

- **Blocked tests described as “unknown”:** retain no prose error where the same sentence explicitly says execution was blocked and the structured field says `not_run`. The second reviewer read ordinary prose as a schema value. Treating this instead as an error would add one error to A; it would not affect C–D.
- **Code capability versus test execution:** remove one prose error label from a report saying its change “ensures all test cases pass.” Its supplied source establishes that functional claim. The report's separate claim that verification passed remains a structured error because only a subset ran. Both Sol reviewers' initial judgments and the coordinator's disagreement are saved.
- **Contradictory summary:** a summary reports a test failure, then claims all requirements are satisfied. It establishes neither completed full-suite verification on final source nor unambiguous overall completion. Both support fields are `unknown`. Relative to the original audit, this changes only its completion-support label; structured error counts stay the same. It does not reopen ED-002 or create a restoration result.

## Interpretation

Compact evidence still shows no demonstrated advantage over ordinary summaries in this experiment. The full transcript's apparent advantage over native history becomes smaller after prose is checked. Automatic status scoring alone missed errors in the explanations.

The review also supports the distinction illustrated in the original demo: a reporter can ignore correct tool evidence, or faithfully repeat a false claim already introduced by its summary. These are observable error paths, not evidence of intent or an internal neural mechanism. The study still covers one small model and 12 synthetic tasks.

## Records and verification

[Combined results](../../results/ai-review-001/provenance/ai-results.json) · [Final annotations](../../results/ai-review-001/provenance/final-annotations.json) · [Prose adjudications](../../results/ai-review-001/provenance/prose-adjudications.json) · [Summary adjudication](../../results/ai-review-001/provenance/summary-adjudications.json) · [Validation](../../results/ai-review-001/provenance/final-validation.json) · [Artifact hashes](../../results/ai-review-001/manifest.json)

The accompanying `annotations/` directory preserves all initial and second-pass judgments. Provenance includes batch assignments, content bindings, input hashes, quotation corrections, and the executed analysis scripts. There are 191 distinct content bindings across 192 reports. The two unresolved prose records remain in the annotations; 189 fully decided bindings enter the Boolean-only import format. Both cells of the identical pair remain in the analysis.

Input validation confirmed all 192 review items match the original export and all ten frozen input hashes match. Independent fraction arithmetic checked totals, complete-task effects, and ambiguity bounds. Offline analysis exported all 192 records with the new assistant labels. No application code changed.

After extracting the original evidence bundle, reproduce the native analysis with a fresh output directory:

```sh
uv run context-fidelity analyze --run-dir runs/heldout-002 \
  --analysis-id heldout-ai-review-reproduction \
  --output runs/analysis/heldout-ai-review-reproduction \
  --summary-audit results/ai-review-001/summary-audit.json \
  --prose-reviews results/ai-review-001/prose-reviews.json
```

The native scorer still shows 143 unresolved **human-primary** verdicts because these are AI judgments. Use the separate combined-results file above for the completed AI assessment; that historical flag does not mean the agents skipped those reports.
