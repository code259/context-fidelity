"""Replay data keeps exact text and enforces report/context provenance."""

from pathlib import Path

import pytest

from context_fidelity.contexts import make_context
from context_fidelity.contracts import Arm, FileSnapshot, History, TaskSpec
from context_fidelity.replay import ReplayCase, ReportView
from context_fidelity.score import Verdict, parse_report

RAW = (
    '{"code_saved":"no","note_saved":"no","verification":"not_run",'
    '"all_steps_complete":"no","summary":"<img src=x onerror=alert(1)>"}'
)


def case() -> ReplayCase:
    task = TaskSpec.model_validate_json(Path("tasks/dev/dev-clamp.json").read_bytes())
    history = History(
        history_id="display-normal",
        task=task,
        environment="normal",
        messages=(),
        events=(),
        final_files=(FileSnapshot(path="solution.py", content=task.initial_source),),
        termination="terminal",
    )
    return ReplayCase(
        history=history,
        contexts=(make_context(history, Arm.C, lambda text: len(text.split())),),
        reports=(ReportView(arm=Arm.C, repetition=0, text=RAW),),
    )


def verdict(raw: str = RAW, *, invalid: bool = False) -> Verdict:
    return Verdict(
        report=None if invalid else parse_report(raw),
        invalid_format=invalid,
        coverage=0 if invalid else 1,
        structured_unreliable=invalid,
        unreliable=True if invalid else None,
        review_complete=False,
        evidence_ids=(),
    )


def test_replay_retains_raw_text_without_rendering_or_claiming_review() -> None:
    record = case()
    restored = ReplayCase.model_validate_json(record.model_dump_json())
    assert restored == record
    assert restored.reports[0].text == RAW
    assert restored.reports[0].verdict is None


def test_duplicate_or_mismatched_lineage_is_rejected() -> None:
    original = case()
    for changes in (
        {"contexts": original.contexts * 2},
        {"reports": original.reports * 2},
        {"contexts": (original.contexts[0].model_copy(update={"history_id": "other"}),)},
        {"reports": (original.reports[0].model_copy(update={"arm": Arm.D}),)},
    ):
        with pytest.raises(ValueError):
            ReplayCase.model_validate(original.model_dump() | changes)


def test_valid_and_invalid_outputs_require_their_corresponding_verdict() -> None:
    assert ReportView(arm=Arm.C, repetition=0, text=RAW, verdict=verdict()).verdict is not None
    bad = ReportView(arm=Arm.C, repetition=0, text="bad", verdict=verdict(invalid=True))
    assert bad.verdict is not None and bad.verdict.invalid_format
    with pytest.raises(ValueError, match="verdict"):
        ReportView(arm=Arm.C, repetition=0, text="bad", verdict=verdict())
    with pytest.raises(ValueError, match="verdict"):
        ReportView(arm=Arm.C, repetition=0, text=RAW, verdict=verdict(invalid=True))
    with pytest.raises(ValueError, match="verdict"):
        ReportView(
            arm=Arm.C,
            repetition=0,
            text=RAW,
            verdict=verdict(RAW.replace('"code_saved":"no"', '"code_saved":"yes"')),
        )
