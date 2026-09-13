"""Offline reanalysis of immutable main-run evidence, with explicit review missingness.

Generation-era source hashes remain provenance; they are not compared with today's
source. The plan and both completed phases must still match their original freezes.
Current analysis code and all analysis inputs receive separate fingerprints.
"""

import hashlib
import json
from pathlib import Path
from typing import Literal

from inspect_ai.model import GenerateConfig, ModelOutput
from pydantic import Field, StrictBool

from context_fidelity.analyze import Observation, PairedEstimate, PairKey, paired_effect
from context_fidelity.artifacts import FreezeManifest, freeze_files, save_json
from context_fidelity.contexts import ReportingContext
from context_fidelity.contracts import Arm, History, Identifier, Record, Truth
from context_fidelity.evidence import derive_truth
from context_fidelity.experiment import GenerationRecord
from context_fidelity.pipeline import (
    PhaseResult,
    PlannedReport,
    StageFailure,
    StudyPlan,
    SummaryAuditManifest,
    build_plan,
)
from context_fidelity.replay import ReplayCase, ReportView
from context_fidelity.score import (
    BlindedReviewBatch,
    BlindReviewInput,
    ContextSupport,
    ProseReview,
    ReviewEvidence,
    Verdict,
    context_digest,
    export_blinded_review,
    parse_report,
    score_report,
)

Metric = Literal["structured_unreliable", "coverage", "unreliable"]
ComparisonName = Literal["C-D", "B-A"]
ARMS = (Arm.A, Arm.B, Arm.C, Arm.D)
COMPARISONS: tuple[tuple[ComparisonName, Arm, Arm], ...] = (
    ("C-D", Arm.C, Arm.D),
    ("B-A", Arm.B, Arm.A),
)


class ReportCell(Record):
    report_id: Identifier
    key: PairKey
    arm: Arm
    generation: GenerationRecord | None
    failure: StageFailure | None
    verdict: Verdict | None
    support_audit_pending: StrictBool


class ArmCounts(Record):
    arm: Arm
    planned: int
    completed: int
    technical_missing: int
    scored: int
    support_audit_pending: int
    primary_pending: int
    review_complete: int
    review_pending: int
    invalid_format: int
    world_false: int
    unsupported: int
    structured_unreliable: int
    primary_unreliable: int
    primary_reliable: int
    mean_coverage: float | None


class MetricInput(Record):
    metric: Metric
    observations: tuple[Observation, ...]


class Comparison(Record):
    comparison: ComparisonName
    metric: Metric
    estimate: PairedEstimate


class RunResults(Record):
    analysis_id: Identifier
    created_at: str = Field(min_length=1)
    plan: StudyPlan
    origin_freeze: FreezeManifest
    collection_freeze: FreezeManifest
    report_freeze: FreezeManifest
    analysis_code: FreezeManifest
    origin_summary_audit: SummaryAuditManifest | None
    summary_audit: SummaryAuditManifest | None
    prose_reviews: tuple[ProseReview, ...]
    planned_pairs: tuple[PairKey, ...]
    cells: tuple[ReportCell, ...]
    arm_counts: tuple[ArmCounts, ...]
    metric_inputs: tuple[MetricInput, ...]
    effects: tuple[Comparison, ...]
    cases: tuple[ReplayCase, ...]
    blinded: BlindedReviewBatch


def _bound(root: Path, name: str, manifest: FreezeManifest) -> bytes:
    hashes = {entry.path: entry.sha256 for entry in manifest.files}
    path = root / name
    if name not in hashes:
        raise ValueError(f"artifact is not bound by freeze: {name}")
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"frozen artifact is missing or outside root: {name}")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != hashes[name]:
        raise ValueError(f"frozen artifact changed: {name}")
    return data


def _snapshot(root: Path, manifest: FreezeManifest) -> dict[str, bytes]:
    return {entry.path: _bound(root, entry.path, manifest) for entry in manifest.files}


def _inventory(
    root: Path, patterns: tuple[str, ...], allowed: set[str], bound: dict[str, bytes]
) -> None:
    for pattern in patterns:
        for path in root.glob(pattern):
            name = path.relative_to(root).as_posix()
            if name not in allowed or name not in bound:
                raise ValueError(f"unplanned or unfrozen artifact: {name}")


def _request(history: History) -> str:
    requests: list[str] = []
    for raw in history.messages:
        message = json.loads(raw)
        if message["role"] != "user" or not isinstance(message.get("content"), str):
            continue
        try:
            content = json.loads(message["content"])
        except ValueError:
            continue
        if isinstance(content, dict) and "initial_manifest" in content:
            requests.append(message["content"])
    if len(requests) != 1:
        raise ValueError("history must preserve exactly one original task request")
    return requests[0]


