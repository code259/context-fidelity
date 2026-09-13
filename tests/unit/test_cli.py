"""CLI behavior uses local runtime paths and dispatches actual prepared artifacts."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_pipeline import repo, services

from context_fidelity import __main__ as cli
from context_fidelity.doctor import RuntimeCheck, RuntimeReadiness


def args(root: Path, *command: str) -> list[str]:
    return ["--repo-root", str(root), "--runtime-dir", str(root / "runtime"), *command]


def test_cli_prepare_never_connects_and_uses_explicit_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repo(tmp_path)

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("prepare connected to model/tokenizer")

    monkeypatch.setattr(cli, "local_model", unexpected)
    monkeypatch.setattr(cli, "check_runtime", unexpected)
    status = cli.main(
        args(root, "prepare", "--run-dir", "runs/pilot", "--run-id", "pilot", "--split", "dev")
    )
    assert status == 0
    assert json.loads((root / "runs/pilot/plan.json").read_text())["repetitions"] == 1
    assert "8 histories" in capsys.readouterr().out
    assert os.environ["XDG_DATA_HOME"] == str(root / "runtime/data")
    assert os.environ["XDG_CACHE_HOME"] == str(root / "runtime/cache")
    assert os.environ["TOKENIZERS_PARALLELISM"] == "false"


def test_cli_collect_and_report_reuse_preflight_tokenizer_and_preserve_invalid_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repo(tmp_path)
    svc = services(monkeypatch)
    monkeypatch.setattr(cli, "local_model", lambda endpoint, **kwargs: svc.model)

    requirements = []

    def ready(endpoint, cache_dir, **kwargs):
        assert cache_dir == root / "runtime/hf"
        requirements.append(kwargs["require_docker"])
        return RuntimeReadiness(
            (RuntimeCheck("tokenizer", True, "cached and templated"),), svc.tokenizer
        )

    monkeypatch.setattr(cli, "check_runtime", ready)
    assert (
        cli.main(
            args(root, "prepare", "--run-dir", "runs/pilot", "--run-id", "pilot", "--split", "dev")
        )
        == 0
    )
    assert cli.main(args(root, "collect", "--run-dir", "runs/pilot")) == 0
    assert cli.main(args(root, "report", "--run-dir", "runs/pilot")) == 0
    assert requirements == [True, False]
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
    monkeypatch.setattr(
        cli,
        "check_runtime",
        lambda *args, **kwargs: RuntimeReadiness(
            (RuntimeCheck("tokenizer", True, "cached and templated"),), svc.tokenizer
        ),
    )
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


@pytest.mark.parametrize("phase", ["collect", "report"])
def test_cli_failed_preflight_leaves_phase_unstarted_without_model_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], phase: str
) -> None:
    import asyncio

    from context_fidelity import pipeline

    root = repo(tmp_path)
    svc = services(monkeypatch)
    assert (
        cli.main(
            args(root, "prepare", "--run-dir", "runs/pilot", "--run-id", "pilot", "--split", "dev")
        )
        == 0
    )
    if phase == "report":
        asyncio.run(pipeline.collect_study(root, root / "runs/pilot", svc))

    def unavailable(endpoint, cache_dir, **kwargs):
        assert kwargs["require_docker"] is (phase == "collect")
        return RuntimeReadiness(
            (RuntimeCheck("model_health", False, "Restore the local endpoint tunnel"),),
            svc.tokenizer,
        )

    monkeypatch.setattr(cli, "check_runtime", unavailable, raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail("phase created its model before preflight succeeded")

    monkeypatch.setattr(cli, "local_model", forbidden)
    assert cli.main(args(root, phase, "--run-dir", "runs/pilot")) == 1
    assert "Runtime not ready" in capsys.readouterr().err
    assert not (root / "runs/pilot" / f"{phase}-started.json").exists()


@pytest.mark.parametrize("ready", [True, False])
def test_cli_doctor_prints_availability_and_never_creates_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], ready: bool
) -> None:
    from test_pipeline import WordTokenizer

    root = repo(tmp_path)

    def runtime(endpoint, cache_dir, **kwargs):
        assert cache_dir == root / "runtime/hf"
        assert kwargs["require_docker"] is True
        return RuntimeReadiness(
            (RuntimeCheck("docker", ready, "28.3.2" if ready else "Start Docker"),), WordTokenizer()
        )

    monkeypatch.setattr(cli, "check_runtime", runtime, raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail("doctor created a model or generated output")

    monkeypatch.setattr(cli, "local_model", forbidden)
    assert cli.main(args(root, "doctor")) == int(not ready)
    output = capsys.readouterr()
    assert "docker" in output.out + output.err
    assert ("28.3.2" if ready else "Start Docker") in output.out + output.err
    assert not (root / "runs").exists()


def test_cli_report_runs_with_real_preflight_when_docker_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    import httpx
    from test_doctor import healthy

    from context_fidelity import doctor, pipeline

    root = repo(tmp_path)
    svc = services(monkeypatch)
    assert (
        cli.main(
            args(root, "prepare", "--run-dir", "runs/pilot", "--run-id", "pilot", "--split", "dev")
        )
        == 0
    )
    asyncio.run(pipeline.collect_study(root, root / "runs/pilot", svc))

    def absent_docker(*args, **kwargs):
        pytest.fail("report preflight attempted Docker")

    def preflight(endpoint, cache_dir, **kwargs):
        return doctor.check_runtime(
            endpoint,
            cache_dir,
            **kwargs,
            transport=httpx.MockTransport(healthy),
            runner=absent_docker,
            tokenizer_loader=lambda **kw: svc.tokenizer,
        )

    monkeypatch.setattr(cli, "check_runtime", preflight)
    monkeypatch.setattr(cli, "local_model", lambda *args, **kwargs: svc.model)
    assert cli.main(args(root, "report", "--run-dir", "runs/pilot")) == 0
    assert len(list((root / "runs/pilot/reports").glob("*.json"))) == 32


@pytest.mark.parametrize("with_reviews", [False, True])
def test_cli_analysis_is_offline_and_uses_generation_freeze(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    with_reviews: bool,
) -> None:
    from test_results import audits, fixture, load

    root, run = fixture(tmp_path)
    extra = []
    if with_reviews:
        (root / "audit.json").write_text(audits(load(root, run)).model_dump_json())
        (root / "reviews.json").write_text("[]")
        extra = ["--summary-audit", "audit.json", "--prose-reviews", "reviews.json"]
    (root / "config.yaml").write_text("current source can change after generation")

    def forbidden(*args, **kwargs):
        pytest.fail("offline analysis attempted live runtime or current generation setup")

    for name in ("check_runtime", "local_model", "load_study"):
        monkeypatch.setattr(cli, name, forbidden)
    calls = []

    async def publish(results, run_dir, output):
        calls.append((results, run_dir, output))

    monkeypatch.setattr(cli, "publish_analysis", publish, raising=False)
    assert (
        cli.main(
            args(
                root,
                "analyze",
                "--run-dir",
                str(run),
                "--analysis-id",
                "view",
                "--output",
                "results/view",
                *extra,
            )
        )
        == 0
    )
    assert calls[0][0].analysis_id == "view"
    assert len(calls[0][0].cells) == 32
    assert calls[0][1] == run
    assert calls[0][2] == root / "results/view"
    assert (calls[0][0].summary_audit is not None) == with_reviews
    assert "pending" in capsys.readouterr().out


@pytest.mark.parametrize("payload", ["{}", "[{}]"])
def test_cli_analysis_rejects_malformed_review_records(tmp_path: Path, payload: str) -> None:
    root = tmp_path
    (root / "reviews.json").write_text(payload)
    assert (
        cli.main(
            args(
                root,
                "analyze",
                "--run-dir",
                "runs/missing",
                "--analysis-id",
                "view",
                "--output",
                "results/view",
                "--prose-reviews",
                "reviews.json",
            )
        )
        == 1
    )
    assert not (root / "results/view").exists()
