"""Native scored copies must bind to immutable raw traces and never invoke a model."""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from inspect_ai.log import read_eval_log, write_eval_log
from inspect_ai.model import Model, get_model
from test_experiment import CountingTokenizer, output, run_actor

from context_fidelity.contexts import make_context
from context_fidelity.contracts import Arm
from context_fidelity.experiment import report_context
from context_fidelity.inspect_export import export_scored_report
from context_fidelity.score import ContextSupport, context_digest, score_report


@pytest.fixture(autouse=True)
def runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path.parent / "inspect-data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path.parent / "inspect-cache"))
    monkeypatch.setenv("INSPECT_DISPLAY", "none")


def fixture(tmp_path: Path, raw: str | None = None, arm: Arm = Arm.B):
    history, _ = run_actor(tmp_path / "actor", [output("Stopped")])
    summary_arm = arm in (Arm.D, Arm.RESCUE, Arm.CONTROL)
    context = make_context(
        history,
        Arm.D if summary_arm else arm,
        CountingTokenizer().count,
        summary="Work status is unavailable." if summary_arm else None,
    ).model_copy(update={"arm": arm})
    text = raw or json.dumps(
        {
            "code_saved": "no",
            "note_saved": "no",
            "verification": "not_run",
            "all_steps_complete": "no",
            "summary": "No work saved.",
        }
    )
    generation = asyncio.run(
        report_context(
            history,
            context,
            repetition=0,
            model=get_model("mockllm/model", custom_outputs=[output(text)]),
            tokenizer=CountingTokenizer(),
            log_dir=tmp_path / "report",
            seed=19,
        )
    )
    audit = (
        ContextSupport(
            history_id=history.history_id,
            context_digest=context_digest(context),
            code_saved="unknown",
            note_saved="unknown",
            verification="unknown",
            all_steps_complete="unknown",
            reviewer_id="assistant-test",
            reviewer_kind="assistant",
            notes="No specific status established.",
        )
        if summary_arm
        else None
    )
    return history, context, generation, score_report(text, history, context, support=audit)


def export(case, destination: Path):
    history, context, generation, verdict = case
    return asyncio.run(
        export_scored_report(
            history,
            context,
            generation,
            verdict,
            run_id="pilot",
            raw_log=Path(generation.log_path),
            destination=destination,
        )
    )


def test_native_copy_preserves_raw_header_trace_and_pending_primary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = fixture(tmp_path)
    raw_path = Path(case[2].log_path)
    original_bytes = raw_path.read_bytes()
    original = read_eval_log(raw_path)

    async def no_model(*args, **kwargs):
        raise AssertionError("export attempted model generation")

    monkeypatch.setattr(Model, "generate", no_model)
    result = export(case, tmp_path / "scored" / "report.eval")
    assert raw_path.read_bytes() == original_bytes
    assert result.source_sha256 == hashlib.sha256(original_bytes).hexdigest()
    scored = read_eval_log(result.scored_log)
    assert scored.eval.model == original.eval.model
    assert scored.eval.model_args == original.eval.model_args
    assert scored.plan.config == original.plan.config
    assert scored.samples and original.samples
    sample = scored.samples[0]
    assert sample.messages == original.samples[0].messages
    assert sample.output == original.samples[0].output
    original_ids = {event.uuid for event in original.samples[0].events}
    assert [event for event in sample.events if event.uuid in original_ids] == original.samples[
        0
    ].events
    assert sample.metadata["arm"] == "B" and sample.metadata["run_id"] == "pilot"
    assert "arm:B" in scored.eval.tags
    assert "environment:normal" in scored.eval.tags
    assert (
        scored.eval.viewer.task_samples_view.score_labels["primary_unreliability"]
        == "Primary error"
    )
    assert sample.scores["primary_unreliability"].value == "pending"
    assert sample.scores["structured_unreliability"].value == 0
    assert sample.scores["factual_coverage"].value == 1
    assert sample.scores["primary_unreliability"].answer == case[2].text
    assert "pending" in sample.scores["primary_unreliability"].explanation.lower()
    assert sample.metadata["context_fidelity_derivation"]["source_sha256"] == result.source_sha256
    assert sample.scores["factual_coverage"].metadata["evidence_ids"] == list(case[3].evidence_ids)
    with pytest.raises(FileExistsError):
        export(case, Path(result.scored_log))


@pytest.mark.parametrize("arm", [Arm.D, Arm.RESCUE, Arm.CONTROL])
def test_assistant_unsupported_summary_claims_remain_pending(tmp_path: Path, arm: Arm) -> None:
    case = fixture(tmp_path, arm=arm)
    assert case[3].structured_unreliable is True
    assert case[3].unreliable is None
    result = export(case, tmp_path / "pending-summary.eval")
    sample = read_eval_log(result.scored_log).samples[0]
    assert sample.scores["primary_unreliability"].value == "pending"
    assert sample.scores["structured_unreliability"].value == 1
    assert sample.metadata["review_status"] == "pending"


def test_definite_structured_error_cannot_be_marked_pending(tmp_path: Path) -> None:
    history, context, generation, verdict = fixture(tmp_path, "not JSON")
    verdict = verdict.model_copy(update={"unreliable": None})
    with pytest.raises(ValueError, match="primary verdict"):
        export((history, context, generation, verdict), tmp_path / "invalid.eval")


