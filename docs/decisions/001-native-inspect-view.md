# Use Inspect View for experiment review

**Accepted September 12, 2026, during development.**

The initial replay page duplicated work Inspect already does: show model messages, inspect tool traces, and display scores. Maintaining a second renderer would add code without improving the experiment.

Use Inspect View as the main interface. Export separate scored copies of the original logs through Inspect's public scoring API. Add task, environment, history, arm, repetition, and review status as metadata. Scores link to the source evidence and preserve the distinction between a known error and a verdict awaiting human review. Raw generation logs remain unchanged.

[Inspect View](https://inspect.aisi.org.uk/log-viewer.html) supports trace navigation, filtering, and portable log bundles. Its [offline scoring workflow](https://inspect.aisi.org.uk/scoring-workflow.html) lets us add verdicts without another model call. Matplotlib remains appropriate for the paired estimates and exportable research figures. Separate JSON files support blinded review.

Grafana is a reasonable choice for querying service metrics, logs, traces, and alerts. We do not need that operating model for this fixed experiment. Adding a metrics store, dashboard configuration, and another service would not improve evidence review. Reconsider it if the project becomes a continuously running evaluation service with latency, cost, throughput, or availability targets. [Grafana fundamentals](https://grafana.com/docs/grafana/latest/fundamentals/)

This choice does not make custom interfaces an antipattern in general. A dedicated annotation workflow could justify one later. The present task has a maintained interface already available in its evaluation framework.

Serve local results on loopback. A published Inspect bundle needs a server with HTTP Range support; Python's basic `http.server` is unsuitable. Deployment and access control are separate decisions if logs ever contain private tasks or customer data.
