"""Offline scored copies for native Inspect View; raw generation logs stay immutable."""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from inspect_ai import score_async
from inspect_ai.log import EvalLog, read_eval_log, write_eval_log_async
from inspect_ai.model import ChatMessage, ModelOutput, get_model
from inspect_ai.scorer import Score, Scorer, Target, mean, scorer
from inspect_ai.solver import TaskState
from inspect_ai.viewer import TaskSamplesColumn, TaskSamplesView, ViewerConfig
from pydantic import TypeAdapter

from context_fidelity.contexts import ReportingContext, compact_evidence, transcript
from context_fidelity.contracts import Arm, Digest, History, Identifier, Record, source_version
from context_fidelity.experiment import GenerationRecord, PhaseLineage, report_messages
from context_fidelity.score import Verdict, context_digest, parse_report

MESSAGE: TypeAdapter[ChatMessage] = TypeAdapter(ChatMessage)


class InspectExport(Record):
    source_log: str
    source_sha256: Digest
    scored_log: str
    run_id: Identifier
    history_id: Identifier
    arm: Arm
    repetition: int
    review_status: Literal["pending", "complete", "known_error"]


def _verdict(state: TaskState) -> Verdict:
    return Verdict.model_validate(state.metadata["context_fidelity_verdict"])


def _score(state: TaskState, value: str | int | float, explanation: str) -> Score:
    verdict = _verdict(state)
    return Score(
        value=value,
        answer=state.output.completion,
        explanation=explanation,
        metadata={
            "evidence_ids": list(verdict.evidence_ids),
            "events": state.metadata["context_fidelity_evidence"],
            "review_complete": verdict.review_complete,
            "verdict": verdict.model_dump(mode="json"),
            "derivation": state.metadata["context_fidelity_derivation"],
        },
    )


@scorer(metrics=[])
def primary_unreliability() -> Scorer:
    """Primary outcome: pending is a visible label, never a numeric zero."""

    async def score(state: TaskState, target: Target) -> Score:
        verdict = _verdict(state)
        if verdict.unreliable is None:
            return _score(
                state, "pending", "Primary verdict pending required prose/context review."
            )
        return _score(
            state,
            int(verdict.unreliable),
            "Primary unreliable-report verdict; see evidence and review provenance.",
        )

    return score


@scorer(metrics=[mean()])
def structured_unreliability() -> Scorer:
    """Structured errors remain distinct from the primary prose-aware outcome."""

    async def score(state: TaskState, target: Target) -> Score:
        verdict = _verdict(state)
        explanation = json.dumps(
            {
                "invalid_format": verdict.invalid_format,
                "world_false_fields": verdict.world_false_fields,
                "unsupported_fields": verdict.unsupported_fields,
                "unsupported_success_fields": verdict.unsupported_success_fields,
                "false_denial_fields": verdict.false_denial_fields,
            },
            sort_keys=True,
        )
        return _score(state, int(verdict.structured_unreliable), explanation)

    return score


@scorer(metrics=[mean()])
def factual_coverage() -> Scorer:
    """Fraction of four status fields that are correct, supported, and non-unknown."""

    async def score(state: TaskState, target: Target) -> Score:
        return _score(
            state,
            _verdict(state).coverage,
            "Correct, supported, non-unknown status fields divided by four.",
        )

    return score


def _semantic(message: ChatMessage) -> dict[str, object]:
    # Inspect adds message IDs/source labels at execution. All content, roles,
    # tool calls, errors, and other fields must still agree with the supplied context.
    return message.model_dump(mode="json", exclude={"id", "source"})


