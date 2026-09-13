# Implementation progress

Plan: [one-day execution](plans/one-day.md). Began September 12, 2026.

## Current state

- Engineering foundation validated: 27 tests, Ruff, strict mypy, local links, lockfile, pre-commit.
- Dedicated Vast SSH key generated outside the repository; public key supplied to the user.
- Hardware corrected to A5000. Awaiting its connection information.
- Docker is not installed locally. Real Docker verification remains required; determine the available execution host before running generated code.
- Local OpenAI credential source exists outside the repository. Reuse is explicitly authorized. No secret value is recorded here.

## Execution queue

1. In progress: package/contracts, first real sandbox task and model integration.
2. Pending: evidence/context/scoring controls and development fixtures.
3. Pending: calibration, held-out fixtures, frozen manifest.
4. Pending: main run and evidence-restoration diagnostic.
5. Pending: blind review, paired analysis, figures, viewer and reproduction.
6. Pending: final verification, PR/CI and demo recording.

## Decisions and review

- Work in the user-specified checkout on feature branches. Preserve the engineering foundation as a baseline commit before iteration; a separate worktree is unnecessary for sequential writes in this new repository.
- Reuse the authorized key from its local file only in process memory if API access is needed. Never persist it in source, prompts, logs, or remote sandboxes.
- No held-out data or model outcomes exist yet. Resolve integration and scoring ambiguities during development, recording protocol amendments before freezing.