def _check_phase(
    phase: PhaseResult, name: str, completed: int, failures: list[StageFailure]
) -> None:
    if (
        phase.phase != name
        or phase.completed != completed
        or len(set(phase.failures)) != len(phase.failures)
        or set(phase.failures) != set(failures)
    ):
        raise ValueError(f"{name} phase totals or retained failures disagree with artifacts")


def _failure(
    data: bytes,
    *,
    history_id: str,
    stage: str,
    arm: Arm | None = None,
    repetition: int | None = None,
) -> StageFailure:
    value = StageFailure.model_validate_json(data)
    if (value.history_id, value.stage, value.arm, value.repetition) != (
        history_id,
        stage,
        arm,
        repetition,
    ):
        raise ValueError("failure lineage does not match planned cell")
    return value


def _collection(
    plan: StudyPlan, inputs: dict[str, bytes]
) -> tuple[dict[str, History], dict[tuple[str, Arm], ReportingContext]]:
    histories: dict[str, History] = {}
    contexts: dict[tuple[str, Arm], ReportingContext] = {}
    failures: list[StageFailure] = []
    tasks = {task.task_id: task for task in plan.tasks}
    for planned in plan.histories:
        base = f"histories/{planned.history_id}"
        history_data = inputs.get(base + "/history.json")
        actor_error = inputs.get(base + "/actor-failure.json")
        if (history_data is None) == (actor_error is None):
            raise ValueError("each planned actor requires exactly one frozen outcome")
        if actor_error is not None:
            failures.append(_failure(actor_error, history_id=planned.history_id, stage="actor"))
            continue
        assert history_data is not None
        history = History.model_validate_json(history_data)
        if (
            history.history_id != planned.history_id
            or history.task != tasks[planned.task_id]
            or history.environment != planned.environment
        ):
            raise ValueError("history task/environment lineage differs from plan")
        if Truth.model_validate_json(inputs[base + "/truth.json"]) != derive_truth(history):
            raise ValueError("retained truth differs from independently derived final state")
        histories[planned.history_id] = history
        _request(history)
        for arm in ARMS:
            payload = inputs.get(base + f"/contexts/{arm}.json")
            error = inputs.get(
                base + ("/summary-failure.json" if arm == Arm.D else f"/context-{arm}-failure.json")
            )
            if (payload is None) == (error is None):
                raise ValueError("each context requires exactly one frozen outcome")
            if error is not None:
                failures.append(
                    _failure(
                        error,
                        history_id=planned.history_id,
                        stage="summary" if arm == Arm.D else "context",
                        arm=arm,
                    )
                )
                continue
            assert payload is not None
            context = ReportingContext.model_validate_json(payload)
            if (context.history_id, context.arm) != (planned.history_id, arm):
                raise ValueError("context lineage differs from its frozen path")
            if arm == Arm.D:
                summary = GenerationRecord.model_validate_json(inputs[base + "/summary.json"])
                if (
                    summary.history_id,
                    summary.stage,
                    summary.arm,
                    summary.repetition,
                    summary.seed,
                    summary.text,
                ) != (
                    planned.history_id,
                    "summary",
                    None,
                    None,
                    planned.summary_seed,
                    context.payload,
                ):
                    raise ValueError("ordinary summary lineage or payload differs from context")
            contexts[(planned.history_id, arm)] = context
    _check_phase(
        PhaseResult.model_validate_json(inputs["collect.json"]), "collect", len(histories), failures
    )
    return histories, contexts