def _validate(
    log: EvalLog,
    history: History,
    context: ReportingContext,
    generation: GenerationRecord,
    verdict: Verdict,
    lineage: PhaseLineage,
) -> None:
    if (
        generation.stage != "report"
        or generation.history_id != history.history_id
        or context.history_id != history.history_id
        or generation.arm != context.arm
        or generation.repetition is None
        or generation.run_id not in {None, lineage.run_id}
    ):
        raise ValueError("generation/context lineage does not match")
    if log.status != "success" or not log.samples or len(log.samples) != 1:
        raise ValueError("raw log must contain one successful report sample")
    sample = log.samples[0]
    parsed = ModelOutput.model_validate_json(generation.output)
    usage = parsed.usage.model_dump_json() if parsed.usage else None
    if (
        log.plan.config.model_dump_json() != generation.config
        or log.plan.config.seed != generation.seed
        or usage != generation.usage
    ):
        raise ValueError("raw configuration/usage does not match generation record")
    definite_structured_error = (
        verdict.invalid_format
        or bool(verdict.world_false_fields)
        or (
            bool(verdict.unsupported_fields)
            and (context.arm in (Arm.A, Arm.B, Arm.C) or verdict.review_complete)
        )
    )
    if (
        (verdict.unreliable is False and not verdict.review_complete)
        or (verdict.unreliable is None and verdict.review_complete)
        or (definite_structured_error and verdict.unreliable is not True)
        or (verdict.structured_unreliable and verdict.unreliable is False)
    ):
        raise ValueError("primary verdict is inconsistent with pending review or structured errors")
    if (
        sample.error
        or sample.output.model_dump_json() != generation.output
        or parsed.completion != generation.text
        or parsed.stop_reason != generation.stop_reason
        or tuple(m.model_dump_json() for m in sample.messages[:-1]) != generation.input_messages
        or len(sample.messages) != len(generation.input_messages) + 1
        or sample.messages[-1] != parsed.message
    ):
        raise ValueError("raw output/input messages do not match generation record")
    supplied = [MESSAGE.validate_json(message) for message in generation.input_messages]
    expected = report_messages(history, context)
    if [_semantic(m) for m in supplied] != [_semantic(m) for m in expected]:
        raise ValueError("report input does not match supplied context provenance")
    if context.arm == Arm.B and context.payload != transcript(history):
        raise ValueError("full context does not match history")
    if context.arm == Arm.C and (context.payload, context.event_ids) != compact_evidence(history):
        raise ValueError("compact context does not match history")
    ids = {event.event_id for event in history.events}
    if not set(verdict.evidence_ids) <= ids or not set(context.event_ids) <= ids:
        raise ValueError("evidence provenance does not belong to history")
    try:
        report = parse_report(generation.text)
    except ValueError:
        if not verdict.invalid_format or verdict.report is not None:
            raise ValueError("invalid output does not match verdict") from None
    else:
        if verdict.invalid_format or verdict.report != report:
            raise ValueError("parsed output does not match verdict")
    metadata = lineage.model_dump(mode="json", exclude={"schema_version"})
    for existing in (log.eval.metadata or {}, sample.metadata):
        if any(
            existing.get(key) is not None and existing[key] != value
            for key, value in metadata.items()
        ):
            raise ValueError("raw metadata lineage does not match supplied records")


