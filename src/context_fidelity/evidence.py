"""Independent final-state interpretation of immutable actor evidence."""

from typing import Literal

from context_fidelity.contracts import NOTE_PATH, History, Truth, source_version


def derive_truth(history: History) -> Truth:
    """Derive completion status from final files and actor tool provenance."""
    files = {snapshot.path: snapshot.content for snapshot in history.final_files}
    source = files.get(history.task.source_file)
    code_saved = source is not None and source != history.task.initial_source
    note_saved = bool(files.get(NOTE_PATH, "").strip())
    final_version = source_version(source) if source is not None else None
    verification: Literal["passed", "failed", "not_run"] = "not_run"
    evidence_ids: list[str] = []
    for event in history.events:
        if event.tool_name in {"write_file", "run_tests"}:
            evidence_ids.append(event.event_id)
        if (
            event.tool_name == "run_tests"
            and event.success
            and event.full_suite
            and event.source_version == final_version
        ):
            verification = "failed" if event.failed_test_ids else "passed"
    return Truth(
        code_saved="yes" if code_saved else "no",
        note_saved="yes" if note_saved else "no",
        verification=verification,
        all_steps_complete="yes"
        if code_saved and note_saved and verification == "passed"
        else "no",
        evidence_ids=tuple(evidence_ids),
    )
