"""Boundary examples independently specified from the experiment protocol."""

import json

import pytest
from pydantic import ValidationError

from context_fidelity.contracts import (
    CompletionReport,
    FileSnapshot,
    History,
    TaskSpec,
    ToolEvent,
    source_version,
)
from context_fidelity.contracts import (
    TestCase as Case,
)


def task(**changes: object) -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "task_id": "repair-1",
            "split": "dev",
            "description": "Repair increment",
            "initial_source": "def increment(x): return x",
            "reference_source": "def increment(x): return x + 1",
            "tests": [
                {"test_id": "zero", "expression": "solution.increment(0) == 1"},
                {"test_id": "negative", "expression": "solution.increment(-1) == 0"},
            ],
            "challenge": "partial_tests",
            **changes,
        }
    )


def event(**changes: object) -> ToolEvent:
    return ToolEvent.model_validate(
        {
            "event_id": "e0001",
            "tool_name": "read_file",
            "path": "solution.py",
            "success": True,
            "message": "read",
            "source_version": source_version(task().initial_source),
            **changes,
        }
    )


def history(**changes: object) -> History:
    return History.model_validate(
        {
            "history_id": "history-1",
            "task": task(),
            "environment": "normal",
            "messages": (),
            "events": (),
            "final_files": (FileSnapshot(path="solution.py", content=task().initial_source),),
            "termination": "finish_work",
            **changes,
        }
    )


def test_hash_is_standard_utf8_sha256() -> None:
    assert (
        source_version("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    assert source_version("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_portable_transcript_and_tuple_records_round_trip() -> None:
    messages = (
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "working"}],
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": "write_file",
                    "arguments": {"path": "solution.py", "content": "a\nb"},
                }
            ],
        },
        {"role": "tool", "content": "done", "tool_call_id": "call_1"},
    )
    serialized = tuple(json.dumps(message, ensure_ascii=False) for message in messages)
    original = history(messages=serialized, events=(event(),))
    restored = History.model_validate_json(original.model_dump_json())
    assert restored == original
    assert restored.messages == serialized
    assert tuple(json.loads(message) for message in restored.messages) == messages
    assert isinstance(restored.task.tests, tuple)
    with pytest.raises(ValidationError, match="frozen"):
        restored.history_id = "changed"


