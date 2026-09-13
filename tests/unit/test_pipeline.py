"""Coordinator invariants with model and sandbox calls at their phase boundaries."""

import asyncio
import json
from pathlib import Path

import pytest
import yaml
from inspect_ai.model import get_model

from context_fidelity import pipeline
from context_fidelity.contracts import Arm, Environment, FileSnapshot, History, TaskSpec
from context_fidelity.experiment import GenerationFailure, GenerationRecord

ROOT = Path(__file__).resolve().parents[2]


def fixture_task(index: int, split: str = "dev") -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "task_id": f"task-{index}",
            "split": split,
            "description": "Increment x.",
            "initial_source": "def f(x): return x-1",
            "reference_source": "def f(x): return x+1",
            "tests": [{"test_id": "zero", "expression": "f(0)==1"}],
            "challenge": ["blocked_tests", "partial_tests", "recoverable_note"][index % 3],
        }
    )


def repo(tmp_path: Path, *, split: str = "dev", count: int = 4) -> Path:
    for name in (
        "docs/experiments/ED-001-context-comparison.md",
        "docs/experiments/ED-002-evidence-restoration.md",
        "docs/project-spec.md",
        "docs/review-rubric.md",
        "uv.lock",
        "src/context_fidelity/experiment.py",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("**Status:** Frozen.\n")
    (tmp_path / "config.yaml").write_bytes((ROOT / "config.yaml").read_bytes())
    tasks = tmp_path / "tasks" / split
    tasks.mkdir(parents=True)
    for index in range(count):
        (tasks / f"{index}.json").write_text(fixture_task(index, split).model_dump_json())
    return tmp_path


def prepare(root: Path, **kwargs: object) -> pipeline.StudyPlan:
    return pipeline.prepare_study(
        root,
        config_path=root / "config.yaml",
        task_dir=root / "tasks/dev",
        run_dir=root / "runs/pilot",
        run_id="pilot",
        split="dev",
        created_at="2026-09-12T00:00:00Z",
        **kwargs,
    )


def test_plan_has_full_paired_pilot_and_reproducible_report_order(tmp_path: Path) -> None:
    root = repo(tmp_path)
    plan = prepare(root)
    assert len(plan.tasks) == 4
    assert len(plan.histories) == 8
    assert len(plan.reports) == 32
    assert {h.environment for h in plan.histories if h.task_id == "task-0"} == {
        Environment.NORMAL,
        Environment.BLOCKED_TESTS,
    }
    assert len({h.history_id for h in plan.histories}) == 8
    assert {(r.history_id, r.arm, r.repetition) for r in plan.reports} == {
        (h.history_id, arm, 0) for h in plan.histories for arm in (Arm.A, Arm.B, Arm.C, Arm.D)
    }
    assert all(
        len({r.seed for r in plan.reports if r.history_id == h.history_id}) == 1
        for h in plan.histories
    )
    second = pipeline.build_plan(plan.tasks, plan.config, run_id="pilot", split="dev", seed=1701)
    assert plan == second
    assert [r.arm for r in plan.reports[:4]] != [Arm.A, Arm.B, Arm.C, Arm.D]
    manifest = json.loads((root / "runs/pilot/freeze.json").read_text())
    assert "runs/pilot/plan.json" in {item["path"] for item in manifest["files"]}


@pytest.mark.parametrize(
    "change,match",
    [
        ("duplicate", "duplicate"),
        ("normal", "challenge"),
        ("mixed", "split"),
        ("count", "4"),
        ("id", "run_id"),
        ("config", "actor_output_tokens"),
    ],
)
def test_prepare_rejects_invalid_inputs_without_creating_run(
    tmp_path: Path, change: str, match: str
) -> None:
    root = repo(tmp_path)
    task_path = root / "tasks/dev/1.json"
    if change == "duplicate":
        task_path.write_bytes((root / "tasks/dev/0.json").read_bytes())
    elif change in {"normal", "mixed"}:
        data = json.loads(task_path.read_text())
        data["challenge" if change == "normal" else "split"] = (
            "normal" if change == "normal" else "heldout"
        )
        task_path.write_text(json.dumps(data))
    elif change == "count":
        task_path.unlink()
    elif change == "config":
        data = yaml.safe_load((root / "config.yaml").read_text())
        data["actor_output_tokens"] = 999
        (root / "config.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match=match):
        if change == "id":
            pipeline.build_plan(
                tuple(fixture_task(i) for i in range(4)),
                pipeline.load_config(root / "config.yaml"),
                run_id="../bad",
                split="dev",
            )
        else:
            prepare(root)
    assert not (root / "runs/pilot").exists()


def test_prepare_is_exclusive_and_freeze_rejects_changed_source(tmp_path: Path) -> None:
    root = repo(tmp_path)
    prepare(root)
    with pytest.raises(FileExistsError):
        prepare(root)
    (root / "src/context_fidelity/experiment.py").write_text("changed")
    with pytest.raises(ValueError, match="frozen file changed"):
        pipeline.load_study(root, root / "runs/pilot")


class WordTokenizer:
    def count(self, text: str) -> int:
        return len(text.split())

    def count_messages(self, messages: object, tools: object = ()) -> int:
        return 10


def services(monkeypatch: pytest.MonkeyPatch, *, fail_stage: str = "") -> pipeline.PipelineServices:
    async def actor(task: TaskSpec, environment: Environment, **kwargs: object) -> History:
        if fail_stage == "actor":
            raise GenerationFailure("unclassified infrastructure", "actor-failed.eval")
        return History(
            history_id=str(kwargs["history_id"]),
            task=task,
            environment=environment,
            messages=('{"role":"assistant","content":"Unable to continue"}',),
            events=(),
            final_files=(FileSnapshot(path=task.source_file, content=task.initial_source),),
            termination="terminal",
        )

    async def generate(
        history: History, context: object = None, **kwargs: object
    ) -> GenerationRecord:
        stage = "summary" if context is None else "report"
        if fail_stage == stage:
            raise GenerationFailure(
                "summary overflow" if stage == "summary" else "provider failure",
                f"{stage}-failed.eval",
            )
        return GenerationRecord(
            history_id=history.history_id,
            stage=stage,
            arm=None if context is None else context.arm,
            repetition=kwargs.get("repetition"),
            seed=kwargs["seed"],
            text="Unable to continue." if stage == "summary" else "MALFORMED BUT PRESERVED",
            stop_reason="stop",
            input_messages=(),
            output="{}",
            usage=None,
            config="{}",
            prompt_tokens=10,
            log_path=str(kwargs["log_dir"]) + "/inspect.eval",
        )

    monkeypatch.setattr(pipeline, "execute_actor", actor)
    monkeypatch.setattr(pipeline, "summarize_history", generate)
    monkeypatch.setattr(pipeline, "report_context", generate)
    return pipeline.PipelineServices(
        model=get_model("mockllm/model"),
        tokenizer=WordTokenizer(),
        sandbox_factory=lambda task, environment: None,
        progress=lambda message: None,
    )


def test_collection_separates_truth_and_contexts_then_reports_every_planned_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repo(tmp_path)
    plan = prepare(root)
    svc = services(monkeypatch)
    run = root / "runs/pilot"
    collected = asyncio.run(pipeline.collect_study(root, run, svc))
    assert collected.completed == 8
    assert not collected.failures
    for history in plan.histories:
        directory = run / "histories" / history.history_id
        assert json.loads((directory / "truth.json").read_text())["all_steps_complete"] == "no"
        assert (directory / "summary.json").exists()
        assert len(list((directory / "contexts").glob("*.json"))) == 4
        assert "all_steps_complete" not in (directory / "contexts/C.json").read_text()
    reported = asyncio.run(pipeline.report_study(root, run, svc))
    assert reported.completed == 32
    assert not reported.failures
    records = [json.loads(path.read_text()) for path in (run / "reports").glob("*.json")]
    assert len(records) == 32
    assert all(record["text"] == "MALFORMED BUT PRESERVED" for record in records)
    execution_order = json.loads((run / "report-started.json").read_text())["report_order"]
    assert execution_order == [record.model_dump(mode="json") for record in plan.reports]
    with pytest.raises(FileExistsError):
        asyncio.run(pipeline.collect_study(root, run, svc))
    with pytest.raises(FileExistsError):
        asyncio.run(pipeline.report_study(root, run, svc))


@pytest.mark.parametrize(
    "stage,completed,missing", [("actor", 0, 32), ("summary", 8, 8), ("report", 8, 32)]
)
def test_failure_records_preserve_missing_planned_cells_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, completed: int, missing: int
) -> None:
    root = repo(tmp_path)
    prepare(root)
    svc = services(monkeypatch, fail_stage=stage)
    run = root / "runs/pilot"
    collected = asyncio.run(pipeline.collect_study(root, run, svc))
    assert collected.completed == completed
    reported = asyncio.run(pipeline.report_study(root, run, svc))
    assert len(reported.failures) == missing
    assert reported.completed + len(reported.failures) == 32
    failures = collected.failures + reported.failures
    assert all(failure.attempt == 1 for failure in failures)
    assert any(failure.log_path == f"{stage}-failed.eval" for failure in failures)
    assert all(failure.history_id for failure in failures)


def test_reporting_rejects_changed_collected_evidence_before_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repo(tmp_path)
    plan = prepare(root)
    svc = services(monkeypatch)
    run = root / "runs/pilot"
    with pytest.raises(FileNotFoundError):
        asyncio.run(pipeline.report_study(root, run, svc))
    asyncio.run(pipeline.collect_study(root, run, svc))
    (run / "histories" / plan.histories[0].history_id / "contexts/D.json").write_text("changed")
    with pytest.raises(ValueError, match="frozen file changed"):
        asyncio.run(pipeline.report_study(root, run, svc))
    assert not (run / "report-started.json").exists()


def test_heldout_plan_requires_frozen_balanced_full_design(tmp_path: Path) -> None:
    root = repo(tmp_path, split="heldout", count=12)
    config = pipeline.load_config(root / "config.yaml")
    tasks = tuple(fixture_task(i, "heldout") for i in range(12))
    with pytest.raises(ValueError, match="frozen protocol"):
        pipeline.build_plan(tasks, config, run_id="main", split="heldout")
    config = config.model_copy(update={"protocol_status": "frozen"})
    plan = pipeline.build_plan(tasks, config, run_id="main", split="heldout")
    assert len(plan.histories) == 24 and len(plan.reports) == 192
    assert all(
        len({r.seed for r in plan.reports if r.history_id == h.history_id and r.repetition == rep})
        == 1
        for h in plan.histories
        for rep in (0, 1)
    )
    with pytest.raises(ValueError, match="repetitions"):
        pipeline.build_plan(tasks, config, run_id="main", split="heldout", repetitions=1)
    bad = tuple(task.model_copy(update={"challenge": Environment.BLOCKED_TESTS}) for task in tasks)
    with pytest.raises(ValueError, match="challenge assignment"):
        pipeline.build_plan(bad, config, run_id="main", split="heldout")
    small = config.model_copy(update={"heldout_tasks": 8})
    with pytest.raises(ValueError, match="amendment"):
        pipeline.build_plan(tasks[:8], small, run_id="small", split="heldout")
    amended = pipeline.build_plan(
        tasks[:8],
        small.model_copy(update={"amendment_note": "Feasibility: eight tasks."}),
        run_id="small",
        split="heldout",
    )
    assert len(amended.reports) == 128


def test_heldout_audit_binds_every_D_context_before_any_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repo(tmp_path, split="heldout", count=12)
    config = yaml.safe_load((root / "config.yaml").read_text())
    config["protocol_status"] = "frozen"
    (root / "config.yaml").write_text(yaml.safe_dump(config))
    ed = root / "docs/experiments/ED-001-context-comparison.md"
    ed.write_text("**Status:** Proposed; not frozen.")
    kwargs = dict(
        config_path=root / "config.yaml",
        task_dir=root / "tasks/heldout",
        run_dir=root / "runs/main",
        run_id="main",
        split="heldout",
        created_at="now",
    )
    with pytest.raises(ValueError, match="Frozen ED"):
        pipeline.prepare_study(root, **kwargs)
    ed.write_text("**Status:** Frozen.")
    validation = fixture_validation(tuple(fixture_task(i, "heldout") for i in range(12)))
    validation_path = root / "validation.json"
    validation_path.write_text(validation.model_dump_json())
    with pytest.raises(ValueError, match="fixture validation"):
        pipeline.prepare_study(root, **kwargs)
    plan = pipeline.prepare_study(root, **kwargs, validation_path=validation_path)
    run = root / "runs/main"
    svc = services(monkeypatch)
    asyncio.run(pipeline.collect_study(root, run, svc))
    with pytest.raises(ValueError, match="summary audit"):
        asyncio.run(pipeline.report_study(root, run, svc))
    assert not (run / "report-started.json").exists()
    contexts = [
        pipeline.ReportingContext.model_validate_json(path.read_bytes())
        for path in (run / "histories").glob("*/contexts/D.json")
    ]
    supports = [
        pipeline.ContextSupport(
            history_id=context.history_id,
            context_digest=pipeline.context_digest(context),
            code_saved="unknown",
            note_saved="unknown",
            verification="unknown",
            all_steps_complete="unknown",
            reviewer_id="development-assistant",
            reviewer_kind="assistant",
            notes="Audit before reporting.",
        )
        for context in contexts
    ]
    audit_path = root / "audit.json"
    audit_path.write_text(
        pipeline.SummaryAuditManifest(
            created_at="now", supports=tuple(supports[:-1])
        ).model_dump_json()
    )
    with pytest.raises(ValueError, match="every available D"):
        asyncio.run(pipeline.report_study(root, run, svc, audit_path=audit_path))
    assert not (run / "reports").exists()
    audit_path.write_text(
        pipeline.SummaryAuditManifest(created_at="now", supports=tuple(supports)).model_dump_json()
    )
    result = asyncio.run(pipeline.report_study(root, run, svc, audit_path=audit_path))
    assert result.completed == len(plan.reports) == 192
    frozen_audit = json.loads((run / "summary-audit.json").read_text())
    assert {support["reviewer_kind"] for support in frozen_audit["supports"]} == {"assistant"}


def test_validate_tasks_uses_sandbox_full_run_evidence_and_preserves_failures(
    tmp_path: Path,
) -> None:
    from contextlib import contextmanager

    from context_fidelity.adapters.sandbox import SandboxError
    from context_fidelity.contracts import ToolEvent, source_version

    root = repo(tmp_path)

    @contextmanager
    def sandbox(task: TaskSpec, environment: Environment):
        assert environment == Environment.NORMAL
        if task.task_id == "task-3":
            raise SandboxError("Docker unavailable")

        class ValidationSandbox:
            reference = False

            def run_tests(self):
                ids = tuple(test.test_id for test in task.tests)
                passed = self.reference and task.task_id != "task-2"
                return ToolEvent(
                    event_id="e2" if self.reference else "e1",
                    tool_name="run_tests",
                    success=True,
                    message="completed",
                    source_version=source_version(
                        task.reference_source if self.reference else task.initial_source
                    ),
                    test_ids=ids,
                    expected_test_ids=ids,
                    full_suite=True,
                    passed_test_ids=ids if passed else (),
                    failed_test_ids=() if passed else ids,
                )

            def write_file(self, path: str, content: str):
                assert path == task.source_file and content == task.reference_source
                self.reference = True

        yield ValidationSandbox()

    result = pipeline.validate_tasks(root / "tasks/dev", root / "validation.json", sandbox)
    assert len(result.records) == 4
    assert [task.valid for task in result.records] == [True, True, False, False]
    assert result.records[-1].error == "Docker unavailable"
    assert result.records[0].initial.test_ids == ("zero",)
    assert result.records[0].task_sha256 == source_version(fixture_task(0).model_dump_json())
    assert json.loads((root / "validation.json").read_text())["records"][2]["valid"] is False
    with pytest.raises(FileExistsError):
        pipeline.validate_tasks(root / "tasks/dev", root / "validation.json", sandbox)


def fixture_validation(tasks: tuple[TaskSpec, ...]) -> pipeline.FixtureValidation:
    from context_fidelity.contracts import ToolEvent, source_version

    records = []
    for task in tasks:
        ids = tuple(test.test_id for test in task.tests)
        events = [
            ToolEvent(
                event_id=f"e{index + 1}",
                tool_name="run_tests",
                success=True,
                message="completed",
                source_version=source_version(source),
                test_ids=ids,
                expected_test_ids=ids,
                failed_test_ids=ids if index == 0 else (),
                passed_test_ids=ids if index == 1 else (),
                full_suite=True,
            )
            for index, source in enumerate((task.initial_source, task.reference_source))
        ]
        records.append(
            pipeline.TaskValidation(
                task_id=task.task_id,
                split=task.split,
                task_sha256=source_version(task.model_dump_json()),
                valid=True,
                initial=events[0],
                reference=events[1],
            )
        )
    return pipeline.FixtureValidation(duration_seconds=1, records=tuple(records))


@pytest.mark.parametrize(
    "key,value",
    [
        ("model_id", "different"),
        ("model_revision", "0" * 40),
        ("sandbox_image", "python:latest"),
        ("actor_temperature", 0.1),
        ("summary_temperature", 0.1),
        ("report_temperature", 0),
        ("top_p", 0.5),
    ],
)
def test_config_cannot_claim_unimplemented_settings(
    tmp_path: Path, key: str, value: object
) -> None:
    root = repo(tmp_path)
    config_path = root / "config.yaml"
    data = yaml.safe_load(config_path.read_text())
    data[key] = value
    config_path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError):
        pipeline.load_config(config_path)


