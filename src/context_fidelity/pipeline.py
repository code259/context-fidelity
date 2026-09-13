"""Small, immutable experiment coordinator over the Inspect phase boundaries.

A prepared study fixes every requested cell. Collection and reporting are each
single-use: interrupted runs remain inspectable and cannot overwrite valid
outputs. Unclassified Inspect failures are recorded without automatic retries.
"""

import hashlib
import random
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Literal

import yaml
from inspect_ai.model import Model
from pydantic import Field

from context_fidelity.adapters.model import MODEL_ID, MODEL_REVISION, ContextOverflow, Tokenizer
from context_fidelity.adapters.sandbox import DEFAULT_IMAGE, SandboxError
from context_fidelity.artifacts import (
    FreezeManifest,
    freeze_files,
    load_tasks,
    save_json,
    verify_freeze,
)
from context_fidelity.contexts import BudgetExceeded, ReportingContext, make_context
from context_fidelity.contracts import (
    Arm,
    Digest,
    Environment,
    History,
    Identifier,
    Record,
    TaskSpec,
    ToolEvent,
    source_version,
)
from context_fidelity.evidence import derive_truth
from context_fidelity.experiment import (
    GenerationFailure,
    Sandbox,
    execute_actor,
    report_context,
    summarize_history,
)
from context_fidelity.score import ContextSupport, context_digest


class StudyConfig(Record):
    protocol_status: Literal["development", "frozen"] = "development"
    protocol_version: str = "0.3"
    amendment_note: str = ""
    model_id: str = MODEL_ID
    model_revision: str = MODEL_REVISION
    backend: Literal["vllm-0.10.2"]
    inspect_provider: Literal["openai-api/local"] = "openai-api/local"
    precision: Literal["bfloat16"]
    context_window: int = Field(gt=1024)
    sandbox_image: str
    seed: int = Field(ge=0, le=2147483647)
    actor_tool_limit: int = Field(ge=1, le=12)
    actor_output_tokens: Literal[1024]
    actor_temperature: float
    summary_payload_cap: Literal[384]
    summary_temperature: float
    report_output_tokens: Literal[512]
    report_temperature: float
    top_p: float
    top_k: Literal[-1]
    report_repetitions: Literal[2]
    bootstrap_resamples: Literal[10000]
    test_timeout_seconds: float = Field(gt=0, le=10)
    heldout_tasks: Literal[8, 12]
    development_tasks: Literal[4]


class PlannedHistory(Record):
    history_id: Identifier
    task_id: Identifier
    environment: Environment
    actor_seed: int
    summary_seed: int


class PlannedReport(Record):
    history_id: Identifier
    arm: Literal[Arm.A, Arm.B, Arm.C, Arm.D]
    repetition: int = Field(ge=0)
    seed: int


class StudyPlan(Record):
    run_id: Identifier
    split: Literal["dev", "heldout"]
    seed: int = Field(ge=0, le=2147483647)
    repetitions: int = Field(ge=1, le=2)
    config: StudyConfig
    tasks: tuple[TaskSpec, ...]
    histories: tuple[PlannedHistory, ...]
    reports: tuple[PlannedReport, ...]


def load_config(path: Path) -> StudyConfig:
    config = StudyConfig.model_validate(yaml.safe_load(path.read_text()))
    for key, expected in {
        "actor_temperature": 0.0,
        "summary_temperature": 0.0,
        "report_temperature": 0.7,
        "top_p": 1.0,
    }.items():
        if getattr(config, key) != expected:
            raise ValueError(f"{key} differs from the implemented generation settings")
    if config.model_id != MODEL_ID or config.model_revision != MODEL_REVISION:
        raise ValueError("model configuration differs from the pinned adapter")
    if config.sandbox_image != DEFAULT_IMAGE:
        raise ValueError("sandbox_image must match the pinned sandbox")
    return config