def _generation(
    record: GenerationRecord,
    planned: PlannedReport,
    history: History,
    context: ReportingContext,
    plan: StudyPlan,
    inputs: dict[str, bytes],
) -> None:
    if (record.history_id, record.stage, record.arm, record.repetition, record.seed) != (
        planned.history_id,
        "report",
        planned.arm,
        planned.repetition,
        planned.seed,
    ):
        raise ValueError("generation lineage differs from planned report")
    attempt = f"histories/{planned.history_id}/report-{planned.arm}-{planned.repetition}/attempt-1"
    retained = inputs.get(attempt + "/generation.json")
    if retained is None or GenerationRecord.model_validate_json(retained) != record:
        raise ValueError("report differs from retained generation record")
    if attempt + "/" + Path(record.log_path).name not in inputs:
        raise ValueError("generation log is absent from report freeze")
    output = ModelOutput.model_validate_json(record.output)
    if (
        not output.choices
        or output.completion != record.text
        or output.message.text != record.text
        or output.stop_reason != record.stop_reason
        or output.model != plan.config.model_id
        or output.error
    ):
        raise ValueError("raw model output differs from generation metadata")
    usage = json.loads(record.usage) if record.usage is not None else None
    if usage != (output.usage.model_dump(mode="json") if output.usage is not None else None):
        raise ValueError("generation usage differs from raw model output")
    config = GenerateConfig.model_validate_json(record.config)
    if (config.seed, config.max_tokens, config.temperature, config.top_p, config.top_k) != (
        planned.seed,
        plan.config.report_output_tokens,
        plan.config.report_temperature,
        plan.config.top_p,
        plan.config.top_k,
    ):
        raise ValueError("generation configuration differs from frozen plan")
    if (config.extra_body or {}).get("top_k") != plan.config.top_k:
        raise ValueError("generation configuration must preserve explicit provider top-k")
    messages = [json.loads(raw) for raw in record.input_messages]
    prefix = f"Original task request:\n{_request(history)}\n\n"
    if context.arm == Arm.A:
        # Inspect's sample_messages deep-copies carried messages and sets their
        # source to "input". Preserve every other field, independent of JSON spacing.
        expected = [json.loads(raw) | {"source": "input"} for raw in history.messages]
        if messages[:-1] != expected:
            raise ValueError("native generation input differs from frozen history")
    else:
        prefix = f"Supplied work history:\n{context.payload}\n" + prefix
        if len(messages) != 1:
            raise ValueError("fresh generation must contain one reporting input")
    if (
        not messages
        or messages[-1].get("role") != "user"
        or not isinstance(messages[-1].get("content"), str)
        or not messages[-1]["content"].startswith(prefix)
    ):
        raise ValueError("generation input does not contain its exact supplied context and request")


def _audits(
    audit: SummaryAuditManifest | None, contexts: dict[tuple[str, Arm], ReportingContext]
) -> dict[str, ContextSupport]:
    if audit is None:
        return {}
    audit = SummaryAuditManifest.model_validate(audit.model_dump())
    supports = {support.history_id: support for support in audit.supports}
    expected = {
        history_id: context_digest(context)
        for (history_id, arm), context in contexts.items()
        if arm == Arm.D
    }
    if (
        len(supports) != len(audit.supports)
        or {key: value.context_digest for key, value in supports.items()} != expected
    ):
        raise ValueError("summary audit must bind every available D context exactly once")
    return supports


