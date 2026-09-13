"""Portable, versioned contracts for actor-visible evidence and final state.

``success`` means a tool completed, including a test run with failed assertions.
``source_version`` is the source hash after an event. Only the recorder constructs
these records; model tool arguments cannot supply authoritative provenance.
"""

import hashlib
import json
import re
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    field_validator,
    model_validator,
)

Identifier = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")]
SourceName = Annotated[StrictStr, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]*\.py$")]
Digest = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
ToolName = Literal["list_files", "read_file", "write_file", "run_tests", "finish_work"]
NOTE_PATH = "fix-note.md"


class Environment(StrEnum):
    NORMAL = "normal"
    BLOCKED_TESTS = "blocked_tests"
    PARTIAL_TESTS = "partial_tests"
    RECOVERABLE_NOTE = "recoverable_note"


class Arm(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    RESCUE = "rescue"
    CONTROL = "control"


class YesNoUnknown(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class Verification(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"
    UNKNOWN = "unknown"


class Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value


def source_version(content: str) -> str:
    """Return the SHA256 digest of exact UTF-8 source bytes."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class TestCase(Record):
    test_id: Identifier
    expression: Annotated[StrictStr, Field(min_length=1)]


class TaskSpec(Record):
    task_id: Identifier
    split: Literal["dev", "heldout"]
    description: StrictStr
    source_file: SourceName = "solution.py"
    initial_source: StrictStr
    reference_source: StrictStr
    tests: Annotated[tuple[TestCase, ...], Field(min_length=1)]
    challenge: Environment

    @model_validator(mode="after")
    def validate_task(self) -> Self:
        if len({test.test_id for test in self.tests}) != len(self.tests):
            raise ValueError("test IDs must be unique")
        if self.initial_source == self.reference_source:
            raise ValueError("initial and reference source must differ")
        return self


class FileSnapshot(Record):
    path: StrictStr
    content: StrictStr


class ToolEvent(Record):
    event_id: Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_-]*[0-9]$")]
    tool_name: ToolName
    path: StrictStr | None = None
    success: StrictBool
    message: StrictStr
    source_version: Digest
    source_after: StrictStr | None = None
    test_ids: tuple[Identifier, ...] = ()
    passed_test_ids: tuple[Identifier, ...] = ()
    failed_test_ids: tuple[Identifier, ...] = ()
    expected_test_ids: tuple[Identifier, ...] = ()
    full_suite: StrictBool = False
    write_content: StrictStr | None = None

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        scopes = (self.test_ids, self.passed_test_ids, self.failed_test_ids, self.expected_test_ids)
        if any(len(ids) != len(set(ids)) for ids in scopes):
            raise ValueError("test scope IDs must be unique")
        collected, passed, failed, expected = map(set, scopes)
        if passed & failed or not (passed | failed) <= collected:
            raise ValueError("test outcomes must be disjoint and collected")
        if self.tool_name != "run_tests" and (any(scopes) or self.full_suite):
            raise ValueError("only run_tests may contain test evidence")
        if not collected <= expected:
            raise ValueError("collected tests must belong to the supplied suite")
        completed_full_suite = (
            self.success
            and bool(expected)
            and collected == expected
            and passed | failed == expected
        )
        if self.full_suite != completed_full_suite:
            raise ValueError("full_suite must agree with completed outcomes for the supplied suite")
        if self.tool_name == "write_file":
            if self.path is None or (self.success and self.write_content is None):
                raise ValueError("successful writes require path and exact content")
            if not self.success and self.source_after is not None:
                raise ValueError("failed writes cannot update source")
        elif self.write_content is not None or self.source_after is not None:
            raise ValueError("only writes may contain write contents")
        return self


class History(Record):
    history_id: Identifier
    task: TaskSpec
    environment: Environment
    messages: tuple[StrictStr, ...]
    events: tuple[ToolEvent, ...]
    final_files: tuple[FileSnapshot, ...]
    termination: Literal["finish_work", "terminal", "tool_limit"]

    @model_validator(mode="after")
    def validate_history(self) -> Self:
        for message in self.messages:
            payload = json.loads(message)
            if not isinstance(payload, dict) or not isinstance(payload.get("role"), str):
                raise ValueError("messages must encode objects with string roles")
        paths = [snapshot.path for snapshot in self.final_files]
        if len(set(paths)) != len(paths):
            raise ValueError("final file paths must be unique")
        allowed_paths = {self.task.source_file, NOTE_PATH}
        if not set(paths) <= allowed_paths:
            raise ValueError("unexpected final file path")
        state = {self.task.source_file: self.task.initial_source}
        last_number = -1
        expected_tests = {test.test_id for test in self.task.tests}
        for event in self.events:
            suffix = re.search(r"[0-9]+$", event.event_id)
            assert suffix is not None  # Guaranteed by the event contract.
            number = int(suffix.group())
            if number <= last_number:
                raise ValueError("event IDs must increase monotonically")
            last_number = number
            if event.tool_name == "write_file":
                if event.success:
                    if event.path not in allowed_paths:
                        raise ValueError("write path is outside task artifacts")
                    assert event.path is not None and event.write_content is not None
                    if event.path == self.task.source_file:
                        if event.source_after != event.write_content:
                            raise ValueError("source_after must match successful source write")
                    elif event.source_after is not None:
                        raise ValueError("note writes cannot update source")
                    state[event.path] = event.write_content
            if event.source_version != source_version(state[self.task.source_file]):
                raise ValueError("event source version does not match recorded state")
            if event.tool_name == "run_tests":
                if set(event.expected_test_ids) != expected_tests:
                    raise ValueError("test evidence must identify the supplied suite")
        actual = {snapshot.path: snapshot.content for snapshot in self.final_files}
        # Missing initial source can represent an interrupted/failed setup. An
        # observed artifact may never contradict the append-only write record.
        for path, content in actual.items():
            if state.get(path) != content:
                raise ValueError("final file does not match recorded writes")
        written_paths = {
            event.path for event in self.events if event.tool_name == "write_file" and event.success
        }
        if not written_paths <= actual.keys():
            raise ValueError("successful write is missing from final files")
        return self


class CompletionReport(Record):
    code_saved: YesNoUnknown
    note_saved: YesNoUnknown
    verification: Verification
    all_steps_complete: YesNoUnknown
    summary: StrictStr


class Truth(Record):
    code_saved: Literal["yes", "no"]
    note_saved: Literal["yes", "no"]
    verification: Literal["passed", "failed", "not_run"]
    all_steps_complete: Literal["yes", "no"]
    evidence_ids: tuple[Identifier, ...]
