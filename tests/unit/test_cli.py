"""CLI behavior uses local runtime paths and dispatches actual prepared artifacts."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_pipeline import repo, services

from context_fidelity import __main__ as cli


def args(root: Path, *command: str) -> list[str]:
    return ["--repo-root", str(root), "--runtime-dir", str(root / "runtime"), *command]


def test_cli_prepare_never_connects_and_uses_explicit_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repo(tmp_path)

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("prepare connected to model/tokenizer")

    monkeypatch.setattr(cli, "local_model", unexpected)
    monkeypatch.setattr(cli.OfficialTokenizer, "load", unexpected)
    status = cli.main(
        args(root, "prepare", "--run-dir", "runs/pilot", "--run-id", "pilot", "--split", "dev")
    )
    assert status == 0
    assert json.loads((root / "runs/pilot/plan.json").read_text())["repetitions"] == 1
    assert "8 histories" in capsys.readouterr().out
    assert os.environ["XDG_DATA_HOME"] == str(root / "runtime/data")
    assert os.environ["XDG_CACHE_HOME"] == str(root / "runtime/cache")
    assert os.environ["TOKENIZERS_PARALLELISM"] == "false"


def test_cli_collect_and_report_load_cached_tokenizer_and_preserve_invalid_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repo(tmp_path)
    svc = services(monkeypatch)
    monkeypatch.setattr(cli, "local_model", lambda endpoint, **kwargs: svc.model)

    def load(**kwargs: object):
        assert kwargs["local_files_only"] is True
        assert kwargs["cache_dir"] == root / "runtime/hf"
        return svc.tokenizer

    monkeypatch.setattr(cli.OfficialTokenizer, "load", load)
    assert (
        cli.main(
            args(root, "prepare", "--run-dir", "runs/pilot", "--run-id", "pilot", "--split", "dev")
        )
        == 0
    )
    assert cli.main(args(root, "collect", "--run-dir", "runs/pilot")) == 0
    assert cli.main(args(root, "report", "--run-dir", "runs/pilot")) == 0
    assert "MALFORMED BUT PRESERVED" not in capsys.readouterr().out
    assert len(list((root / "runs/pilot/reports").glob("*.json"))) == 32
    assert cli.main(args(root, "report", "--run-dir", "runs/pilot")) == 1
    assert "already started" in capsys.readouterr().err


def test_cli_reports_phase_failure_log_path_and_nonzero_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repo(tmp_path)
    svc = services(monkeypatch, fail_stage="actor")
    monkeypatch.setattr(cli, "local_model", lambda endpoint, **kwargs: svc.model)
    monkeypatch.setattr(cli.OfficialTokenizer, "load", lambda **kwargs: svc.tokenizer)
    assert (
        cli.main(
            args(root, "prepare", "--run-dir", "runs/pilot", "--run-id", "pilot", "--split", "dev")
        )
        == 0
    )
    assert cli.main(args(root, "collect", "--run-dir", "runs/pilot")) == 1
    assert "actor-failed.eval" in capsys.readouterr().err


@pytest.mark.parametrize(
    "command",
    [
        [],
        ["unknown"],
        ["prepare"],
        ["prepare", "--run-dir", "runs/dev", "--run-id", "dev", "--split", "wrong"],
    ],
)
def test_cli_invalid_arguments_exit_nonzero(command: list[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(command)
    assert caught.value.code == 2


def test_module_help_is_runnable() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "context_fidelity", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "validate-tasks" in result.stdout


@pytest.mark.parametrize("invalid", [False, True])
def test_cli_validation_runs_sandbox_and_returns_fixture_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    invalid: bool,
) -> None:
    from contextlib import contextmanager

    from test_pipeline import fixture_validation

    from context_fidelity.adapters.sandbox import SandboxError

    root = repo(tmp_path)

    @contextmanager
    def sandbox(task, environment, **kwargs):
        assert kwargs["test_timeout"] == 10
        if invalid and task.task_id == "task-0":
            raise SandboxError("Docker not reachable")
        record = fixture_validation((task,)).records[0]

        class Sandbox:
            reference = False

            def run_tests(self):
                return record.reference if self.reference else record.initial

            def write_file(self, path, content):
                self.reference = True

        yield Sandbox()

    monkeypatch.setattr(cli, "DockerSandbox", sandbox)
    status = cli.main(
        args(root, "validate-tasks", "--tasks", "tasks/dev", "--output", "validation.json")
    )
    assert status == int(invalid)
    result = json.loads((root / "validation.json").read_text())
    assert len(result["records"]) == 4
    if invalid:
        assert "Docker not reachable" in capsys.readouterr().err


def test_cli_prepare_accepts_existing_validation_and_default_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_pipeline import fixture_task, fixture_validation

    root = repo(tmp_path / "repository")
    validation = root / "validation.json"
    validation.write_text(
        fixture_validation(tuple(fixture_task(i) for i in range(4))).model_dump_json()
    )
    assert (
        cli.main(
            [
                "--repo-root",
                str(root),
                "prepare",
                "--run-dir",
                "runs/pilot",
                "--run-id",
                "pilot",
                "--split",
                "dev",
                "--validation",
                str(validation),
            ]
        )
        == 0
    )
    assert os.environ["XDG_DATA_HOME"] == str(tmp_path / ".local-runtime/data")
