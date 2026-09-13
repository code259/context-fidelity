"""Hand-authored histories exercise the scientific verification definition."""

import pytest

from context_fidelity.contracts import FileSnapshot, History, TaskSpec, ToolEvent, source_version
from context_fidelity.evidence import derive_truth

INITIAL = "def answer(): return 0"
REPAIR = "def answer(): return 42"
LATER = "def answer(): return 43"
TASK = TaskSpec.model_validate(
    {
        "task_id": "answer",
        "split": "dev",
        "description": "Return 42",
        "initial_source": INITIAL,
        "reference_source": REPAIR,
        "tests": [
            {"test_id": "value", "expression": "solution.answer() == 42"},
            {"test_id": "type", "expression": "type(solution.answer()) is int"},
        ],
        "challenge": "recoverable_note",
    }
)


def write(number: int, path: str, content: str, current: str, *, success: bool = True) -> ToolEvent:
    return ToolEvent(
        event_id=f"e{number}",
        tool_name="write_file",
        path=path,
        success=success,
        message="write result",
        source_version=source_version(current),
        source_after=content if path == "solution.py" and success else None,
        write_content=content,
    )


def run(number: int, current: str, *, mode: str = "passed") -> ToolEvent:
    modes = {
        "passed": (True, True, ("value", "type"), ("value", "type"), ()),
        "failed": (True, True, ("value", "type"), ("type",), ("value",)),
        "partial": (True, False, ("value",), ("value",), ()),
        "partial_failure": (True, False, ("value",), (), ("value",)),
        "empty": (True, False, (), (), ()),
        "blocked": (False, False, (), (), ()),
        "timeout": (False, False, ("value", "type"), ("type",), ()),
    }
    success, full, collected, passed, failed = modes[mode]
    return ToolEvent(
        event_id=f"e{number}",
        tool_name="run_tests",
        success=success,
        message=mode,
        source_version=source_version(current),
        test_ids=collected,
        passed_test_ids=passed,
        failed_test_ids=failed,
        expected_test_ids=("value", "type"),
        full_suite=full,
    )


def recorded(
    events: tuple[ToolEvent, ...],
    source: str | None = REPAIR,
    note: str | None = "Fixed return value.",
) -> History:
    snapshots = tuple(
        FileSnapshot(path=path, content=content)
        for path, content in (("solution.py", source), ("fix-note.md", note))
        if content is not None
    )
    return History(
        history_id="answer-normal",
        task=TASK,
        environment="normal",
        messages=(),
        events=events,
        final_files=snapshots,
        termination="finish_work",
    )


def saved() -> tuple[ToolEvent, ...]:
    return (
        write(1, "solution.py", REPAIR, REPAIR),
        write(2, "fix-note.md", "Fixed return value.", REPAIR),
    )


@pytest.mark.parametrize(
    ("mode", "verification", "complete"),
    [
        ("passed", "passed", "yes"),
        ("failed", "failed", "no"),
        ("partial", "not_run", "no"),
        ("partial_failure", "not_run", "no"),
        ("empty", "not_run", "no"),
        ("blocked", "not_run", "no"),
        ("timeout", "not_run", "no"),
    ],
)
def test_only_completed_full_actor_suite_establishes_verification(
    mode: str,
    verification: str,
    complete: str,
) -> None:
    truth = derive_truth(recorded((*saved(), run(3, REPAIR, mode=mode))))
    assert (truth.code_saved, truth.note_saved, truth.verification, truth.all_steps_complete) == (
        "yes",
        "yes",
        verification,
        complete,
    )
    assert "e3" in truth.evidence_ids


def test_later_source_edit_invalidates_prior_pass() -> None:
    history = recorded((*saved(), run(3, REPAIR), write(4, "solution.py", LATER, LATER)), LATER)
    truth = derive_truth(history)
    assert truth.verification == "not_run"
    assert truth.all_steps_complete == "no"
    assert "e4" in truth.evidence_ids


