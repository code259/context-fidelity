# Engineering review

Reviewed September 12, 2026, after the first complete development pilot.

The project is a local research evaluation. Its production standard is reproducible evidence, bounded execution, explicit failures, and code another engineer can maintain. It does not need the operating infrastructure of a hosted service.

| Finding | Change | Evidence |
|---|---|---|
| Custom replay duplicated Inspect's interface | Removed the HTML renderer; added native scored exports, readable labels, experiment tags, and separate review records | Actual pilot logs opened in Inspect; offline exports preserve original messages, outputs, and trace events |
| Some phase records could be left partially written | Routed them through the exclusive atomic JSON writer | Failure-injection tests verify no partial published artifact |
| Runtime setup errors consumed a run ID | Added `doctor` and preflight checks before collection/reporting starts | Live tokenizer, endpoint, Docker, and image checks passed; tests verify no phase marker on failed readiness |
| Plotting initialization delayed unrelated CLI commands | Load presentation code only during offline analysis | Preparation completed in 0.96 seconds after a cold-start reproduction exposed font-cache work |
| Native message metadata changes broke reanalysis | Accept Inspect's carried-message source reset while checking every other field | Regression against real development histories plus content, role, tool-call, ID, and metadata tampering tests |
| Pending summary judgments could be promoted or rejected during export | Keep assistant-only support judgments provisional in all summary variants | Regressions cover ordinary, restored, and control summaries; definite errors still cannot become pending |
| Review gaps and export failures could look complete | Preserve missing verdicts; write a package manifest only after output and input checks finish | Tests cover missing cells, interrupted exports, changed inputs, and non-overwrite behavior |

The existing foundation also provides pinned dependencies and Actions, typed contracts, model-free PR tests, statement and branch coverage gates, real Docker integration, source-bound verification, single-attempt generation, and separate development/held-out records. Exact-source verification prevents partial or stale test results from becoming a full pass.

Keep the limits clear. The sandbox runs a small fixed Python task suite; it is not an adversarial Python containment benchmark. The model API stays on loopback behind an SSH tunnel. The CLI runs phases serially, and an interrupted phase needs a new run ID. These are deliberate constraints for this study. A shared service would need separate decisions about authentication, access control, job scheduling, persistence, monitoring, and adversarial isolation.

GitHub Actions is configured, but hosted CI and remote branch rules remain pending publication. Local checks are not evidence that those remote controls are active. Full primary outcomes also remain pending required human review.

Final source `f8fa80c` passed 526 offline tests, including a full rerun after the held-out figure labels were shortened to prevent overlap. The 11 real Docker integration tests passed on unchanged sandbox code. Statement coverage is 99.66%; branch coverage is 98.70%. Independent assistant review reproduced the held-out counts and paired intervals. Browser verification confirmed all 192 native logs, an eight-report filtered comparison, and the selected case's scores and evidence. Use Inspect's Raw control for exact JSON; its Markdown view can interpret schema dollar signs as math.
