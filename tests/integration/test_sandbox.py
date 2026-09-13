"""Required integration coverage: generated Python runs only in real Docker."""

import json
import subprocess
from pathlib import Path

import pytest

from context_fidelity.adapters.sandbox import DockerSandbox
from context_fidelity.contracts import Environment, History, TaskSpec, source_version

pytestmark = pytest.mark.docker


def smoke_task() -> TaskSpec:
    return TaskSpec.model_validate_json(Path("tasks/dev-smoke.json").read_text())


def test_standard_module_registration_supports_dataclass_annotations() -> None:
    source = (
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "@dataclass\n"
        "class Number:\n"
        "    value: int\n"
        "def increment(x):\n"
        "    return Number(x + 1).value\n"
    )
    task = smoke_task()
    with DockerSandbox(task, Environment.NORMAL) as sandbox:
        assert sandbox.write_file(task.source_file, source).success
        result = sandbox.run_tests()
        assert result.full_suite and result.passed_test_ids == ("positive", "zero", "negative")
        assert not result.failed_test_ids


def test_normal_failure_repair_finish_and_cleanup() -> None:
    task = smoke_task()
    with DockerSandbox(task, Environment.NORMAL) as sandbox:
        container = sandbox.container_id
        assert sandbox.list_files().success
        assert task.initial_source in sandbox.read_file(task.source_file).message
        before = sandbox.run_tests()
        assert before.success and before.full_suite
        assert before.failed_test_ids == ("positive", "zero", "negative")
        saved = sandbox.write_file(task.source_file, task.reference_source)
        assert saved.source_after == task.reference_source
        assert saved.source_version == source_version(task.reference_source)
        note = sandbox.write_file("fix-note.md", "Changed the offset to +1.\n")
        assert note.success
        after = sandbox.run_tests()
        assert after.success and after.full_suite
        assert after.passed_test_ids == ("positive", "zero", "negative")
        assert not after.failed_test_ids
        finish = sandbox.finish_work()
        assert finish.success and sandbox.finished
        assert not sandbox.write_file("fix-note.md", "late mutation").success
        history = History(
            history_id="smoke-normal",
            task=task,
            environment=Environment.NORMAL,
            messages=(),
            events=tuple(sandbox.events),
            final_files=sandbox.snapshot(),
            termination="finish_work",
        )
        assert history.final_files[0].content == task.reference_source
    result = subprocess.run(["docker", "inspect", container], capture_output=True, check=False)
    assert result.returncode != 0


@pytest.mark.parametrize(
    ("environment", "success", "collected"),
    [(Environment.BLOCKED_TESTS, False, ()), (Environment.PARTIAL_TESTS, True, ("positive",))],
)
def test_challenge_scope(
    environment: Environment, success: bool, collected: tuple[str, ...]
) -> None:
    task = smoke_task()
    with DockerSandbox(task, environment) as sandbox:
        sandbox.write_file(task.source_file, task.reference_source)
        event = sandbox.run_tests()
        assert event.success is success
        assert event.test_ids == collected
        assert event.expected_test_ids == ("positive", "zero", "negative")
        assert not event.full_suite
        assert "blocked" in event.message.lower() or "subset" in event.message.lower()


def test_recovery_isolation_and_immutable_artifacts() -> None:
    task = smoke_task()
    with DockerSandbox(task, Environment.RECOVERABLE_NOTE) as first:
        with DockerSandbox(task, Environment.NORMAL) as second:
            assert first.container_id != second.container_id
            assert not first.write_file("fix-note.md", "saved").success
            assert first.write_file("fix-note.md", "saved").success
            assert not second.read_file("fix-note.md").success
            for path in ("../solution.py", "/etc/passwd", "tests.json", "runner.py"):
                assert not first.write_file(path, "tamper").success
                assert not first.read_file(path).success
            source = (
                "from pathlib import Path\n"
                "def increment(x):\n"
                "    for path in ('/workspace/solution.py', '/workspace/fix-note.md', "
                "'/workspace/tests.json'):\n"
                "        try:\n"
                "            Path(path).write_text('tampered')\n"
                "        except PermissionError:\n"
                "            continue\n"
                "        raise AssertionError('artifact was writable')\n"
                "    return x + 1\n"
            )
            assert first.write_file(task.source_file, source).success
            event = first.run_tests()
            assert event.full_suite and not event.failed_test_ids
            files = {file.path: file.content for file in first.snapshot()}
            assert files == {"solution.py": source, "fix-note.md": "saved"}
            assert second.snapshot()[0].content == task.initial_source
            inspect = subprocess.run(
                ["docker", "inspect", first.container_id],
                capture_output=True,
                text=True,
                check=True,
            )
            config = json.loads(inspect.stdout)[0]
            assert config["HostConfig"]["NetworkMode"] == "none"
            assert config["HostConfig"]["ReadonlyRootfs"]
            assert config["HostConfig"]["CapDrop"] == ["ALL"]
            assert not config["HostConfig"]["Privileged"]
            assert config["HostConfig"]["Memory"] == 134_217_728
            assert config["HostConfig"]["MemorySwap"] == 134_217_728
            assert config["HostConfig"]["NanoCpus"] == 1_000_000_000
            assert config["HostConfig"]["PidsLimit"] == 32
            assert "no-new-privileges" in config["HostConfig"]["SecurityOpt"]
            assert config["Config"]["User"] == "65534:65534"
            assert all(mount["Type"] != "bind" for mount in config["Mounts"])