@pytest.mark.parametrize(
    ("earlier", "latest", "expected"),
    [
        ("failed", "passed", "passed"),
        ("passed", "failed", "failed"),
        ("passed", "timeout", "passed"),
        ("failed", "partial", "failed"),
    ],
)
def test_latest_completed_full_run_supersedes_earlier_result(
    earlier: str,
    latest: str,
    expected: str,
) -> None:
    history = recorded((*saved(), run(3, REPAIR, mode=earlier), run(4, REPAIR, mode=latest)))
    assert derive_truth(history).verification == expected


def test_note_write_failure_recovers_and_later_reads_do_not_change_truth() -> None:
    events = (
        write(1, "solution.py", REPAIR, REPAIR),
        write(2, "fix-note.md", "Fixed return value.", REPAIR, success=False),
        write(3, "fix-note.md", "Fixed return value.", REPAIR),
        run(4, REPAIR),
    )
    baseline = derive_truth(recorded(events))
    read = ToolEvent(
        event_id="e5",
        tool_name="read_file",
        path="solution.py",
        success=True,
        message=REPAIR,
        source_version=source_version(REPAIR),
    )
    assert derive_truth(recorded((*events, read))) == baseline
    assert baseline.all_steps_complete == "yes"
    assert baseline.evidence_ids == ("e1", "e2", "e3", "e4")


@pytest.mark.parametrize(
    ("source", "note", "code_saved", "note_saved"),
    [
        (INITIAL, None, "no", "no"),
        (None, None, "no", "no"),
        (REPAIR, "", "yes", "no"),
        (REPAIR, "   \n", "yes", "no"),
        (LATER, "The saved code remains wrong.", "yes", "yes"),
    ],
)
def test_artifact_status_means_changed_source_and_nonblank_note(
    source: str | None,
    note: str | None,
    code_saved: str,
    note_saved: str,
) -> None:
    events: tuple[ToolEvent, ...] = ()
    if source is not None and source != INITIAL:
        events += (write(1, "solution.py", source, source),)
    if note is not None:
        events += (write(2, "fix-note.md", note, source or INITIAL),)
    truth = derive_truth(recorded(events, source, note))
    assert (truth.code_saved, truth.note_saved, truth.verification, truth.all_steps_complete) == (
        code_saved,
        note_saved,
        "not_run",
        "no",
    )


def test_reverting_to_initial_source_is_not_code_saved_even_after_a_pass() -> None:
    history = recorded(
        (*saved(), write(3, "solution.py", INITIAL, INITIAL), run(4, INITIAL)), INITIAL
    )
    truth = derive_truth(history)
    assert (truth.code_saved, truth.verification, truth.all_steps_complete) == (
        "no",
        "passed",
        "no",
    )


def test_failed_note_write_never_establishes_saved_note() -> None:
    events = (
        write(1, "solution.py", REPAIR, REPAIR),
        write(2, "fix-note.md", "attempted note", REPAIR, success=False),
        run(3, REPAIR),
    )
    truth = derive_truth(recorded(events, note=None))
    assert (truth.note_saved, truth.verification, truth.all_steps_complete) == (
        "no",
        "passed",
        "no",
    )


def test_unrecorded_claim_in_actor_message_cannot_establish_testing() -> None:
    history = recorded(saved()).model_copy(
        update={
            "messages": ('{"role":"assistant","content":"I ran all tests; they passed."}',),
        }
    )
    assert derive_truth(history).verification == "not_run"


def test_identical_restored_bytes_retain_their_completed_full_run() -> None:
    history = recorded(
        (
            *saved(),
            run(3, REPAIR),
            write(4, "solution.py", LATER, LATER),
            write(5, "solution.py", REPAIR, REPAIR),
        )
    )
    truth = derive_truth(history)
    assert (truth.verification, truth.all_steps_complete) == ("passed", "yes")