def build_plan(
    tasks: tuple[TaskSpec, ...],
    config: StudyConfig,
    *,
    run_id: str,
    split: Literal["dev", "heldout"],
    seed: int | None = None,
    repetitions: int | None = None,
) -> StudyPlan:
    """Fix the complete factorial matrix and random report order before acting."""
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]*", run_id):
        raise ValueError("run_id must be a safe identifier")
    if len({task.task_id for task in tasks}) != len(tasks):
        raise ValueError("duplicate task ID")
    if any(task.split != split for task in tasks):
        raise ValueError("task split differs from requested split")
    if any(task.challenge == Environment.NORMAL for task in tasks):
        raise ValueError("assigned challenge cannot be normal")
    expected = config.development_tasks if split == "dev" else config.heldout_tasks
    if len(tasks) != expected:
        raise ValueError(f"{split} requires exactly {expected} tasks")
    repetitions = repetitions if repetitions is not None else (1 if split == "dev" else 2)
    if split == "heldout":
        if config.protocol_status != "frozen":
            raise ValueError("heldout requires a frozen protocol")
        if config.heldout_tasks == 8 and not config.amendment_note.strip():
            raise ValueError("eight-task heldout requires a documented amendment")
        if repetitions != config.report_repetitions:
            raise ValueError("heldout must use the frozen report repetitions")
        counts = Counter(task.challenge for task in tasks)
        expected_counts = {
            Environment.BLOCKED_TESTS: 4 if expected == 12 else 3,
            Environment.PARTIAL_TESTS: 4 if expected == 12 else 3,
            Environment.RECOVERABLE_NOTE: 4 if expected == 12 else 2,
        }
        if counts != expected_counts:
            raise ValueError("heldout challenge assignment differs from the frozen design")
    if repetitions not in (1, 2):
        raise ValueError("repetitions must be one or two")
    seed = config.seed if seed is None else seed
    if not 0 <= seed <= 2147483647:
        raise ValueError("seed must be between zero and 2147483647")
    rng = random.Random(seed)
    histories: list[PlannedHistory] = []
    reports: list[PlannedReport] = []
    ordered_tasks = tuple(sorted(tasks, key=lambda task: task.task_id))
    for task in ordered_tasks:
        for environment in (Environment.NORMAL, task.challenge):
            history_id = f"{run_id}-{task.task_id}-{environment.value}"
            histories.append(
                PlannedHistory(
                    history_id=history_id,
                    task_id=task.task_id,
                    environment=environment,
                    actor_seed=rng.randrange(2147483648),
                    summary_seed=rng.randrange(2147483648),
                )
            )
            for repetition in range(repetitions):
                report_seed = rng.randrange(2147483648)
                arms: tuple[Literal[Arm.A, Arm.B, Arm.C, Arm.D], ...] = (Arm.A, Arm.B, Arm.C, Arm.D)
                reports.extend(
                    PlannedReport(
                        history_id=history_id, arm=arm, repetition=repetition, seed=report_seed
                    )
                    for arm in arms
                )
    rng.shuffle(reports)
    return StudyPlan(
        run_id=run_id,
        split=split,
        seed=seed,
        repetitions=repetitions,
        config=config,
        tasks=ordered_tasks,
        histories=tuple(histories),
        reports=tuple(reports),
    )


def prepare_study(
    root: Path,
    *,
    config_path: Path,
    task_dir: Path,
    run_dir: Path,
    run_id: str,
    split: Literal["dev", "heldout"],
    created_at: str,
    seed: int | None = None,
    repetitions: int | None = None,
    validation_path: Path | None = None,
) -> StudyPlan:
    """Create the full plan and content hashes without connecting to a model."""
    root = root.resolve()
    if not run_dir.resolve().is_relative_to(root):
        raise ValueError("run directory must be inside repository")
    config = load_config(config_path)
    plan = build_plan(
        load_tasks(task_dir, split=split),
        config,
        run_id=run_id,
        split=split,
        seed=seed,
        repetitions=repetitions,
    )
    protocols = [
        root / "docs/project-spec.md",
        root / "docs/review-rubric.md",
        root / "docs/experiments/ED-001-context-comparison.md",
        root / "docs/experiments/ED-002-evidence-restoration.md",
    ]
    if split == "heldout":
        for path in protocols[2:]:
            if not re.search(r"\*\*Status:\*\*\s*Frozen\b", path.read_text(), re.IGNORECASE):
                raise ValueError(f"heldout requires a Frozen ED status: {path.name}")
    if split == "heldout" and validation_path is None:
        raise ValueError("heldout prepare requires fixture validation")
    if validation_path is not None:
        validate_fixture_record(validation_path, plan.tasks)
    paths = [
        config_path,
        root / "uv.lock",
        *protocols,
        *([validation_path] if validation_path is not None else []),
        *sorted(task_dir.glob("*.json")),
        *sorted((root / "src/context_fidelity").rglob("*.py")),
    ]
    # Validate all inputs and containment before creating a run directory.
    freeze_files(root, paths, created_at=created_at)
    run_dir.mkdir(parents=True, exist_ok=True)
    if any(run_dir.iterdir()):
        raise FileExistsError(f"run directory must be empty: {run_dir}")
    save_json(run_dir / "plan.json", plan)
    save_json(
        run_dir / "freeze.json",
        freeze_files(root, [*paths, run_dir / "plan.json"], created_at=created_at),
    )
    return plan


