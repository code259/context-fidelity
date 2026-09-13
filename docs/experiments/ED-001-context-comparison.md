# ED-001: Context representation and completion reporting

**Status:** Proposed; not frozen or run. **Protocol:** v0.3, September 12, 2026.
**Shared definitions:** [project spec](../project-spec.md). **Execution:** [one-day plan](../plans/one-day.md).

## Motivation

Context presentation and evidence retention are plausible, separable influences on reporting. The [literature-backed motivation](../project-spec.md#question-and-motivation) explains the relevant findings and limits. This study measures final-report accuracy after actual work, with execution held fixed across reporting contexts.

## Hypothesis

**H1:** A compact evidence record yields fewer unreliable reports than an ordinary summary at the same token cap, while preserving useful factual coverage.

**H2, exploratory:** A fresh context containing the complete transcript improves reporting over continuation of the original conversation.

## Method

1. Validate four development tasks. Freeze prompts, task split, extraction rule, model configuration, seeds, budgets, rubric, exclusions, and analysis before evaluation.
2. Execute 12 held-out repairs in normal and assigned challenge environments: blocked tests, partial tests, or recoverable note-write failure; four tasks per challenge.
3. Freeze the resulting 24 histories. Use the same model for acting, summarization, and reporting.
4. Derive A: native conversation; B: verbatim external record; C: deterministic visible evidence; D: ordinary summary. C/D each have a 384-token cap. Freeze one D per history.
5. Generate two reports per arm/history: 192 main reports. All arms share the original request, report instructions, output budget, and schema.
6. Score world accuracy against execution evidence, and support against the actual supplied context. Blind arm labels during prose review; record unavoidable unblinding.

Primary outcome: unreliable-report rate, with false/unsupported claims and invalid output shown separately. Useful factual coverage is the fraction of four status fields that are correct, supported, and non-unknown.

Primary effect: C–D error rate, averaged within each base task across environments and repetitions. Use 10,000 paired task-cluster bootstrap resamples and a 95% interval. Report counts and all task differences. Estimate the corresponding coverage difference. Secondary comparison: B–A.

Follow the spec’s technical-failure policy. Report missingness per arm; calculate paired effects only on complete required pairs and disclose how many clusters remain. Give best/worst-case bounds for missing reports before treating a result as supported. No outcome-based exclusions. Any smaller sample must be fixed through a pre-evaluation amendment.

## Positive result and implications

A supported useful improvement requires the C–D error interval to lie below zero and the coverage interval to rule out a loss greater than five percentage points.

This would support the tested evidence-preserving compression strategy. C–D alone cannot establish that missing evidence caused the difference: formatting, salience, or summarizer fabrications remain alternatives. [ED-002](ED-002-evidence-restoration.md) provides a direct behavioral diagnostic.

A B–A improvement supports sensitivity to context presentation. It does not independently identify self-attribution or narrative commitment.

## Negative or inconclusive result and implications

- C does not improve on D with a sufficiently narrow interval: the predicted benefit is unsupported within this setup.
- Lower errors accompanied by lower coverage: a conservative reporting tradeoff.
- Accurate summaries still cause errors: interpretation or use of retained evidence remains a candidate explanation.
- All arms accurate: saturation; no demonstrated improvement.
- Wide intervals: insufficient precision, not evidence of no effect.

## Results

**Not run.** Fill the [results record](results.md) with actual counts, C–D and B–A estimates, intervals, coverage, failures, and links to plots/raw runs. Do not substitute hypothetical figures.

## Analysis and interpretation

**Pending results.** Record: direct observation → supported explanation → competing explanations → limitations → next experiment.

Limit conclusions to this model, task distribution, and reporting-only intervention. The execution had already occurred; this is not a test of compression during ongoing problem solving.
