"""A fresh, resource-bounded Docker workspace for each execution history.

The Docker daemon is a trusted boundary. Generated Python is never run on the
host. It runs as nobody in a networkless container; root-owned task artifacts
are writable only through the fixed control program. The supervisor owns test
scope and never interprets generated stdout as an authoritative outcome.
"""

import json
import math
import os
import selectors
import subprocess
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr

from context_fidelity.contracts import (
    NOTE_PATH,
    Environment,
    FileSnapshot,
    TaskSpec,
    ToolEvent,
    ToolName,
    source_version,
)

DEFAULT_IMAGE = (
    "python:3.12.13-slim-bookworm@sha256:"
    "4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2"
)
MAX_FILE_BYTES = 32_768
# JSON can expand a byte to six escaped characters for both allowed files.
MAX_CONTROL_OUTPUT = 524_288
MAX_TESTS = 64

# These fixed scripts receive data via stdin, never shell interpolation. They
# are installed root-owned and read-only before any generated code is executed.
_CONTROL = r"""
import json, os, pathlib, sys
payload = json.load(sys.stdin)
root = pathlib.Path('/workspace')
op = sys.argv[1]
if op == 'setup':
    files = {payload['source_file']: payload['source'], 'tests.json': payload['tests'],
             'runner.py': payload['runner'], 'worker.py': payload['worker']}
    for name, content in files.items():
        path = root / name
        path.write_bytes(content.encode('utf-8'))
        path.chmod(0o444)
elif op == 'write':
    path = root / payload['path']
    temporary = root / '.pending-write'
    try:
        temporary.write_bytes(payload['content'].encode('utf-8'))
        temporary.chmod(0o444)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
elif op == 'snapshot':
    print(json.dumps({name: (root / name).read_bytes().decode('utf-8')
                     for name in payload['paths'] if (root / name).exists()}))
"""

_WORKER = r"""
import builtins, os, pathlib, resource, sys, types
# File output is bounded independently of the supervisor's read limit.
resource.setrlimit(resource.RLIMIT_FSIZE, (4096, 4096))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
source_path, expression, result_fd = sys.argv[1], sys.argv[2], int(sys.argv[3])
write_result = os.write
execute, evaluate, compile_source = builtins.exec, builtins.eval, builtins.compile
source = pathlib.Path(source_path).read_text(encoding='utf-8')
solution = types.ModuleType('solution')
solution.__file__ = source_path
sys.modules['solution'] = solution
namespace = solution.__dict__
try:
    execute(compile_source(source, source_path, 'exec'), namespace)
    passed = bool(evaluate(expression, dict(namespace, solution=solution)))
except BaseException:
    passed = False
write_result(result_fd, b'PASS' if passed else b'FAIL')
os.close(result_fd)
"""

_HARNESS = r"""
import json, os, pathlib, signal, subprocess, sys, tempfile, time
source_file, selected, timeout = sys.argv[1], json.loads(sys.argv[2]), float(sys.argv[3])
tests = json.loads(pathlib.Path('/workspace/tests.json').read_text())
by_id = {test['test_id']: test['expression'] for test in tests}
result = {'completed': True, 'collected': [], 'passed': [], 'failed': [], 'detail': ''}
deadline = time.monotonic() + timeout
for test_id in selected:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        result.update(completed=False, detail='test timeout')
        break
    result['collected'].append(test_id)
    read_fd, write_fd = os.pipe()
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            [sys.executable, '-I', '/workspace/worker.py', '/workspace/' + source_file,
             by_id[test_id], str(write_fd)], stdin=subprocess.DEVNULL, stdout=output,
            stderr=output, pass_fds=(write_fd,), start_new_session=True, cwd='/tmp')
        os.close(write_fd)
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            result.update(completed=False, detail='test timeout')
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        os.set_blocking(read_fd, False)
        try:
            verdict = os.read(read_fd, 5)
        except BlockingIOError:
            verdict = b''
        os.close(read_fd)
        if not result['completed']:
            break
        if process.returncode != 0 or verdict not in (b'PASS', b'FAIL'):
            result.update(completed=False, detail='test worker did not return a valid outcome')
            break
        result['passed' if verdict == b'PASS' else 'failed'].append(test_id)
print(json.dumps(result))
"""