def test_legacy_metadata_is_disclosed_and_invalid_output_is_a_known_error(tmp_path: Path) -> None:
    case = fixture(tmp_path, "not JSON")
    raw = read_eval_log(case[2].log_path)
    raw.eval.metadata = {}
    raw.samples[0].metadata = {}
    write_eval_log(raw, case[2].log_path)
    result = export(case, tmp_path / "derived.eval")
    sample = read_eval_log(result.scored_log).samples[0]
    assert sample.metadata["context_fidelity_derivation"]["original_metadata_absent"] is True
    assert sample.scores["primary_unreliability"].value == 1
    assert sample.scores["structured_unreliability"].value == 1
    assert sample.scores["factual_coverage"].value == 0


@pytest.mark.parametrize(
    "change", ["text", "messages", "context", "verdict", "arm", "run", "config", "usage", "pending"]
)
def test_export_rejects_mismatched_provenance(tmp_path: Path, change: str) -> None:
    history, context, generation, verdict = fixture(tmp_path)
    if change == "text":
        generation = generation.model_copy(update={"text": "changed"})
    if change == "messages":
        generation = generation.model_copy(update={"input_messages": ()})
    if change == "context":
        context = context.model_copy(update={"payload": "invented history"})
    if change == "verdict":
        verdict = verdict.model_copy(update={"report": None})
    if change == "arm":
        generation = generation.model_copy(update={"arm": Arm.C})
    if change == "run":
        generation = generation.model_copy(update={"run_id": "other-run"})
    if change == "config":
        generation = generation.model_copy(update={"config": "{}"})
    if change == "usage":
        generation = generation.model_copy(update={"usage": None})
    if change == "pending":
        verdict = verdict.model_copy(update={"unreliable": False})
    with pytest.raises(ValueError, match="(match|provenance|lineage|context|output|verdict)"):
        export((history, context, generation, verdict), tmp_path / "derived.eval")
    assert not (tmp_path / "derived.eval").exists()


def test_export_cannot_replace_raw_and_failed_write_leaves_no_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = fixture(tmp_path)
    raw_path = Path(case[2].log_path)
    before = raw_path.read_bytes()
    with pytest.raises(ValueError, match="raw"):
        export(case, raw_path)

    async def fail_write(log, location, **kwargs):
        Path(location).write_bytes(b"partial")
        raise OSError("synthetic write failure")

    monkeypatch.setattr("context_fidelity.inspect_export.write_eval_log_async", fail_write)
    with pytest.raises(OSError, match="write failure"):
        export(case, tmp_path / "derived.eval")
    assert not (tmp_path / "derived.eval").exists()
    assert raw_path.read_bytes() == before
    assert not list(tmp_path.glob(".inspect-score-*"))


@pytest.mark.parametrize("arm", [Arm.A, Arm.C])
def test_native_and_compact_copy_keep_settled_primary_visible(tmp_path: Path, arm: Arm) -> None:
    history, context, generation, verdict = fixture(tmp_path, arm=arm)
    settled = verdict.model_copy(update={"unreliable": False, "review_complete": True})
    result = export((history, context, generation, settled), tmp_path / "derived.eval")
    assert result.review_status == "complete"
    assert read_eval_log(result.scored_log).samples[0].scores["primary_unreliability"].value == 0


@pytest.mark.parametrize(
    "change", ["raw_status", "raw_metadata", "evidence", "invalid_verdict", "suffix"]
)
def test_export_rejects_unusable_or_misattributed_evidence(tmp_path: Path, change: str) -> None:
    case = fixture(tmp_path, "bad JSON" if change == "invalid_verdict" else None)
    history, context, generation, verdict = case
    raw = read_eval_log(generation.log_path)
    if change == "raw_status":
        raw.status = "error"
    if change == "raw_metadata":
        raw.samples[0].metadata["arm"] = "C"
    if change in {"raw_status", "raw_metadata"}:
        write_eval_log(raw, generation.log_path)
    if change == "evidence":
        verdict = verdict.model_copy(update={"evidence_ids": ("e999",)})
    if change == "invalid_verdict":
        verdict = verdict.model_copy(update={"invalid_format": False})
    target = tmp_path / ("derived.json" if change == "suffix" else "derived.eval")
    with pytest.raises(ValueError):
        export((history, context, generation, verdict), target)
    assert not target.exists()


@pytest.mark.parametrize("change", ["readback", "source"])
def test_export_checks_written_copy_and_source_integrity_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    import context_fidelity.inspect_export as module

    case = fixture(tmp_path)
    original_writer = module.write_eval_log_async

    async def changed_write(log, location, **kwargs):
        if change == "readback":
            log.eval.model = "tampered-model"
        await original_writer(log, location, **kwargs)
        if change == "source":
            with Path(case[2].log_path).open("ab") as stream:
                stream.write(b"external mutation")

    monkeypatch.setattr(module, "write_eval_log_async", changed_write)
    with pytest.raises(ValueError, match="readback|raw log changed"):
        export(case, tmp_path / "derived.eval")
    assert not (tmp_path / "derived.eval").exists()