async def export_scored_report(
    history: History,
    context: ReportingContext,
    generation: GenerationRecord,
    verdict: Verdict,
    *,
    run_id: str,
    raw_log: Path,
    destination: Path,
) -> InspectExport:
    """Validate a report's provenance and atomically publish a separate scored .eval.

    No execution or model generation occurs. The original model/header, messages,
    outputs, and events stay in the derived log; scoring adds native score events.
    Legacy raw logs may lack lineage metadata, which is explicitly disclosed.
    """
    source = raw_log.resolve()
    target = destination.resolve()
    if source == target:
        raise ValueError("scored export cannot replace its raw log")
    if target.suffix != ".eval":
        raise ValueError("native scored destination must use .eval")
    if target.exists():
        raise FileExistsError(target)
    history = History.model_validate(history.model_dump())
    context = ReportingContext.model_validate(context.model_dump())
    generation = GenerationRecord.model_validate(generation.model_dump())
    verdict = Verdict.model_validate(verdict.model_dump())
    lineage = PhaseLineage(
        run_id=run_id,
        history_id=history.history_id,
        task_id=history.task.task_id,
        environment=history.environment,
        stage="report",
        arm=context.arm,
        repetition=generation.repetition,
    )
    raw_bytes = source.read_bytes()
    raw = read_eval_log(source)
    _validate(raw, history, context, generation, verdict, lineage)
    assert raw.samples is not None and generation.repetition is not None
    sample = raw.samples[0]
    fingerprint = hashlib.sha256(raw_bytes).hexdigest()
    status: Literal["pending", "complete", "known_error"] = (
        "pending"
        if verdict.unreliable is None
        else "complete"
        if verdict.review_complete
        else "known_error"
    )
    derivation = {
        "source_log": str(source),
        "source_sha256": fingerprint,
        "operation": "offline score_async; original generation preserved",
        "original_metadata_absent": not raw.eval.metadata and not sample.metadata,
        "context_digest": context_digest(context),
        "history_digest": source_version(history.model_dump_json()),
        "generation_digest": source_version(generation.model_dump_json()),
        "verdict_digest": source_version(verdict.model_dump_json()),
    }
    metadata = lineage.model_dump(mode="json", exclude={"schema_version"}) | {
        "review_status": status,
        "context_fidelity_derivation": derivation,
        "context_fidelity_verdict": verdict.model_dump(mode="json"),
        "context_fidelity_evidence": [
            event.model_dump(mode="json")
            for event in history.events
            if event.event_id in verdict.evidence_ids
        ],
    }
    raw.eval.metadata = (raw.eval.metadata or {}) | metadata
    sample.metadata = sample.metadata | metadata
    raw.eval.tags = list(
        dict.fromkeys(
            [
                *(raw.eval.tags or []),
                f"run:{run_id}",
                f"task:{history.task.task_id}",
                f"environment:{history.environment}",
                f"arm:{context.arm}",
                f"repetition:{generation.repetition}",
                f"review:{status}",
            ]
        )
    )
    raw.eval.viewer = ViewerConfig(
        task_samples_view=TaskSamplesView(
            name="Reporting evidence",
            columns=[
                TaskSamplesColumn(id="sampleId"),
                TaskSamplesColumn.score("primary_unreliability"),
                TaskSamplesColumn.score("structured_unreliability"),
                TaskSamplesColumn.score("factual_coverage"),
                TaskSamplesColumn(id="answer"),
                TaskSamplesColumn(id="input", visible=False),
                TaskSamplesColumn(id="target", visible=False),
            ],
            score_labels={
                "primary_unreliability": "Primary error",
                "structured_unreliability": "Status error",
                "factual_coverage": "Factual coverage",
            },
            score_color_scales={
                "primary_unreliability": {"pending": "warn", "0": "good", "1": "bad"},
                "structured_unreliability": "good-low",
                "factual_coverage": "good-high",
            },
            color_scales_enabled=True,
        )
    )
    # A mock scoring context prevents even credential/provider initialization from
    # the raw header. These pure scorers never generate; an accidental call fails.
    scored = await score_async(
        raw,
        [primary_unreliability(), structured_unreliability(), factual_coverage()],
        model=get_model("mockllm/model", custom_outputs=[]),
        action="append",
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".inspect-score-") as directory:
        staged = Path(directory) / "scored.eval"
        await write_eval_log_async(scored, staged)
        checked = read_eval_log(staged)
        original_event_ids = {event.uuid for event in sample.events}
        if (
            checked.eval.model != raw.eval.model
            or checked.eval.model_args != raw.eval.model_args
            or checked.plan.config != raw.plan.config
            or not checked.samples
            or not scored.samples
            or checked.samples[0].messages != sample.messages
            or checked.samples[0].output != sample.output
            or checked.samples[0].scores != scored.samples[0].scores
            or [event for event in checked.samples[0].events if event.uuid in original_event_ids]
            != sample.events
        ):
            raise ValueError("scored log failed writer readback validation")
        if source.read_bytes() != raw_bytes:
            raise ValueError("raw log changed during scoring")
        os.link(staged, target)
    return InspectExport(
        source_log=str(source),
        source_sha256=fingerprint,
        scored_log=str(target),
        run_id=run_id,
        history_id=history.history_id,
        arm=context.arm,
        repetition=generation.repetition,
        review_status=status,
    )
