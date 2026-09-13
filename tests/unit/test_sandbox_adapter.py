"""Host-side contract tests inject only the Docker command boundary."""

import json
import selectors
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from context_fidelity.adapters.sandbox import (
    MAX_FILE_BYTES,
    CommandResult,
    DockerSandbox,
    SandboxError,
    run_command,
)
from context_fidelity.contracts import Environment, TaskSpec, source_version


def test_command_cancellation_reaps_the_real_child(monkeypatch: pytest.MonkeyPatch) -> None:
    children = []
    real_popen = subprocess.Popen

    def tracked_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        children.append(process)
        return process

    class InterruptedSelector(selectors.DefaultSelector):
        def select(self, timeout=None):
            raise KeyboardInterrupt

    monkeypatch.setattr(subprocess, "Popen", tracked_popen)
    monkeypatch.setattr(selectors, "DefaultSelector", InterruptedSelector)
    try:
        with pytest.raises(KeyboardInterrupt):
            run_command([sys.executable, "-I", "-c", "import time; time.sleep(30)"])
        assert len(children) == 1 and children[0].poll() is not None
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait()


def task() -> TaskSpec:
    return TaskSpec.model_validate_json(Path("tasks/dev-smoke.json").read_text())


@dataclass
class DockerBoundary:
    """Minimal Docker transport double: no generated code is executed."""

    files: dict[str, str] = field(default_factory=dict)
    commands: list[tuple[str, ...]] = field(default_factory=list)
    outcomes: bytes = (
        b'{"completed":true,"collected":["positive","zero","negative"],'
        b'"passed":["positive","zero","negative"],"failed":[],"detail":""}'
    )
    failure: str = ""
    snapshot_override: bytes | None = None
    write_mutation: bool = False

    def __call__(
        self,
        command: Sequence[str],
        *,
        input: bytes | None = None,
        timeout: float = 15,
        output_limit: int = 131_072,
    ) -> CommandResult:
        del timeout, output_limit
        self.commands.append(tuple(command))
        operation = command[-1]
        if command[1] == "run":
            operation = "start"
        elif command[1] == "rm":
            operation = "remove"
        elif "/workspace/runner.py" in command:
            operation = "tests"
        if self.failure == operation:
            return CommandResult(1, stderr=b"controlled infrastructure failure")
        if operation == "setup":
            payload = json.loads(input or b"{}")
            self.files = {payload["source_file"]: payload["source"]}
        elif operation == "write":
            payload = json.loads(input or b"{}")
            self.files[payload["path"]] = (
                "unexpected mutation" if self.write_mutation else payload["content"]
            )
        elif operation == "snapshot":
            return CommandResult(
                0,
                self.snapshot_override
                if self.snapshot_override is not None
                else json.dumps(self.files).encode(),
            )
        elif operation == "tests":
            return CommandResult(0, self.outcomes)
        return CommandResult(0)


def test_exact_write_versions_snapshot_and_finish_are_recorded() -> None:
    boundary = DockerBoundary()
    spec = task()
    with DockerSandbox(spec, Environment.NORMAL, runner=boundary) as sandbox:
        assert sandbox.list_files().message == "Files: solution.py"
        assert sandbox.read_file("solution.py").message == spec.initial_source
        saved = sandbox.write_file("solution.py", spec.reference_source)
        assert saved.write_content == saved.source_after == spec.reference_source
        assert saved.source_version == source_version(spec.reference_source)
        assert sandbox.write_file("fix-note.md", "é\n").success
        assert "3 bytes" in sandbox.events[-1].message
        assert sandbox.run_tests().full_suite
        assert sandbox.finish_work().success
        assert not sandbox.finish_work().success
        assert not sandbox.run_tests().success
        assert not sandbox.list_files().success
        assert not sandbox.read_file("solution.py").success
        assert not sandbox.write_file("solution.py", "later").success
        assert sandbox.snapshot()[0].content == spec.reference_source
        assert [event.event_id for event in sandbox.events] == [f"event-{i}" for i in range(1, 12)]
    sandbox.close()
    with pytest.raises(SandboxError, match="reused"):
        sandbox.__enter__()


@pytest.mark.parametrize(
    "tool", ["list_files", "read_file", "write_file", "run_tests", "finish_work"]
)
def test_closed_tool_calls_return_explicit_failure(tool: str) -> None:
    sandbox = DockerSandbox(task(), Environment.NORMAL, runner=DockerBoundary())
    args = (
        ("solution.py", "new source")
        if tool == "write_file"
        else ("solution.py",)
        if tool == "read_file"
        else ()
    )
    event = getattr(sandbox, tool)(*args)
    assert not event.success
    assert "not open" in event.message
    assert event.source_version == source_version(task().initial_source)