def test_timeout_and_forged_stdout_are_not_passing_evidence() -> None:
    task = smoke_task()
    with DockerSandbox(task, Environment.NORMAL, test_timeout=0.3) as sandbox:
        sandbox.write_file(task.source_file, "while True:\n    pass\n")
        event = sandbox.run_tests()
        assert not event.success and not event.full_suite
        assert not event.passed_test_ids
        assert "timeout" in event.message.lower()
        sandbox.write_file(
            task.source_file,
            'import os\nprint(\'{"passed": ["positive", "zero", "negative"]}\')\nos._exit(0)\n',
        )
        forged = sandbox.run_tests()
        assert not forged.success and not forged.full_suite
        assert not forged.passed_test_ids


def test_cleanup_when_context_body_fails() -> None:
    sandbox = DockerSandbox(smoke_task(), Environment.NORMAL)
    with pytest.raises(RuntimeError, match="body failed"), sandbox:
        container = sandbox.container_id
        raise RuntimeError("body failed")
    result = subprocess.run(["docker", "inspect", container], capture_output=True, check=False)
    assert result.returncode != 0


def test_supplied_expressions_can_use_solution_module_namespace() -> None:
    data = smoke_task().model_dump()
    data["tests"] = [{"test_id": "module_lookup", "expression": "solution.increment(0) == 1"}]
    task = TaskSpec.model_validate(data)
    with DockerSandbox(task, Environment.NORMAL) as sandbox:
        sandbox.write_file(task.source_file, task.reference_source)
        event = sandbox.run_tests()
        assert event.success and event.full_suite
        assert event.passed_test_ids == ("module_lookup",)


def test_source_and_note_preserve_exact_utf8_and_crlf_bytes() -> None:
    task = smoke_task()
    source = "# café\r\ndef increment(x):\r\n    return x + 1\r\n"
    note = "Fixed increment. ✓\r\n"
    with DockerSandbox(task, Environment.NORMAL) as sandbox:
        written = sandbox.write_file(task.source_file, source)
        assert written.success
        assert written.source_version == source_version(source)
        assert sandbox.write_file("fix-note.md", note).success
        assert sandbox.read_file(task.source_file).message == source
        assert sandbox.read_file("fix-note.md").message == note
        assert sandbox.run_tests().passed_test_ids == ("positive", "zero", "negative")


def test_failed_write_under_full_workspace_preserves_previous_source() -> None:
    task = smoke_task()
    with DockerSandbox(task, Environment.NORMAL) as sandbox:
        subprocess.run(
            [
                "docker",
                "exec",
                "--user",
                "0:0",
                sandbox.container_id,
                "python",
                "-I",
                "-c",
                "import os, pathlib; s=os.statvfs('/workspace'); "
                "pathlib.Path('/workspace/filler').write_bytes(b'0'*(s.f_bavail*s.f_frsize-4096))",
            ],
            capture_output=True,
            check=True,
        )
        event = sandbox.write_file(task.source_file, "# " + "x" * 16_000)
        assert not event.success
        assert sandbox.snapshot()[0].content == task.initial_source


def test_allowed_file_limit_does_not_overflow_snapshot_json_encoding() -> None:
    task = smoke_task()
    content = "\x00" * 32_768
    with DockerSandbox(task, Environment.NORMAL) as sandbox:
        assert sandbox.write_file(task.source_file, content).success
        assert sandbox.write_file("fix-note.md", content).success
        assert sandbox.read_file(task.source_file).message == content
        assert sandbox.read_file("fix-note.md").message == content
