# ED-002: Does restoring omitted evidence repair reporting?

**Status:** Frozen. Eligibility will be audited before held-out reporting; the diagnostic has not run.
**Parent:** [ED-001](ED-001-context-comparison.md). **Shared definitions:** [project spec](../project-spec.md).

## Motivation

ED-001 can find a difference between compression methods without explaining why it occurs. This experiment adds one omitted tool event back to an ordinary summary. If that helps more than a similarly sized event that leaves the missing status unresolved, the omission becomes a stronger explanation for the error. The [project motivation](../project-spec.md#question-and-motivation) describes the earlier work and limits of the novelty claim.

## Hypothesis

Adding an omitted, decisive tool event improves correct, supported reporting more than adding a similarly sized event that does not resolve the missing status.

## Method

1. Audit ordinary summaries against their histories **before viewing reporting outcomes**.
2. In task-ID order, choose up to eight qualifying histories, at most one per base task. Require a raw omitted event which, together with the summary, resolves a status. Exclude fabricated opposite claims, which require a different correction.
3. Create two additions from actor-visible events: decisive evidence and a nondecisive control. Serialize each complete raw event with `ToolEvent.model_dump_json()`. Keep both within 256 tokens and within eight tokens of each other under the frozen tokenizer. Preserve every field; do not cut off a qualification or pad with invented events. Exclude and record cases without a valid match.
4. Keep the original summary unchanged in both variants. The total payload cap is 656 tokens; apply the same placement, reporting prompt, model, and decoding.
5. Generate two reports per variant/history: at most 32 reports. Reuse the main rubric and blinded review procedure.

The diagnostic effect is the unreliable-report rate with decisive evidence minus the rate with the control addition. Average repetitions within each selected base task, then use a paired task-cluster bootstrap with 10,000 resamples and a 95% interval. Report coverage, raw pairs, and the eligible and selected counts. If few tasks qualify, emphasize individual differences and uncertainty.

Record omissions, matches, and selections in a manifest before generating reports. If no history qualifies, mark the diagnostic not run. Do not manufacture an omission to keep the experiment going. Handle technical missingness as in ED-001.

## Development feasibility amendment

September 12, 2026, before held-out evaluation: only 2/44 development events and 0/8 test events fit the original 128-token cap. Raise the raw-event cap to 256 tokens and the total payload cap from 512 to 656. Keep the eight-token matching tolerance. Every raw field stays intact; long writes and unmatched events remain ineligible.

An independent assistant read all eight development summaries and histories without seeing reporting outcomes. It found **zero eligible decisive omissions**. Normal summaries retained the final statuses. The partial-suite summary invented a full-pass claim, while blocked-suite summaries kept the blocking fact but contradicted it in their completion prose. The recovered note-write failure no longer determined the final status.

The larger cap does not make these cases eligible. These are development judgments by an assistant; human adjudication and the held-out diagnostic remain pending.

## Positive result and implications

If decisive restoration lowers unreliability while preserving coverage, the added evidence contributed to better reporting in the selected summaries. An interval below zero strengthens that conclusion. A point estimate alone remains tentative.

The result would locate a problem in the evidence available to the reporter. It would not reveal an internal neural mechanism. Because cases are selected for omissions, their effect does not estimate the effect across all agent work.

## Negative or inconclusive result and implications

- If both additions help similarly, extra context or another chance to reconsider may explain the change.
- If neither helps and the interval is sufficiently narrow, the tested restoration hypothesis is unsupported. Difficulty interpreting evidence remains a possibility.
- If decisive evidence hurts, check for conflicting wording, scope errors, or an event that does not actually resolve the status.
- Wide intervals or few eligible cases leave the result inconclusive.
- With no qualifying omissions, the diagnostic has no eligible cases. The hypothesis has not been tested.

## Results

**Not run.** Record eligible and selected cases, event lengths, both error rates, the paired effect and interval, factual coverage, failures, and the restoration plot in the [results record](results.md).

## Analysis and interpretation

**Pending results.** Compare decisive restoration with its matched control. Distinguish an incomplete summary from a false one, and link each claim to raw event and report IDs. Explain whether salience or wording could still account for the result, then state what a follow-up would test.