class SandboxError(RuntimeError):
    """An explicit sandbox infrastructure or integrity failure."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""


class CommandRunner(Protocol):
    def __call__(
        self,
        command: Sequence[str],
        *,
        input: bytes | None = None,
        timeout: float = 15.0,
        output_limit: int = MAX_CONTROL_OUTPUT,
    ) -> CommandResult: ...


def run_command(
    command: Sequence[str],
    *,
    input: bytes | None = None,
    timeout: float = 15.0,
    output_limit: int = MAX_CONTROL_OUTPUT,
) -> CommandResult:
    """Run a trusted CLI with bounded pipes, time, and guaranteed child reaping."""
    output = {"stdout": bytearray(), "stderr": bytearray()}
    pending = memoryview(input or b"")
    with subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ) as process:
        assert (
            process.stdin is not None and process.stdout is not None and process.stderr is not None
        )
        with selectors.DefaultSelector() as selector:
            for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            if pending:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            else:
                process.stdin.close()
            deadline = time.monotonic() + timeout
            try:
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise SandboxError("command timeout")
                    for key, _ in selector.select(remaining):
                        if key.data == "stdin":
                            try:
                                count = os.write(key.fd, pending[:4096])
                                pending = pending[count:]
                            except BrokenPipeError:
                                pending = memoryview(b"")
                            if not pending:
                                selector.unregister(key.fd)
                                process.stdin.close()
                        else:
                            chunk = os.read(key.fd, 4096)
                            if not chunk:
                                selector.unregister(key.fd)
                                continue
                            output[key.data].extend(chunk)
                            if sum(map(len, output.values())) > output_limit:
                                raise SandboxError("command output limit exceeded")
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except BaseException as error:
                process.kill()
                process.wait()
                if isinstance(error, (SandboxError, subprocess.TimeoutExpired)):
                    raise SandboxError("command timeout or output limit exceeded") from None
                raise
        return CommandResult(process.returncode, bytes(output["stdout"]), bytes(output["stderr"]))


class _TestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    completed: StrictBool
    collected: list[StrictStr]
    passed: list[StrictStr]
    failed: list[StrictStr]
    detail: StrictStr


class DockerSandbox:
    """Synchronous five-tool interface; use as a context manager per history."""

    def __init__(
        self,
        task: TaskSpec,
        environment: Environment,
        *,
        image: str = DEFAULT_IMAGE,
        runner: CommandRunner | None = None,
        test_timeout: float = 10.0,
    ) -> None:
        if not math.isfinite(test_timeout) or not 0 < test_timeout <= 10:
            raise ValueError("test_timeout must be positive and at most 10 seconds")
        if not image or image.startswith("-"):
            raise ValueError("image must be a nonempty Docker image reference")
        if len(task.tests) > MAX_TESTS or any(
            len(value.encode("utf-8")) > MAX_FILE_BYTES
            for value in (task.initial_source, *(test.expression for test in task.tests))
        ):
            raise ValueError("task exceeds sandbox input limits")
        if task.source_file in {"runner.py", "worker.py"}:
            raise ValueError("source filename conflicts with the protected harness")
        if environment == Environment.PARTIAL_TESTS and len(task.tests) < 2:
            raise ValueError("partial-tests environment requires at least two supplied tests")
        self.task = task
        self.environment = Environment(environment)
        self.image = image
        self.test_timeout = test_timeout
        self.events: list[ToolEvent] = []
        self.finished = False
        self.container_id = ""
        self._runner = runner or run_command
        self._source = task.initial_source
        self._files = {task.source_file: task.initial_source}
        self._note_failed = False
        self._closed = False

    def __enter__(self) -> Self:
        if self.container_id or self._closed:
            raise SandboxError("sandbox instances cannot be reused")
        self.container_id = f"context-fidelity-{uuid.uuid4().hex}"
        try:
            self._command(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--name",
                    self.container_id,
                    "--network",
                    "none",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--user",
                    "65534:65534",
                    "--memory",
                    "128m",
                    "--memory-swap",
                    "128m",
                    "--cpus",
                    "1",
                    "--pids-limit",
                    "32",
                    "--ipc",
                    "none",
                    "--tmpfs",
                    "/workspace:rw,noexec,nosuid,nodev,size=1048576,mode=755",
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,nodev,size=16777216,mode=1777",
                    "--label",
                    "context-fidelity.sandbox=1",
                    self.image,
                    "python",
                    "-I",
                    "-c",
                    "import time; time.sleep(86400)",
                ],
                timeout=30,
            )
            self._control(
                "setup",
                {
                    "source_file": self.task.source_file,
                    "source": self._source,
                    "tests": json.dumps([test.model_dump() for test in self.task.tests]),
                    "runner": _HARNESS,
                    "worker": _WORKER,
                },
            )
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Remove this history's container, including any surviving subprocesses."""
        if self.container_id and not self._closed:
            self._command(["docker", "rm", "--force", self.container_id], timeout=15)
            self._closed = True

    def _command(
        self, command: Sequence[str], *, input: bytes | None = None, timeout: float = 15
    ) -> bytes:
        try:
            result = self._runner(command, input=input, timeout=timeout)
        except OSError as error:
            raise SandboxError(f"Docker command unavailable: {error}") from error
        if result.returncode:
            detail = result.stderr.decode("utf-8", errors="replace")[:512]
            raise SandboxError(f"Docker command failed ({result.returncode}): {detail}")
        return result.stdout

    def _control(self, operation: str, payload: dict[str, object]) -> bytes:
        return self._command(
            [
                "docker",
                "exec",
                "--user",
                "0:0",
                "-i",
                self.container_id,
                "python",
                "-I",
                "-c",
                _CONTROL,
                operation,
            ],
            input=json.dumps(payload).encode(),
        )

    def _require_open(self) -> None:
        if not self.container_id or self._closed:
            raise SandboxError("sandbox is not open")

    def snapshot(self) -> tuple[FileSnapshot, ...]:
        """Independently read exact final files; reject unrecorded mutations."""
        self._require_open()
        raw = self._control("snapshot", {"paths": [self.task.source_file, NOTE_PATH]})
        try:
            files = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as error:
            raise SandboxError("invalid sandbox snapshot") from error
        if files != self._files:
            raise SandboxError("sandbox artifacts differ from recorded writes")
        return tuple(FileSnapshot(path=path, content=content) for path, content in files.items())

    def _event(
        self,
        tool_name: ToolName,
        success: bool,
        message: str,
        *,
        path: str | None = None,
        write_content: str | None = None,
        source_after: str | None = None,
        test_ids: tuple[str, ...] = (),
        passed_test_ids: tuple[str, ...] = (),
        failed_test_ids: tuple[str, ...] = (),
        full_suite: bool = False,
    ) -> ToolEvent:
        event = ToolEvent(
            event_id=f"event-{len(self.events) + 1}",
            tool_name=tool_name,
            path=path,
            success=success,
            message=message,
            source_version=source_version(self._source),
            write_content=write_content,
            source_after=source_after,
            test_ids=test_ids,
            passed_test_ids=passed_test_ids,
            failed_test_ids=failed_test_ids,
            expected_test_ids=tuple(test.test_id for test in self.task.tests)
            if tool_name == "run_tests"
            else (),
            full_suite=full_suite,
        )
        self.events.append(event)
        return event

    def _ready(self) -> None:
        self._require_open()
        if self.finished:
            raise SandboxError("work has already finished")

    def list_files(self) -> ToolEvent:
        try:
            self._ready()
            files = self.snapshot()
            message = "Files: " + ", ".join(file.path for file in files)
        except SandboxError as error:
            return self._event("list_files", False, str(error))
        return self._event("list_files", True, message)

    def read_file(self, path: str) -> ToolEvent:
        try:
            self._ready()
            self._validate_path(path)
            files = {file.path: file.content for file in self.snapshot()}
            if path not in files:
                raise SandboxError("file does not exist")
        except SandboxError as error:
            return self._event("read_file", False, str(error), path=path)
        return self._event("read_file", True, files[path], path=path)

    def _validate_path(self, path: str) -> None:
        if path not in {self.task.source_file, NOTE_PATH}:
            raise SandboxError("path must be the task source basename or fix-note.md")

    def write_file(self, path: str, content: str) -> ToolEvent:
        try:
            self._ready()
            self._validate_path(path)
            if len(content.encode("utf-8")) > MAX_FILE_BYTES:
                raise SandboxError(f"file exceeds {MAX_FILE_BYTES} byte limit")
            self.snapshot()
            if (
                self.environment == Environment.RECOVERABLE_NOTE
                and path == NOTE_PATH
                and not self._note_failed
            ):
                self._note_failed = True
                raise SandboxError("first note write failed; a subsequent note write may succeed")
            self._control("write", {"path": path, "content": content})
            self._files[path] = content
            if path == self.task.source_file:
                self._source = content
        except SandboxError as error:
            return self._event("write_file", False, str(error), path=path)
        # A transport success followed by an inconsistent read is an integrity
        # failure, not a failed write: do not manufacture a contradictory event.
        self.snapshot()
        return self._event(
            "write_file",
            True,
            f"Saved {len(content.encode('utf-8'))} bytes; nonempty={bool(content.strip())}.",
            path=path,
            write_content=content,
            source_after=content if path == self.task.source_file else None,
        )

    def run_tests(self) -> ToolEvent:
        collected: tuple[str, ...] = ()
        passed: tuple[str, ...] = ()
        failed: tuple[str, ...] = ()
        try:
            self._ready()
            self.snapshot()
            if self.environment == Environment.BLOCKED_TESTS:
                raise SandboxError("test runner blocked before collecting any tests")
            selected = tuple(test.test_id for test in self.task.tests)
            partial = self.environment == Environment.PARTIAL_TESTS
            if partial:
                selected = selected[: max(1, len(selected) // 2)]
            raw = self._command(
                [
                    "docker",
                    "exec",
                    self.container_id,
                    "python",
                    "-I",
                    "/workspace/runner.py",
                    self.task.source_file,
                    json.dumps(selected),
                    str(self.test_timeout),
                ],
                timeout=self.test_timeout + 5,
            )
            result = _TestResult.model_validate_json(raw)
            collected, passed, failed = map(tuple, (result.collected, result.passed, result.failed))
            if (
                collected != selected[: len(collected)]
                or len(set(passed)) != len(passed)
                or len(set(failed)) != len(failed)
                or set(passed) & set(failed)
                or not set(passed + failed) <= set(collected)
                or (
                    result.completed
                    and (collected != selected or set(passed + failed) != set(selected))
                )
            ):
                raise ValueError("inconsistent test scope or outcomes")
            self.snapshot()
            if not result.completed:
                return self._event(
                    "run_tests",
                    False,
                    result.detail,
                    test_ids=collected,
                    passed_test_ids=passed,
                    failed_test_ids=failed,
                )
            full = not partial and len(collected) == len(self.task.tests)
            message = (
                "Subset only; " if partial else "Full supplied suite; "
            ) + f"{len(passed)} passed, {len(failed)} failed."
            return self._event(
                "run_tests",
                True,
                message,
                test_ids=collected,
                passed_test_ids=passed,
                failed_test_ids=failed,
                full_suite=full,
            )
        except (SandboxError, ValueError) as error:
            return self._event("run_tests", False, str(error)[:512])

    def finish_work(self) -> ToolEvent:
        try:
            self._ready()
            self.snapshot()
        except SandboxError as error:
            return self._event("finish_work", False, str(error))
        self.finished = True
        return self._event(
            "finish_work", True, "Work stopped; final reporting will be requested separately."
        )