@pytest.mark.parametrize(
    "path", ["../solution.py", "/etc/passwd", "", "tests.json", "solution.py/child"]
)
def test_invalid_paths_never_reach_the_write_boundary(path: str) -> None:
    boundary = DockerBoundary()
    with DockerSandbox(task(), Environment.NORMAL, runner=boundary) as sandbox:
        assert not sandbox.write_file(path, "bad").success
        assert sandbox.events[-1].path == path
        assert not sandbox.read_file(path).success
        assert not any(command[-1] == "write" for command in boundary.commands)


def test_file_size_limit_and_recoverable_note_are_visible() -> None:
    boundary = DockerBoundary()
    with DockerSandbox(task(), Environment.RECOVERABLE_NOTE, runner=boundary) as sandbox:
        assert not sandbox.write_file("solution.py", "é" * (MAX_FILE_BYTES // 2 + 1)).success
        assert not sandbox.read_file("fix-note.md").success
        assert not sandbox.write_file("fix-note.md", "first").success
        assert sandbox.write_file("fix-note.md", "second").success
        assert sandbox.write_file("fix-note.md", " ").success
        assert "nonempty=False" in sandbox.events[-1].message
        assert sandbox.read_file("fix-note.md").message == " "


def test_blocked_runner_collects_nothing_without_running_a_worker() -> None:
    boundary = DockerBoundary()
    with DockerSandbox(task(), Environment.BLOCKED_TESTS, runner=boundary) as sandbox:
        event = sandbox.run_tests()
        assert not event.success and not event.full_suite
        assert event.test_ids == ()
        assert event.expected_test_ids == ("positive", "zero", "negative")
        assert not any("/workspace/runner.py" in command for command in boundary.commands)


def test_partial_scope_and_completed_assertion_failures_are_distinct() -> None:
    boundary = DockerBoundary(
        outcomes=b'{"completed":true,"collected":["positive"],"passed":[],"failed":["positive"],"detail":""}'
    )
    with DockerSandbox(task(), Environment.PARTIAL_TESTS, runner=boundary) as sandbox:
        event = sandbox.run_tests()
        assert event.success and not event.full_suite
        assert event.test_ids == event.failed_test_ids == ("positive",)
        assert "Subset only" in event.message
        assert event.expected_test_ids == ("positive", "zero", "negative")


def test_timeout_retains_already_observed_test_outcomes() -> None:
    boundary = DockerBoundary(
        outcomes=(
            b'{"completed":false,"collected":["positive","zero"],'
            b'"passed":["positive"],"failed":[],"detail":"test timeout"}'
        )
    )
    with DockerSandbox(task(), Environment.NORMAL, runner=boundary) as sandbox:
        event = sandbox.run_tests()
        assert not event.success and not event.full_suite
        assert event.test_ids == ("positive", "zero")
        assert event.passed_test_ids == ("positive",)
        assert event.message == "test timeout"


@pytest.mark.parametrize(
    "outcomes",
    [
        b"not json",
        b"{}",
        b'{"completed":true,"collected":["invented"],"passed":["invented"],"failed":[],"detail":""}',
        b'{"completed":false,"collected":["positive"],"passed":["positive","positive"],"failed":[],"detail":""}',
        b'{"completed":false,"collected":["positive"],"passed":[],"failed":["positive","positive"],"detail":""}',
        b'{"completed":false,"collected":["positive"],"passed":["positive"],"failed":["positive"],"detail":""}',
        b'{"completed":false,"collected":["positive"],"passed":["negative"],"failed":[],"detail":""}',
        b'{"completed":true,"collected":["positive"],"passed":["positive"],"failed":[],"detail":""}',
        b'{"completed":true,"collected":["positive","zero","negative"],"passed":["positive"],"failed":[],"detail":""}',
    ],
)
def test_malformed_or_inconsistent_harness_output_is_not_evidence(outcomes: bytes) -> None:
    with DockerSandbox(
        task(), Environment.NORMAL, runner=DockerBoundary(outcomes=outcomes)
    ) as sandbox:
        event = sandbox.run_tests()
        assert not event.success and not event.full_suite
        assert event.test_ids == event.passed_test_ids == event.failed_test_ids == ()
        assert event.expected_test_ids == ("positive", "zero", "negative")


@pytest.mark.parametrize("failure", ["start", "setup"])
def test_setup_failure_still_removes_the_container(failure: str) -> None:
    boundary = DockerBoundary(failure=failure)
    with pytest.raises(SandboxError, match="controlled infrastructure failure"):
        with DockerSandbox(task(), Environment.NORMAL, runner=boundary):
            pytest.fail("failed setup must not enter the context")
    assert boundary.commands[-1][1:3] == ("rm", "--force")


def test_write_or_test_command_failure_preserves_recorded_state() -> None:
    boundary = DockerBoundary()
    with DockerSandbox(task(), Environment.NORMAL, runner=boundary) as sandbox:
        boundary.failure = "write"
        assert not sandbox.write_file("solution.py", "new source").success
        assert sandbox.snapshot()[0].content == task().initial_source
        boundary.failure = "tests"
        assert not sandbox.run_tests().success
        assert "infrastructure failure" in sandbox.events[-1].message


@pytest.mark.parametrize("override", [b"not json", b"\xff", b"{}", b'{"solution.py":"tampered"}'])
def test_snapshot_rejects_invalid_or_unrecorded_state(override: bytes) -> None:
    boundary = DockerBoundary()
    with DockerSandbox(task(), Environment.NORMAL, runner=boundary) as sandbox:
        boundary.snapshot_override = override
        with pytest.raises(SandboxError, match="snapshot|artifacts"):
            sandbox.snapshot()
        assert not sandbox.list_files().success


def test_missing_docker_is_an_explicit_infrastructure_failure() -> None:
    def missing(
        command: Sequence[str],
        *,
        input: bytes | None = None,
        timeout: float = 15,
        output_limit: int = 131_072,
    ) -> CommandResult:
        if command[1] == "rm":
            return CommandResult(0)
        raise FileNotFoundError("docker unavailable")

    with pytest.raises(SandboxError, match="Docker command unavailable"):
        with DockerSandbox(task(), Environment.NORMAL, runner=missing):
            pytest.fail("Docker is required")


@pytest.mark.parametrize("timeout", [0, -1, 11, float("inf"), float("nan")])
def test_timeout_configuration_is_bounded(timeout: float) -> None:
    with pytest.raises(ValueError, match="test_timeout"):
        DockerSandbox(task(), Environment.NORMAL, test_timeout=timeout)


@pytest.mark.parametrize("image", ["", "--privileged"])
def test_invalid_image_cannot_inject_docker_flags(image: str) -> None:
    with pytest.raises(ValueError, match="image"):
        DockerSandbox(task(), Environment.NORMAL, image=image)


@pytest.mark.parametrize("field_name", ["initial_source", "source_file", "tests"])
def test_invalid_task_cannot_overwrite_harness_or_exceed_bounds(field_name: str) -> None:
    data = task().model_dump()
    data[field_name] = {
        "initial_source": "x" * (MAX_FILE_BYTES + 1),
        "source_file": "runner.py",
        "tests": [{"test_id": f"case_{index}", "expression": "True"} for index in range(65)],
    }[field_name]
    with pytest.raises(ValueError, match="limits|harness"):
        DockerSandbox(TaskSpec.model_validate(data), Environment.NORMAL)


def test_command_runner_streams_input_and_separates_stderr() -> None:
    # This trusted fixed process is not task/generated Python.
    result = run_command(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data); "
            "sys.stderr.write('diagnostic')",
        ],
        input=b"a" * 20_000,
    )
    assert result.returncode == 0
    assert result.stdout == b"a" * 20_000
    assert result.stderr == b"diagnostic"


