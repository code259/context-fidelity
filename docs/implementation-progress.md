# Implementation progress

Plan: [one-day execution](plans/one-day.md). Began September 12, 2026.

## Current state

- Engineering foundation validated: tests, Ruff, strict mypy, local links, lockfile, pre-commit. Baseline commit: `caf7082`; implementation branch: `feat/context-compression-eval`.
- Dedicated Vast SSH key generated outside the repository; public key supplied to the user.
- A5000 connected and verified: 24,564 MiB, compute capability 8.6; private vLLM endpoint passed an Inspect smoke call.
- Dedicated Colima Docker VM installed with host mounts disabled. Pinned Python image downloaded; first real integration tests pass. Vast cannot run nested Docker, so model serving and sandbox execution use separate hosts.
- Local OpenAI credential source exists outside the repository. Reuse is explicitly authorized. No secret value is recorded here.

## Execution queue

1. Complete: package/contracts, real Docker sandbox, Inspect model integration.
2. Complete: evidence/context/scoring controls, development and held-out fixtures; all 16 initial/reference pairs validated through real Docker.
3. In progress: development calibration, offline results export, figures; held-out freeze pending.
4. Pending: main run and evidence-restoration diagnostic.
5. Pending: blind review, paired analysis, figures, viewer and reproduction.
6. Pending: final verification, PR/CI and demo recording.

## Decisions and review

- Work in the user-specified checkout on feature branches. Preserve the engineering foundation as a baseline commit before iteration; a separate worktree is unnecessary for sequential writes in this new repository.
- Reuse the authorized key from its local file only in process memory if API access is needed. Never persist it in source, prompts, logs, or remote sandboxes.
- No held-out model outcomes exist yet. Resolve integration and scoring ambiguities during development, recording protocol amendments before freezing.
- Task 1A independently reviewed: 81 tests, 100% statement/branch coverage for contracts and evidence. Review caught inconsistent full-suite metadata; fixed with a regression. Failed invalid-path attempts are preserved while successful traversal remains forbidden.
- Compact contexts preserve every write/test event, use content-equality version aliases, and raise on overflow. No oracle labels or final-file facts enter them. Fifteen focused tests, 100% statement/branch coverage.
- Local VM image had a dangling systemd-resolved link although the resolver service was absent. Repointed the dedicated VM resolver to its DHCP-provided DNS; image pull then succeeded.
- Verification uses source-content identity: a completed full run applies to identical final bytes, including edit-away/restore. Tests and environment remain fixed. An ordinary later change with different bytes is stale.
- Independent sandbox review caught valid dataclass failures from missing module registration and unreaped command processes on cancellation. Both reproduced before fixes and independently rechecked afterward.
- First real development actor completed a repair in nine seconds: five tool events, code/note saved, complete supplied suite passed. The actor terminated in prose rather than calling `finish_work`; that premature terminal message remains in its history as required. This used the initial `vllm/` Inspect provider and is only a connectivity/execution smoke, not a held-out result.
- Independent provider review exposed hidden OpenAI SDK retries beneath Inspect's retry setting. The replacement public `openai-api/local` adapter explicitly controls SDK retries/timeouts while serving the same pinned model through vLLM. Exact message normalization, cancellation failure sidecars, public report schema, and explicit top-k forwarding are fixed and independently reviewed.
- Implementation commit `5ec4a9f` passed 359 offline tests and 11 actual Docker integration tests. Statement coverage was 1649/1650 and branch coverage 489/492; all four critical modules reached 100% of each. Ruff, strict mypy, lock validation, package build, local links, and pre-commit passed. Hosted CI has not run.
- `dev-pilot-001` failed all eight actors because the SSH tunnel timed out. Server health remained good. Keep-alives restored transport; `connection-smoke-002` then completed five real tool events through the current Inspect adapter. The failed pilot is retained and will not be overwritten.
- Independent pipeline/scoring review caught acceptance of files added after collection freeze and an off-by-one review-export repetition constraint. Both are fixed with regression tests and independently rechecked. Reporting now also freezes its outputs, raw logs, audits, and explicit failure cells before analysis.
- Static replay was checked in the local browser using `connection-smoke-002`: context switching and expansion of exact test evidence work. Numerical plotting supports observed denominators, task-cluster intervals, missingness bounds, and visibly provisional review status; PNG/PDF exports have been visually inspected.