@pytest.mark.parametrize("change", ["duplicate", "split", "repetitions", "seed", "outside"])
def test_public_plan_boundary_rejects_invalid_matrices(tmp_path: Path, change: str) -> None:
    root = repo(tmp_path)
    tasks = tuple(fixture_task(i) for i in range(4))
    config = pipeline.load_config(root / "config.yaml")
    if change == "duplicate":
        tasks = (tasks[0],) * 4
    elif change == "split":
        tasks = (fixture_task(0, "heldout"), *tasks[1:])
    with pytest.raises(ValueError):
        if change == "outside":
            pipeline.prepare_study(
                root,
                config_path=root / "config.yaml",
                task_dir=root / "tasks/dev",
                run_dir=root.parent / "escaped",
                run_id="dev",
                split="dev",
                created_at="now",
            )
        else:
            pipeline.build_plan(
                tasks,
                config,
                run_id="dev",
                split="dev",
                repetitions=3 if change == "repetitions" else None,
                seed=-1 if change == "seed" else None,
            )


def test_changed_planned_cells_cannot_be_loaded_even_with_rehashed_file(tmp_path: Path) -> None:
    root = repo(tmp_path)
    plan = prepare(root)
    run = root / "runs/pilot"
    (run / "plan.json").write_text(
        plan.model_copy(update={"reports": plan.reports[:-1]}).model_dump_json()
    )
    manifest = pipeline.freeze_files(root, [run / "plan.json"], created_at="now")
    (run / "freeze.json").write_text(manifest.model_dump_json())
    with pytest.raises(ValueError, match="planned matrix"):
        pipeline.load_study(root, run)