@pytest.mark.parametrize(
    ("code", "kwargs"),
    [
        ("import time; time.sleep(2)", {"timeout": 0.05}),
        ("print('x' * 10000)", {"output_limit": 10}),
    ],
)
def test_command_runner_kills_timed_out_or_noisy_processes(
    code: str, kwargs: dict[str, float]
) -> None:
    if "timeout" in kwargs:
        with pytest.raises(SandboxError, match="timeout"):
            run_command([sys.executable, "-I", "-c", code], timeout=kwargs["timeout"])
    else:
        with pytest.raises(SandboxError, match="output limit"):
            run_command(
                [sys.executable, "-I", "-c", code], output_limit=int(kwargs["output_limit"])
            )


def test_command_runner_handles_a_process_that_closes_stdin_early() -> None:
    result = run_command([sys.executable, "-I", "-c", "pass"], input=b"x" * 1_000_000)
    assert result.returncode == 0
    assert result.stdout == result.stderr == b""


def test_interrupted_setup_still_cleans_up_the_container() -> None:
    class InterruptedBoundary(DockerBoundary):
        def __call__(
            self,
            command: Sequence[str],
            *,
            input: bytes | None = None,
            timeout: float = 15,
            output_limit: int = 131_072,
        ) -> CommandResult:
            if command[-1] == "setup":
                raise KeyboardInterrupt
            return super().__call__(
                command, input=input, timeout=timeout, output_limit=output_limit
            )

    boundary = InterruptedBoundary()
    with pytest.raises(KeyboardInterrupt):
        with DockerSandbox(task(), Environment.NORMAL, runner=boundary):
            pytest.fail("interruption must propagate")
    assert boundary.commands[-1][1:3] == ("rm", "--force")


def test_write_integrity_failure_cannot_emit_a_false_failed_write_after_mutating_source() -> None:
    boundary = DockerBoundary(write_mutation=True)
    with DockerSandbox(task(), Environment.NORMAL, runner=boundary) as sandbox:
        with pytest.raises(SandboxError, match="artifacts"):
            sandbox.write_file("solution.py", "new source")
        assert not any(event.tool_name == "write_file" for event in sandbox.events)


def test_single_test_cannot_claim_a_partial_environment() -> None:
    spec = task().model_copy(update={"tests": task().tests[:1]})
    with pytest.raises(ValueError, match="at least two"):
        DockerSandbox(spec, Environment.PARTIAL_TESTS)
