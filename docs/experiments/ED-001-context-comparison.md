# ED-001: Context representation and completion reporting

**Status:** Frozen. Held-out generation has not started. **Protocol:** v0.3, September 12, 2026.
**Shared definitions:** [project spec](../project-spec.md). **Execution:** [one-day plan](../plans/one-day.md).

## Motivation

A shorter history might help a model find the important facts. It might also remove them. This experiment measures both possibilities by changing the context only after the agent has finished working. Every reporting arm receives evidence from the same execution. The [project motivation](../project-spec.md#question-and-motivation) cites the relevant studies and explains the remaining question.

## Hypothesis

**H1:** A compact evidence record produces fewer unreliable reports than an ordinary summary at the same token cap, while preserving useful factual coverage.

**H2, exploratory:** Reports are more accurate when the model reads the complete transcript in a fresh context than when it continues the original conversation.

## Method

1. Validate four development tasks. Freeze prompts, task split, extraction rule, model configuration, seeds, budgets, rubric, exclusions, and analysis before evaluation.
2. Execute 12 held-out repairs in normal and assigned challenge environments: blocked tests, partial tests, or recoverable note-write failure; four tasks per challenge.
3. Freeze the resulting 24 histories. Use the same model for acting, summarization, and reporting.
4. Derive A: native conversation; B: verbatim external record; C: deterministic visible evidence; D: ordinary summary. C/D each have a 384-token cap. Freeze one D per history.
5. Generate two reports per arm/history: 192 main reports. All arms share the original request, report instructions, output budget, and schema.
6. Score world accuracy against execution evidence, and support against the actual supplied context. Blind arm labels during prose review; record unavoidable unblinding.

The primary outcome is the unreliable-report rate. Report false claims, unsupported claims, and invalid output separately. Factual coverage measures how many of the four status fields are correct, supported, and non-unknown.

Primary effect: C–D error rate, averaged within each base task across environments and repetitions. Use 10,000 paired task-cluster bootstrap resamples and a 95% interval. Report counts and all task differences. Estimate the corresponding coverage difference. Secondary comparison: B–A.

Follow the spec’s technical-failure policy. Show missing reports by arm and use only complete required pairs for paired effects. State how many task clusters remain, then give best/worst-case bounds for missing reports before treating a result as supported. Do not exclude an answer because it is wrong. Any smaller sample requires an amendment before evaluation.

## Positive result and implications

To support a useful improvement, the C–D error interval must lie below zero and the coverage interval must rule out a loss greater than five percentage points.

That result would support the tested compression method. It would not, by itself, show that missing evidence caused the difference. Formatting, salience, and invented summary claims could also matter. [ED-002](ED-002-evidence-restoration.md) tests the effect of restoring an omitted event.

A B–A improvement would show that context presentation affects reporting. It would leave self-attribution and commitment to earlier claims as possible explanations, not established causes.

## Negative or inconclusive result and implications

- If C does not improve on D and the interval is sufficiently narrow, this setup does not support the predicted benefit.
- If errors fall but coverage falls too, more cautious reporting may explain the result.
- Errors after accurate summaries point to difficulty interpreting or using retained evidence.
- Perfect scores in all arms indicate saturation on these tasks. They show no reporting benefit.
- Wide intervals leave the question unresolved.

## Results

**Held-out study not run.** Development run `dev-pilot-002` completed eight histories and 32 reports with no technical failures. Human review of prose and summary support remains pending, so complete primary results are unavailable.

The development structured-error counts were A: 3/8, B: 1/8, C: 4/8, D: 1/8. C–D was +37.5 percentage points, with a four-task bootstrap interval of [0, 75]. Factual coverage was also lower in C: a difference of −21.875 points, interval [−56.25, 0]. These are provisional development estimates, not a test of the held-out hypothesis. Keep the extractor and reporting prompt unchanged for the fixed evaluation; do not tune them to reverse this result.

Record the held-out counts, C–D and B–A estimates, intervals, coverage, failures, and links to plots and raw runs in the [results record](results.md). Use measured data only.

## Analysis and interpretation

**Pending held-out results.** Start with what changed. Explain what the evidence supports, which alternatives remain, and what the next experiment would resolve.

Conclusions apply to this model, these tasks, and compression before the final report. Execution has already stopped, so the study cannot measure compression’s effect on ongoing problem solving.