def load_results(
    root: Path,
    run_dir: Path,
    *,
    analysis_id: str,
    created_at: str,
    summary_audit: SummaryAuditManifest | None = None,
    prose_reviews: tuple[ProseReview, ...] = (),
) -> RunResults:
    """Load finalized frozen phases; keep completed unaudited D reports unscored."""
    prose_reviews = tuple(
        ProseReview.model_validate(review.model_dump(warnings=False)) for review in prose_reviews
    )
    root, run_dir = root.resolve(), run_dir.resolve()
    if not run_dir.is_relative_to(root):
        raise ValueError("run directory must be inside repository")
    origin = FreezeManifest.model_validate_json((run_dir / "freeze.json").read_bytes())
    plan = StudyPlan.model_validate_json(
        _bound(root, (run_dir / "plan.json").relative_to(root).as_posix(), origin)
    )
    if plan != build_plan(
        plan.tasks,
        plan.config,
        run_id=plan.run_id,
        split=plan.split,
        seed=plan.seed,
        repetitions=plan.repetitions,
    ):
        raise ValueError("saved plan differs from deterministic planned matrix")
    collection_freeze = FreezeManifest.model_validate_json(
        (run_dir / "collection-freeze.json").read_bytes()
    )
    report_freeze = FreezeManifest.model_validate_json(
        (run_dir / "report-freeze.json").read_bytes()
    )
    collection = _snapshot(run_dir, collection_freeze)
    reports = _snapshot(run_dir, report_freeze)
    allowed_collection = {
        f"histories/{h.history_id}/{name}"
        for h in plan.histories
        for name in (
            "history.json",
            "truth.json",
            "summary.json",
            "actor-failure.json",
            "summary-failure.json",
            *(f"contexts/{arm}.json" for arm in ARMS),
            *(f"context-{arm}-failure.json" for arm in ARMS[:3]),
        )
    }
    _inventory(
        run_dir,
        (
            "histories/*/history.json",
            "histories/*/truth.json",
            "histories/*/summary.json",
            "histories/*/*-failure.json",
            "histories/*/contexts/*.json",
        ),
        allowed_collection,
        collection,
    )
    names = {f"{p.history_id}-{p.arm}-{p.repetition}" for p in plan.reports}
    allowed_reports = {
        f"{folder}/{name}.json" for folder in ("reports", "report-failures") for name in names
    }
    _inventory(run_dir, ("reports/*.json", "report-failures/*.json"), allowed_reports, reports)
    allowed_generations = {
        f"histories/{p.history_id}/report-{p.arm}-{p.repetition}/attempt-1/generation.json"
        for p in plan.reports
    }
    _inventory(
        run_dir,
        ("histories/*/report-*/attempt-1/generation.json",),
        allowed_generations,
        reports,
    )
    histories, contexts = _collection(plan, collection)
    started = json.loads(reports["report-started.json"])
    if started.get("report_order") != [p.model_dump(mode="json") for p in plan.reports]:
        raise ValueError("report-started order differs from frozen plan")
    origin_audit = (
        SummaryAuditManifest.model_validate_json(reports["summary-audit.json"])
        if "summary-audit.json" in reports
        else None
    )
    if started.get("summary_audit") != (
        origin_audit.model_dump(mode="json") if origin_audit else None
    ):
        raise ValueError("pre-report summary audit differs from retained record")
    _audits(origin_audit, contexts)
    selected_audit = summary_audit if summary_audit is not None else origin_audit
    supports = _audits(selected_audit, contexts)
    reviews = {(r.history_id, r.context_digest, r.report_digest): r for r in prose_reviews}
    if len(reviews) != len(prose_reviews):
        raise ValueError("duplicate prose review binding")
    used_reviews = set()
    cells: list[ReportCell] = []
    blind: list[BlindReviewInput] = []
    failures: list[StageFailure] = []
    by_history = {h.history_id: h for h in plan.histories}
    for planned in plan.reports:
        report_id = f"{planned.history_id}-{planned.arm}-{planned.repetition}"
        history_plan = by_history[planned.history_id]
        key = PairKey(
            task_id=history_plan.task_id,
            history_id=planned.history_id,
            environment=history_plan.environment,
            repetition=planned.repetition,
        )
        raw = reports.get(f"reports/{report_id}.json")
        failed = reports.get(f"report-failures/{report_id}.json")
        if (raw is None) == (failed is None):
            raise ValueError("each planned report requires exactly one frozen outcome")
        if failed is not None:
            failure = _failure(
                failed,
                history_id=planned.history_id,
                stage="report",
                arm=planned.arm,
                repetition=planned.repetition,
            )
            failures.append(failure)
            cells.append(
                ReportCell(
                    report_id=report_id,
                    key=key,
                    arm=planned.arm,
                    generation=None,
                    failure=failure,
                    verdict=None,
                    support_audit_pending=False,
                )
            )
            continue
        assert raw is not None
        if (planned.history_id, planned.arm) not in contexts:
            raise ValueError("completed report has no frozen history/context")
        history, context = (
            histories[planned.history_id],
            contexts[(planned.history_id, planned.arm)],
        )
        generation = GenerationRecord.model_validate_json(raw)
        _generation(generation, planned, history, context, plan, reports)
        digest = hashlib.sha256(generation.text.encode()).hexdigest()
        review_key = (history.history_id, context_digest(context), digest)
        review = reviews.get(review_key)
        if review is not None:
            used_reviews.add(review_key)
        pending = context.arm == Arm.D and history.history_id not in supports
        try:
            parse_report(generation.text)
        except ValueError:
            pending = False
        verdict = (
            None
            if pending
            else score_report(
                generation.text,
                history,
                context,
                support=supports.get(history.history_id) if planned.arm == Arm.D else None,
                prose_review=review,
            )
        )
        cells.append(
            ReportCell(
                report_id=report_id,
                key=key,
                arm=planned.arm,
                generation=generation,
                failure=None,
                verdict=verdict,
                support_audit_pending=pending,
            )
        )
        evidence = tuple(
            ReviewEvidence(event_id=event.event_id, text=event.model_dump_json())
            for event in history.events
        ) + (
            ReviewEvidence(
                event_id="final-state",
                text=json.dumps(
                    {
                        "evaluator_only": True,
                        "final_files": [
                            file.model_dump(mode="json") for file in history.final_files
                        ],
                    }
                ),
            ),
        )
        blind.append(
            BlindReviewInput(
                report_id=report_id,
                history_id=history.history_id,
                task_id=history.task.task_id,
                environment=history.environment,
                arm=context.arm,
                repetition=planned.repetition,
                model_id=plan.config.model_id,
                raw_report=generation.text,
                task_request=_request(history),
                context=context,
                evidence=evidence,
            )
        )
    if used_reviews != set(reviews):
        raise ValueError("prose review does not match a completed planned report")
    _check_phase(
        PhaseResult.model_validate_json(reports["report.json"]), "report", len(blind), failures
    )
    planned_pairs = tuple(dict.fromkeys(cell.key for cell in cells))
    metrics: tuple[Metric, ...] = ("structured_unreliable", "coverage", "unreliable")
    metric_inputs = tuple(
        MetricInput(
            metric=metric,
            observations=tuple(
                Observation(
                    key=cell.key,
                    arm=cell.arm,
                    value=getattr(cell.verdict, metric) if cell.verdict is not None else None,
                )
                for cell in cells
            ),
        )
        for metric in metrics
    )
    effects = tuple(
        Comparison(
            comparison=label,
            metric=values.metric,
            estimate=paired_effect(
                values.observations,
                planned_pairs,
                left=left,
                right=right,
                seed=plan.seed,
                resamples=plan.config.bootstrap_resamples,
            ),
        )
        for label, left, right in COMPARISONS
        for values in metric_inputs
    )
    counts = []
    for arm in ARMS:
        rows = [cell for cell in cells if cell.arm == arm]
        verdicts = [cell.verdict for cell in rows if cell.verdict is not None]
        counts.append(
            ArmCounts(
                arm=arm,
                planned=len(rows),
                completed=sum(row.generation is not None for row in rows),
                technical_missing=sum(row.failure is not None for row in rows),
                scored=len(verdicts),
                support_audit_pending=sum(row.support_audit_pending for row in rows),
                primary_pending=sum(
                    row.generation is not None
                    and (row.verdict is None or row.verdict.unreliable is None)
                    for row in rows
                ),
                review_complete=sum(v.review_complete for v in verdicts),
                review_pending=sum(
                    row.generation is not None
                    and (row.verdict is None or not row.verdict.review_complete)
                    for row in rows
                ),
                invalid_format=sum(v.invalid_format for v in verdicts),
                world_false=sum(bool(v.world_false_fields) for v in verdicts),
                unsupported=sum(bool(v.unsupported_fields) for v in verdicts),
                structured_unreliable=sum(v.structured_unreliable for v in verdicts),
                primary_unreliable=sum(v.unreliable is True for v in verdicts),
                primary_reliable=sum(v.unreliable is False for v in verdicts),
                mean_coverage=sum(v.coverage for v in verdicts) / len(verdicts)
                if verdicts
                else None,
            )
        )
    cases = tuple(
        ReplayCase(
            history=history,
            contexts=tuple(
                contexts[(history_id, arm)] for arm in ARMS if (history_id, arm) in contexts
            ),
            reports=tuple(
                ReportView(
                    arm=cell.arm,
                    repetition=cell.key.repetition,
                    text=cell.generation.text,
                    verdict=cell.verdict,
                )
                for cell in cells
                if cell.key.history_id == history_id and cell.generation is not None
            ),
        )
        for history_id, history in histories.items()
    )
    code_root = Path(__file__).resolve().parent
    return RunResults(
        analysis_id=analysis_id,
        created_at=created_at,
        plan=plan,
        origin_freeze=origin,
        collection_freeze=collection_freeze,
        report_freeze=report_freeze,
        analysis_code=freeze_files(
            code_root, sorted(code_root.rglob("*.py")), created_at=created_at
        ),
        origin_summary_audit=origin_audit,
        summary_audit=selected_audit,
        prose_reviews=prose_reviews,
        planned_pairs=planned_pairs,
        cells=tuple(cells),
        arm_counts=tuple(counts),
        metric_inputs=metric_inputs,
        effects=effects,
        cases=cases,
        blinded=export_blinded_review(blind, seed=plan.seed),
    )


def write_results(results: RunResults, destination: Path) -> None:
    """Create one exclusive analysis bundle; retries require a new analysis ID/path."""
    destination.mkdir(parents=True, exist_ok=False)
    save_json(destination / "results.json", results)
    save_json(
        destination / "analysis-input.json",
        {
            "planned_pairs": [row.model_dump(mode="json") for row in results.planned_pairs],
            "metrics": [row.model_dump(mode="json") for row in results.metric_inputs],
        },
    )
    save_json(destination / "replay.json", [case.model_dump(mode="json") for case in results.cases])
    save_json(
        destination / "blinded-items.json",
        [item.model_dump(mode="json") for item in results.blinded.items],
    )
    save_json(
        destination / "blinded-key.json",
        [item.model_dump(mode="json") for item in results.blinded.key],
    )
    save_json(
        destination / "analysis-freeze.json",
        freeze_files(destination, sorted(destination.iterdir()), created_at=results.created_at),
    )
