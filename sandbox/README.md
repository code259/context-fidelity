# Docker execution boundary

`DockerSandbox` in [the adapter](../src/context_fidelity/adapters/sandbox.py)
creates one new container per history and exposes only `list_files`, `read_file`,
`write_file`, `run_tests`, and `finish_work`. Callers must use its context manager
and show each returned `ToolEvent.model_dump_json()` to the actor; the concise
`message` alone does not contain all source-version and scope metadata.

The default image is the immutable multi-platform reference
`python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2`.
Docker's ordinary context/environment resolution applies, including `DOCKER_HOST`.
There is no bind mount, credential environment forwarding, Docker socket mount,
network, privileged mode, or actor shell tool.

The container has a read-only root, drops every capability, enables
`no-new-privileges`, and starts its process and test runner as UID/GID 65534.
Limits are 128 MiB memory including swap, one CPU, 32 processes, a 1 MiB workspace,
and a separate 16 MiB temporary filesystem. Fixed control operations use a root
`docker exec` only to initialize, atomically save, or read root-owned files.
The source, note, test definitions, and both harness scripts are root-owned and
read-only to tested Python. The actor can read/write only its source basename
and `fix-note.md`. The supplied expressions are already included in its task.

A fixed supervisor executes each supplied expression in a separate Python worker
under one total test deadline of at most 10 seconds. Expressions may use
`solution.function(...)`; bare names also work. Generated stdout/stderr goes to a
bounded temporary file, never to the authoritative result channel. A separate
pipe carries the worker outcome; a missing result, timeout, or abnormal exit is
an incomplete run, even if generated stdout printed a success-shaped JSON object.
The supervisor kills each worker's process group; context exit forcibly removes
the container and all remaining processes.

Each file is limited to 32,768 UTF-8 bytes; tasks have at most 64 tests and each
expression has the same byte limit. Docker command output is capped at 512 KiB,
which accommodates JSON escaping of both maximum-size files. Worker file output
is capped at 4 KiB. Limits fail explicitly; outputs are not silently truncated
into success evidence. `snapshot()` independently reads exact UTF-8 file bytes
and rejects state inconsistent with recorded writes. An inconsistent read-back
after a successful transport raises an infrastructure error rather than emitting
a false failed-write event for changed source.

Blocked tests collect nothing. Partial tests always choose the first half of the
supplied IDs, with a minimum of one, and explicitly report the subset; that
environment requires at least two supplied tests. The recoverable-note condition
fails exactly the first otherwise-valid note write. Assertion failures still
mean the harness completed successfully; only completed outcomes for the entire
supplied suite establish `full_suite=True`.

The Docker daemon and fixed harness are trusted. This is process and filesystem
isolation for the repair experiment, not a claim that an arbitrary hostile Python
program cannot attack its in-process evaluator through reflection or deliberately
write to the worker's outcome pipe. Do not interpret these tests as adversarial
judge-hardening certification.

Run the required real boundary checks with a working Docker daemon:

```sh
uv run pytest tests/integration/test_sandbox.py -m docker
```

These tests never skip when Docker is unavailable. Unit tests replace only the
Docker CLI boundary; generated task source is never executed on the host.
