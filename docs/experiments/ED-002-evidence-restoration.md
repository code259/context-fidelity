# ED-002: Does restoring omitted evidence repair reporting?

**Status:** Proposed diagnostic; not frozen or run.
**Parent:** [ED-001](ED-001-context-comparison.md). **Shared definitions:** [project spec](../project-spec.md).

## Motivation

ED-001 can reveal a difference between compression methods without explaining its cause. This diagnostic changes the evidence available within an ordinary summary to test whether a specific omission contributes to a reporting error. Its precedent and novelty limits are in the [project motivation](../project-spec.md#question-and-motivation).

## Hypothesis

Adding an omitted, decisive tool event improves correct, supported reporting more than adding a similarly sized event that does not resolve the missing status.

## Method

1. Audit ordinary summaries against their histories **before viewing reporting outcomes**.
2. In task-ID order, choose up to eight qualifying histories, at most one per base task. Require a raw omitted event which, together with the summary, resolves a status. Exclude fabricated opposite claims, which require a different correction.
3. Create two additions from actor-visible events: decisive evidence and a nondecisive control. Keep both within 128 tokens and within eight tokens of each other under the frozen tokenizer. Preserve event meaning; do not cut off a qualification or pad with invented events. Exclude and record cases without a valid match.
4. Keep the original summary unchanged in both variants. The total payload cap is 512 tokens; apply the same placement, reporting prompt, model, and decoding.
5. Generate two reports per variant/history: at most 32 reports. Reuse the main rubric and blinded review procedure.

Primary diagnostic effect: decisive-addition minus control-addition unreliable-report rate. Average repetitions within each selected base task, then use a paired task-cluster bootstrap with 10,000 resamples and a 95% interval. Report coverage, raw pairs, and selected/eligible counts. With very few qualifying tasks, emphasize individual differences and uncertainty.

Record omissions, matches, and selections in a manifest before generating these reports. If no history qualifies, mark the diagnostic not run; do not manufacture a qualifying omission. Treat technical missingness as in ED-001.

## Positive result and implications

Lower unreliability after decisive restoration, with coverage retained, supports a causal contribution of the added evidence within these selected summaries. An interval below zero strengthens that conclusion; a point estimate alone is tentative.

This identifies a behavioral information bottleneck. It does not establish an internal neural mechanism, and selected omission cases do not estimate the effect across all agent work.

## Negative or inconclusive result and implications

- Both additions help similarly: extra context or generic reconsideration may explain the change.
- Neither helps with reasonable precision: the tested omission-restoration explanation is unsupported; evidence interpretation remains a candidate.
- Decisive evidence hurts: check conflicting wording, event scope, and whether the supposed decisive event actually resolves the status.
- Wide intervals or few eligible cases: inconclusive.
- No omissions: this particular diagnostic has no eligible population, not a failed hypothesis test.

## Results

**Not run.** Record eligible/selected cases, event lengths, both error rates, paired effect/interval, factual coverage, failures, and the restoration plot in the [results record](results.md).

## Analysis and interpretation

**Pending results.** Explain whether restoration helps specifically, whether the source summary was incomplete or false, and whether effects survive the matched control. Link every claim to raw event/report IDs. Discuss salience and wording as remaining alternatives before proposing a follow-up.
