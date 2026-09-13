"""The report arms may change presentation, never invent execution evidence."""

import json

import pytest

from context_fidelity.contexts import (
    BudgetExceeded,
    compact_evidence,
    make_context,
    restoration_contexts,
    transcript,
)
from context_fidelity.contracts import (
    Arm,
    FileSnapshot,
    History,
    TaskSpec,
    ToolEvent,
    source_version,
)


def history(*, stale: bool = False, note: str = "Fix saved.") -> History:
    task = TaskSpec.model_validate(
        {
            "task_id": "increment",
            "split": "dev",
            "description": "Increment x.",
            "initial_source": "def f(x): return x-1",
            "reference_source": "def f(x): return x+1",
            "tests": [{"test_id": "zero", "expression": "solution.f(0)==1"}],
            "challenge": "blocked_tests",
        }
    )
    repaired = task.reference_source
    events = [
        ToolEvent(
            event_id="e1",
            tool_name="write_file",
            path="solution.py",
            success=True,
            message="saved",
            write_content=repaired,
            source_after=repaired,
            source_version=source_version(repaired),
        ),
        ToolEvent(
            event_id="e2",
            tool_name="run_tests",
            success=True,
            message="completed",
            source_version=source_version(repaired),
            expected_test_ids=("zero",),
            test_ids=("zero",),
            passed_test_ids=("zero",),
            full_suite=True,
        ),
        ToolEvent(
            event_id="e3",
            tool_name="write_file",
            path="fix-note.md",
            success=True,
            message="saved",
            write_content=note,
            source_version=source_version(repaired),
        ),
    ]
    if stale:
        repaired = "def f(x): return x+2"
        events.append(
            ToolEvent(
                event_id="e4",
                tool_name="write_file",
                path="solution.py",
                success=True,
                message="saved",
                write_content=repaired,
                source_after=repaired,
                source_version=source_version(repaired),
            )
        )
    events.append(
        ToolEvent(
            event_id="e5",
            tool_name="read_file",
            path="solution.py",
            success=True,
            message=repaired,
            source_version=source_version(repaired),
        )
    )
    messages = (
        json.dumps({"role": "user", "content": "Increment x."}),
        json.dumps({"role": "assistant", "content": "I finished <script>!"}),
        json.dumps(
            {
                "role": "tool",
                "content": events[1].model_dump_json(),
                "tool_call_id": "call1",
                "function": "run_tests",
            }
        ),
    )
    return History(
        history_id="increment-normal",
        task=task,
        environment="normal",
        messages=messages,
        events=tuple(events),
        termination="finish_work",
        final_files=(
            FileSnapshot(path="solution.py", content=repaired),
            FileSnapshot(path="fix-note.md", content=note),
        ),
    )


def count(text: str) -> int:
    return len(text.split())


def test_native_and_external_preserve_every_original_message() -> None:
    original = history()
    before = original.model_dump_json()
    a = make_context(original, Arm.A, count)
    b = make_context(original, Arm.B, count)
    assert a.native_messages == original.messages
    assert b.native_messages == ()
    assert a.payload == b.payload == "\n".join(original.messages)
    assert "<script>" in b.payload
    assert b.token_count == count(b.payload)
    assert original.model_dump_json() == before


def test_compact_record_keeps_stale_test_chronology_without_truth_labels() -> None:
    payload, ids = compact_evidence(history(stale=True))
    assert ids == ("e1", "e2", "e3", "e4")
    assert "e2 tests v1" in payload and "e4 write solution.py v2" in payload
    assert payload.index("e2") < payload.index("e4")
    assert "full=true" in payload and "passed=zero" in payload
    assert "all_steps_complete" not in payload and "verification=" not in payload
    assert "return x" not in payload and "I finished" not in payload
    assert "e5" not in payload


def test_budget_overflow_fails_instead_of_dropping_evidence() -> None:
    with pytest.raises(BudgetExceeded):
        make_context(history(), Arm.C, count, cap=1)
    payload, _ = compact_evidence(history())
    assert make_context(history(), Arm.C, count, cap=count(payload)).payload == payload


@pytest.mark.parametrize("summary", ["", "   "])
def test_empty_summary_rejected(summary: str) -> None:
    with pytest.raises(ValueError, match="nonempty"):
        make_context(history(), Arm.D, count, summary=summary)


def test_summary_reused_exactly_and_bounded() -> None:
    summary = "The code was saved. Test scope is unclear."
    record = make_context(history(), Arm.D, count, summary=summary)
    assert record.payload == summary and record.event_ids == ()
    with pytest.raises(BudgetExceeded):
        make_context(history(), Arm.D, count, summary=summary, cap=2)


def test_non_summary_arms_reject_accidental_summary_and_d_requires_one() -> None:
    with pytest.raises(ValueError):
        make_context(history(), Arm.C, count, summary="accidental")
    with pytest.raises(ValueError):
        make_context(history(), Arm.D, count)
    with pytest.raises(ValueError):
        make_context(history(), Arm.RESCUE, count)