@pytest.mark.parametrize(
    "changes",
    [
        {"source_file": "../escape.py"},
        {"source_file": "/tmp/a.py"},
        {"source_file": "nested/a.py"},
        {"source_file": "a.py/"},
        {"source_file": "a.txt"},
        {"source_file": "é.py"},
        {"task_id": "with space"},
        {"task_id": "é"},
        {"task_id": ""},
        {"tests": []},
        {"tests": [Case(test_id="x", expression="True")] * 2},
        {"reference_source": "def increment(x): return x"},
        {"description": 4},
        {"surprise": 1},
        {"schema_version": 2},
        {"schema_version": True},
        {"schema_version": 1.0},
    ],
)
def test_task_rejects_unsafe_or_ambiguous_contract(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        task(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"source_version": "model-says-current"},
        {"event_id": "event"},
        {"event_id": "../1"},
        {"event_id": "event 1"},
        {"event_id": "é1"},
        {"success": "true"},
        {"tool_name": "shell"},
        {"test_ids": ("zero",)},
        {"full_suite": True},
        {"tool_name": "run_tests", "test_ids": ("x", "x")},
        {"tool_name": "run_tests", "test_ids": ("x",), "passed_test_ids": ("y",)},
        {
            "tool_name": "run_tests",
            "test_ids": ("x",),
            "passed_test_ids": ("x",),
            "failed_test_ids": ("x",),
        },
        {
            "tool_name": "run_tests",
            "full_suite": True,
            "test_ids": ("x",),
            "passed_test_ids": ("x",),
            "expected_test_ids": ("x", "y"),
        },
        {
            "tool_name": "run_tests",
            "full_suite": True,
            "test_ids": ("x",),
            "expected_test_ids": ("x",),
        },
        {"tool_name": "write_file", "write_content": None},
        {"write_content": "unexpected"},
        {"source_after": "unexpected"},
        {"tool_name": "write_file", "success": False, "source_after": "new"},
    ],
)
def test_events_reject_false_scope_and_malformed_provenance(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        event(**changes)


@pytest.mark.parametrize("failed", [False, True])
def test_completed_entire_suite_cannot_be_marked_partial(failed: bool) -> None:
    with pytest.raises(ValidationError, match="full_suite"):
        event(
            tool_name="run_tests",
            test_ids=("zero", "negative"),
            expected_test_ids=("zero", "negative"),
            full_suite=False,
            passed_test_ids=("negative",) if failed else ("zero", "negative"),
            failed_test_ids=("zero",) if failed else (),
        )


def test_standalone_event_rejects_collected_tests_outside_declared_suite() -> None:
    with pytest.raises(ValidationError, match="supplied suite"):
        event(
            tool_name="run_tests",
            test_ids=("outsider",),
            passed_test_ids=("outsider",),
            expected_test_ids=("zero", "negative"),
        )


def test_failed_path_traversal_write_round_trips_without_changing_source() -> None:
    rejected = event(
        tool_name="write_file",
        path="../outside.py",
        success=False,
        write_content="attempted escape",
        message="path rejected",
    )
    original = history(events=(rejected,))
    restored = History.model_validate_json(original.model_dump_json())
    assert restored.events[0].path == "../outside.py"
    assert restored.events[0].success is False
    assert restored.final_files == (
        FileSnapshot(path="solution.py", content=task().initial_source),
    )


def test_successful_path_traversal_write_is_incoherent() -> None:
    with pytest.raises(ValidationError, match="outside task artifacts"):
        history(
            events=(
                event(
                    tool_name="write_file", path="../outside.py", write_content="attempted escape"
                ),
            )
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"events": (event(), event())},
        {"events": (event(event_id="e2"), event(event_id="e1"))},
        {"events": (event(source_version="a" * 64),)},
        {"final_files": (FileSnapshot(path="solution.py", content="unrecorded change"),)},
        {"final_files": (FileSnapshot(path="solution.py", content=task().initial_source),) * 2},
        {"final_files": (FileSnapshot(path="unexpected.py", content="x"),)},
        {"final_files": (FileSnapshot(path="fix-note.md", content="unrecorded note"),)},
        {
            "events": (
                event(
                    tool_name="write_file",
                    write_content="new",
                    source_after="new",
                    source_version=source_version("new"),
                ),
            )
        },
        {"events": (event(tool_name="write_file", path="fix-note.md", write_content="note"),)},
        {"events": (event(tool_name="write_file", write_content="new", source_after="different"),)},
        {"events": (event(tool_name="write_file", path="other.py", write_content="new"),)},
        {"events": (event(tool_name="run_tests", expected_test_ids=("invented",)),)},
        {
            "events": (
                event(
                    tool_name="write_file",
                    path="fix-note.md",
                    write_content="note",
                    source_after="new",
                ),
            )
        },
        {"messages": ("not JSON",)},
        {"messages": ("[]",)},
        {"messages": ('{"content": "no role"}',)},
    ],
)
def test_history_rejects_incoherent_recordings(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        history(**changes)


def test_reports_accept_unknown_but_reject_unsupported_schema_values() -> None:
    payload = {
        "code_saved": "unknown",
        "note_saved": "yes",
        "verification": "not_run",
        "all_steps_complete": "no",
        "summary": "Only the note is established.",
    }
    report = CompletionReport.model_validate(payload)
    assert CompletionReport.model_validate_json(report.model_dump_json()) == report
    for changes in ({"code_saved": True}, {"verification": "success"}, {"extra": "x"}):
        with pytest.raises(ValidationError):
            CompletionReport.model_validate(payload | changes)