def test_compact_overflow_keeps_other_arms_and_context_failure_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    root = repo(tmp_path)
    prepare(root)

    class LargeEvidence(WordTokenizer):
        def count(self, text: str) -> int:
            return 999 if text.startswith("Complete write/test record") else super().count(text)

    svc = replace(services(monkeypatch), tokenizer=LargeEvidence())
    run = root / "runs/pilot"
    result = asyncio.run(pipeline.collect_study(root, run, svc))
    assert result.completed == 8
    assert len(result.failures) == 8
    assert {failure.arm for failure in result.failures} == {Arm.C}
    assert all(failure.retry_policy == "deterministic_no_retry" for failure in result.failures)
    reports = asyncio.run(pipeline.report_study(root, run, svc))
    assert reports.completed == 24 and len(reports.failures) == 8


def test_actor_cannot_change_planned_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repo(tmp_path)
    prepare(root)
    svc = services(monkeypatch)
    actor = pipeline.execute_actor

    async def wrong(*args: object, **kwargs: object) -> History:
        history = await actor(*args, **kwargs)
        return history.model_copy(update={"history_id": "another-history"})

    monkeypatch.setattr(pipeline, "execute_actor", wrong)
    with pytest.raises(ValueError, match="planned task/environment"):
        asyncio.run(pipeline.collect_study(root, root / "runs/pilot", svc))


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "stale", "false_pass"])
def test_fixture_validation_cannot_trust_success_flag(tmp_path: Path, mutation: str) -> None:
    tasks = (fixture_task(0), fixture_task(1))
    validation = fixture_validation(tasks)
    records = validation.records
    if mutation == "duplicate":
        records = (records[0], records[0])
    elif mutation == "missing":
        records = records[:1]
    elif mutation == "stale":
        records = (records[0].model_copy(update={"task_sha256": "0" * 64}), records[1])
    else:
        records = (records[0].model_copy(update={"reference": records[0].initial}), records[1])
    path = tmp_path / "validation.json"
    path.write_text(validation.model_copy(update={"records": records}).model_dump_json())
    with pytest.raises(ValueError):
        pipeline.validate_fixture_record(path, tasks)


def test_eight_task_assignment_requires_two_recovery_tasks(tmp_path: Path) -> None:
    root = repo(tmp_path)
    config = pipeline.load_config(root / "config.yaml").model_copy(
        update={
            "heldout_tasks": 8,
            "protocol_status": "frozen",
            "amendment_note": "Feasibility amendment",
        }
    )
    tasks = tuple(fixture_task(i, "heldout") for i in range(8))
    wrong = (tasks[0].model_copy(update={"challenge": Environment.RECOVERABLE_NOTE}), *tasks[1:])
    with pytest.raises(ValueError, match="challenge assignment"):
        pipeline.build_plan(wrong, config, run_id="main", split="heldout")


def test_loading_a_relocated_run_rejects_the_unbound_plan(tmp_path: Path) -> None:
    import shutil

    root = repo(tmp_path / "repo")
    prepare(root)
    original = root / "runs/pilot"
    for destination in (root / "runs/copy", tmp_path / "external-copy"):
        shutil.copytree(original, destination)
        with pytest.raises(ValueError, match="inside repository|bind.*plan"):
            pipeline.load_study(root, destination)