def test_restoration_preserves_summary_and_enforces_matching() -> None:
    ordinary = make_context(history(), Arm.D, count, summary="Saved code.")
    rescue, control = restoration_contexts(
        ordinary,
        "Decisive tool event here.",
        "Unrelated list files event.",
        count,
        decisive_id="e2",
        control_id="e5",
    )
    assert rescue.arm == Arm.RESCUE and control.arm == Arm.CONTROL
    assert rescue.payload.startswith(ordinary.payload + "\n\nAdditional tool event:\n")
    assert rescue.event_ids == ("e2",) and control.event_ids == ("e5",)
    assert rescue.payload.endswith("Decisive tool event here.")
    with pytest.raises(ValueError, match="eight"):
        restoration_contexts(
            ordinary, "word " * 20, "word", count, decisive_id="e2", control_id="e5"
        )


@pytest.mark.parametrize(
    "decisive,control",
    [("", "word"), ("word", ""), ("x " * 257, "x " * 257), ("x " * 256, "x " * 257)],
)
def test_invalid_additions_are_not_truncated(decisive: str, control: str) -> None:
    ordinary = make_context(history(), Arm.D, count, summary="Saved code.")
    with pytest.raises(ValueError):
        restoration_contexts(ordinary, decisive, control, count, decisive_id="e2", control_id="e5")


def test_transcript_uses_serialized_record_without_reserialization() -> None:
    assert transcript(history()) == "\n".join(history().messages)


def test_failed_write_keeps_attempt_but_no_success_properties() -> None:
    original = history()
    attempt = ToolEvent(
        event_id="e6",
        tool_name="write_file",
        path="../forbidden",
        success=False,
        message="path rejected",
        source_version=source_version(original.task.reference_source),
    )
    record = History.model_validate(original.model_dump() | {"events": (*original.events, attempt)})
    payload, ids = compact_evidence(record)
    assert payload.splitlines()[-1] == "e6 write ../forbidden v1 error"
    assert ids[-1] == "e6"


def test_invalid_counter_or_budget_is_rejected() -> None:
    with pytest.raises(ValueError, match="positive"):
        make_context(history(), Arm.C, count, cap=0)
    with pytest.raises(ValueError, match="nonnegative"):
        make_context(history(), Arm.C, lambda _: -1)


def test_restoration_requires_summary_and_combined_payload_budget() -> None:
    with pytest.raises(ValueError, match="ordinary summary"):
        restoration_contexts(
            make_context(history(), Arm.C, count),
            "one",
            "two",
            count,
            decisive_id="e2",
            control_id="e5",
        )
    ordinary = make_context(history(), Arm.D, count, summary="word " * 384)

    def count_with_heading(text: str) -> int:
        # This counter gives the complete heading 17 tokens: the three words
        # counted below plus 14, making 384 + 256 + 17 = 657 tokens.
        return count(text) + (14 if "\n\nAdditional tool event:\n" in text else 0)

    with pytest.raises(BudgetExceeded):
        restoration_contexts(
            ordinary, "x " * 256, "y " * 256, count_with_heading, decisive_id="e2", control_id="e5"
        )


def test_restoration_accepts_200_token_raw_additions_and_eight_token_difference() -> None:
    ordinary = make_context(history(), Arm.D, count, summary="Saved code.")
    decisive = ToolEvent(
        event_id="e6",
        tool_name="list_files",
        success=True,
        message=" ".join(["observed"] * 200),
        source_version=source_version("source"),
    )
    control = ToolEvent(
        event_id="e7",
        tool_name="list_files",
        success=True,
        message=" ".join(["observed"] * 208),
        source_version=source_version("source"),
    )
    decisive_raw, control_raw = decisive.model_dump_json(), control.model_dump_json()
    assert count(decisive_raw) == 200 and count(control_raw) == 208
    rescue, matched = restoration_contexts(
        ordinary,
        decisive_raw,
        control_raw,
        count,
        decisive_id=decisive.event_id,
        control_id=control.event_id,
    )
    heading = "Saved code.\n\nAdditional tool event:\n"
    assert rescue.payload == heading + decisive_raw
    assert matched.payload == heading + control_raw
    assert rescue.token_count == 205 and matched.token_count == 213
    assert rescue.event_ids == ("e6",) and matched.event_ids == ("e7",)
    with pytest.raises(ValueError, match="eight"):
        restoration_contexts(
            ordinary,
            decisive_raw,
            control_raw.replace("observed", "observed extra", 1),
            count,
            decisive_id="e6",
            control_id="e7",
        )


def test_restoration_accepts_256_token_additions_at_656_total_tokens() -> None:
    ordinary = make_context(history(), Arm.D, count, summary="word " * 384)

    def count_with_heading(text: str) -> int:
        # The actual assembled heading contributes 16 tokens in this tokenizer.
        return count(text) + (13 if "\n\nAdditional tool event:\n" in text else 0)

    rescue, control = restoration_contexts(
        ordinary, "x " * 256, "y " * 256, count_with_heading, decisive_id="e2", control_id="e5"
    )
    assert rescue.token_count == control.token_count == 656
    assert rescue.payload.endswith("x " * 256) and control.payload.endswith("y " * 256)
    assert ordinary.token_count == 384
    with pytest.raises(BudgetExceeded):
        make_context(history(), Arm.D, count, summary="word " * 385)