def load_study(root: Path, run_dir: Path) -> StudyPlan:
    if not run_dir.resolve().is_relative_to(root.resolve()):
        raise ValueError("run directory must be inside repository")
    manifest = FreezeManifest.model_validate_json((run_dir / "freeze.json").read_bytes())
    plan_path = (run_dir / "plan.json").resolve().relative_to(root.resolve()).as_posix()
    if plan_path not in {item.path for item in manifest.files}:
        raise ValueError("freeze must bind this run's plan")
    verify_freeze(root, manifest)
    plan = StudyPlan.model_validate_json((run_dir / "plan.json").read_bytes())
    expected = build_plan(
        plan.tasks,
        plan.config,
        run_id=plan.run_id,
        split=plan.split,
        seed=plan.seed,
        repetitions=plan.repetitions,
    )
    if plan != expected:
        raise ValueError("saved plan does not match the deterministic planned matrix")
    return plan


TaskSandboxFactory = Callable[[TaskSpec, Environment], AbstractContextManager[Sandbox]]


@dataclass(frozen=True)
class PipelineServices:
    model: Model
    tokenizer: Tokenizer
    sandbox_factory: TaskSandboxFactory
    progress: Callable[[str], None] = print


class StageFailure(Record):
    history_id: Identifier
    stage: Literal["actor", "summary", "context", "report"]
    arm: Arm | None = None
    repetition: int | None = None
    attempt: Literal[1] = 1
    error: str
    log_path: str
    retry_policy: Literal["unclassified_no_retry", "deterministic_no_retry", "missing_input"]


class PhaseResult(Record):
    phase: Literal["collect", "report"]
    completed: int = Field(ge=0)
    failures: tuple[StageFailure, ...]


class SummaryAuditManifest(Record):
    created_at: str = Field(min_length=1)
    supports: tuple[ContextSupport, ...]


def _failure(
    history_id: str,
    stage: Literal["actor", "summary", "context", "report"],
    error: GenerationFailure | SandboxError | ContextOverflow | BudgetExceeded,
    log_dir: Path,
    *,
    arm: Arm | None = None,
    repetition: int | None = None,
) -> StageFailure:
    return StageFailure(
        history_id=history_id,
        stage=stage,
        arm=arm,
        repetition=repetition,
        error=str(error),
        log_path=error.log_path if isinstance(error, GenerationFailure) else str(log_dir),
        retry_policy="deterministic_no_retry"
        if isinstance(error, (BudgetExceeded, ContextOverflow))
        else "unclassified_no_retry",
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def collect_study(root: Path, run_dir: Path, services: PipelineServices) -> PhaseResult:
    """Collect all actors and summaries before any main reporting is possible."""
    plan = load_study(root, run_dir)
    save_json(run_dir / "collect-started.json", {"created_at": _now()})
    failures: list[StageFailure] = []
    completed = 0
    tasks = {task.task_id: task for task in plan.tasks}
    for planned in plan.histories:
        task = tasks[planned.task_id]
        directory = run_dir / "histories" / planned.history_id
        log_dir = directory / "actor/attempt-1"
        services.progress(f"collect actor {planned.history_id}")
        try:
            history = await execute_actor(
                task,
                planned.environment,
                history_id=planned.history_id,
                model=services.model,
                tokenizer=services.tokenizer,
                sandbox_factory=partial(services.sandbox_factory, task, planned.environment),
                log_dir=log_dir,
                seed=planned.actor_seed,
                max_context_tokens=plan.config.context_window,
                max_tool_calls=plan.config.actor_tool_limit,
            )
        except (GenerationFailure, SandboxError, ContextOverflow) as error:
            failure = _failure(planned.history_id, "actor", error, log_dir)
            failures.append(failure)
            save_json(directory / "actor-failure.json", failure)
            continue
        if (
            history.history_id != planned.history_id
            or history.task != task
            or history.environment != planned.environment
        ):
            raise ValueError("actor history differs from its planned task/environment")
        save_json(directory / "history.json", history)
        save_json(directory / "truth.json", derive_truth(history))
        completed += 1
        for arm in (Arm.A, Arm.B, Arm.C):
            try:
                context = make_context(
                    history, arm, services.tokenizer.count, cap=plan.config.summary_payload_cap
                )
            except BudgetExceeded as error:
                failure = _failure(planned.history_id, "context", error, directory, arm=arm)
                failures.append(failure)
                save_json(directory / f"context-{arm}-failure.json", failure)
            else:
                save_json(directory / "contexts" / f"{arm}.json", context)
        log_dir = directory / "summary/attempt-1"
        services.progress(f"collect summary {planned.history_id}")
        try:
            summary = await summarize_history(
                history,
                model=services.model,
                tokenizer=services.tokenizer,
                log_dir=log_dir,
                seed=planned.summary_seed,
                max_context_tokens=plan.config.context_window,
            )
            save_json(directory / "summary.json", summary)
            context = make_context(
                history,
                Arm.D,
                services.tokenizer.count,
                cap=plan.config.summary_payload_cap,
                summary=summary.text,
            )
        except (GenerationFailure, ContextOverflow, BudgetExceeded) as error:
            failure = _failure(planned.history_id, "summary", error, log_dir, arm=Arm.D)
            failures.append(failure)
            save_json(directory / "summary-failure.json", failure)
        else:
            save_json(directory / "contexts/D.json", context)
    result = PhaseResult(phase="collect", completed=completed, failures=tuple(failures))
    save_json(run_dir / "collect.json", result)
    paths = [
        run_dir / "collect.json",
        *(path for path in (run_dir / "histories").rglob("*") if path.is_file()),
    ]
    save_json(run_dir / "collection-freeze.json", freeze_files(run_dir, paths, created_at=_now()))
    return result


def _reporting_inputs(run_dir: Path, collection: FreezeManifest) -> dict[str, bytes]:
    """Snapshot only hash-bound data; late files cannot repair frozen missingness."""
    frozen = {item.path: item.sha256 for item in collection.files}
    inputs: dict[str, bytes] = {}
    paths = [
        *(run_dir / "histories").glob("*/history.json"),
        *(run_dir / "histories").glob("*/contexts/*.json"),
    ]
    for path in sorted(paths):
        relative = path.relative_to(run_dir).as_posix()
        if relative not in frozen:
            raise ValueError(f"reporting input is not bound by collection freeze: {relative}")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != frozen[relative]:
            raise ValueError(f"frozen reporting input changed: {relative}")
        inputs[relative] = content
    return inputs


def _audit_before_reports(audit_path: Path, inputs: Mapping[str, bytes]) -> SummaryAuditManifest:
    audit = SummaryAuditManifest.model_validate_json(audit_path.read_bytes())
    available = {
        context.history_id: context_digest(context)
        for path, content in inputs.items()
        if path.endswith("/contexts/D.json")
        for context in (ReportingContext.model_validate_json(content),)
    }
    audited = {support.history_id: support.context_digest for support in audit.supports}
    if len(audited) != len(audit.supports) or audited != available:
        raise ValueError("summary audit must bind every available D context exactly once")
    return audit


async def report_study(
    root: Path,
    run_dir: Path,
    services: PipelineServices,
    *,
    audit_path: Path | None = None,
) -> PhaseResult:
    """Collect each planned report once; retain malformed reports and missing cells."""
    plan = load_study(root, run_dir)
    collection = FreezeManifest.model_validate_json(
        (run_dir / "collection-freeze.json").read_bytes()
    )
    verify_freeze(run_dir, collection)
    if (run_dir / "report-started.json").exists():
        raise FileExistsError(f"report phase already started: {run_dir}")
    if plan.split == "heldout" and audit_path is None:
        raise ValueError("heldout reporting requires a frozen pre-report summary audit")
    inputs = _reporting_inputs(run_dir, collection)
    audit = _audit_before_reports(audit_path, inputs) if audit_path is not None else None
    if audit is not None:
        save_json(run_dir / "summary-audit.json", audit)
    save_json(
        run_dir / "report-started.json",
        {
            "created_at": _now(),
            "report_order": [report.model_dump(mode="json") for report in plan.reports],
            "summary_audit": audit.model_dump(mode="json") if audit is not None else None,
        },
    )
    failures: list[StageFailure] = []
    completed = 0
    for planned in plan.reports:
        services.progress(f"report {planned.history_id} {planned.arm} {planned.repetition}")
        directory = run_dir / "histories" / planned.history_id
        log_dir = directory / f"report-{planned.arm}-{planned.repetition}/attempt-1"
        history_path = f"histories/{planned.history_id}/history.json"
        context_path = f"histories/{planned.history_id}/contexts/{planned.arm}.json"
        name = f"{planned.history_id}-{planned.arm}-{planned.repetition}"
        failure: StageFailure | None = None
        if history_path not in inputs or context_path not in inputs:
            failure = StageFailure(
                history_id=planned.history_id,
                stage="report",
                arm=planned.arm,
                repetition=planned.repetition,
                error="planned history or context unavailable",
                log_path=str(directory),
                retry_policy="missing_input",
            )
        else:
            history = History.model_validate_json(inputs[history_path])
            context = ReportingContext.model_validate_json(inputs[context_path])
            try:
                record = await report_context(
                    history,
                    context,
                    repetition=planned.repetition,
                    model=services.model,
                    tokenizer=services.tokenizer,
                    log_dir=log_dir,
                    seed=planned.seed,
                    max_context_tokens=plan.config.context_window,
                )
            except (GenerationFailure, ContextOverflow) as error:
                failure = _failure(
                    planned.history_id,
                    "report",
                    error,
                    log_dir,
                    arm=planned.arm,
                    repetition=planned.repetition,
                )
            else:
                save_json(run_dir / "reports" / f"{name}.json", record)
                completed += 1
        if failure is not None:
            failures.append(failure)
            save_json(run_dir / "report-failures" / f"{name}.json", failure)
    result = PhaseResult(phase="report", completed=completed, failures=tuple(failures))
    save_json(run_dir / "report.json", result)
    paths = [
        run_dir / "report-started.json",
        run_dir / "report.json",
        *run_dir.glob("summary-audit.json"),
        *(run_dir / "reports").glob("*.json"),
        *(run_dir / "report-failures").glob("*.json"),
        *(
            path
            for attempt in (run_dir / "histories").glob("*/report-*/attempt-1")
            for path in attempt.rglob("*")
            if path.is_file()
        ),
    ]
    save_json(run_dir / "report-freeze.json", freeze_files(run_dir, paths, created_at=_now()))
    return result


class TaskValidation(Record):
    task_id: Identifier
    split: Literal["dev", "heldout"]
    task_sha256: Digest
    evaluator_only: Literal[True] = True
    valid: bool
    initial: ToolEvent | None = None
    reference: ToolEvent | None = None
    error: str | None = None


class FixtureValidation(Record):
    evaluator_only: Literal[True] = True
    duration_seconds: float = Field(ge=0)
    records: tuple[TaskValidation, ...]


def _valid_fixture(task: TaskSpec, initial: ToolEvent | None, reference: ToolEvent | None) -> bool:
    ids = {test.test_id for test in task.tests}
    return (
        initial is not None
        and reference is not None
        and initial.tool_name == reference.tool_name == "run_tests"
        and initial.full_suite
        and reference.full_suite
        and bool(initial.failed_test_ids)
        and not reference.failed_test_ids
        and set(initial.expected_test_ids) == ids
        and set(reference.expected_test_ids) == ids
        and initial.source_version == source_version(task.initial_source)
        and reference.source_version == source_version(task.reference_source)
    )


def validate_fixture_record(path: Path, tasks: tuple[TaskSpec, ...]) -> None:
    validation = FixtureValidation.model_validate_json(path.read_bytes())
    by_id = {record.task_id: record for record in validation.records}
    if len(by_id) != len(validation.records):
        raise ValueError("duplicate fixture validation task ID")
    for task in tasks:
        record = by_id.get(task.task_id)
        if (
            record is None
            or record.task_sha256 != source_version(task.model_dump_json())
            or record.split != task.split
            or not record.valid
            or not _valid_fixture(task, record.initial, record.reference)
        ):
            raise ValueError(f"fixture validation missing, stale, or unsuccessful: {task.task_id}")


def validate_tasks(
    task_dir: Path,
    output: Path,
    sandbox_factory: TaskSandboxFactory,
    *,
    progress: Callable[[str], None] = print,
) -> FixtureValidation:
    """Validate initial and reference programs through a normal sandbox only."""
    tasks = load_tasks(task_dir)
    if output.exists():
        raise FileExistsError(f"validation artifact already exists: {output}")
    save_json(output.with_suffix(".started.json"), {"created_at": _now()})
    started = time.monotonic()
    records: list[TaskValidation] = []
    for task in tasks:
        progress(f"validate {task.task_id}")
        initial: ToolEvent | None = None
        reference: ToolEvent | None = None
        error: str | None = None
        try:
            with sandbox_factory(task, Environment.NORMAL) as sandbox:
                initial = sandbox.run_tests()
                sandbox.write_file(task.source_file, task.reference_source)
                reference = sandbox.run_tests()
        except SandboxError as caught:
            error = str(caught)
        records.append(
            TaskValidation(
                task_id=task.task_id,
                split=task.split,
                task_sha256=source_version(task.model_dump_json()),
                initial=initial,
                reference=reference,
                valid=error is None and _valid_fixture(task, initial, reference),
                error=error,
            )
        )
    result = FixtureValidation(duration_seconds=time.monotonic() - started, records=tuple(records))
    save_json(output, result)
    return result
