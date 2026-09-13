"""Portable replay rendering must preserve evidence and treat model text as data."""

import json
from pathlib import Path

import pytest

from context_fidelity.contexts import make_context
from context_fidelity.contracts import (
    Arm,
    FileSnapshot,
    History,
    TaskSpec,
    ToolEvent,
    source_version,
)
from context_fidelity.score import Verdict, parse_report
from context_fidelity.viewer import ReplayCase, ReportView, render_viewer


def case() -> ReplayCase:
    task = TaskSpec.model_validate_json(Path("tasks/dev/dev-clamp.json").read_bytes())
    history = History(
        history_id="display-normal",
        task=task,
        environment="normal",
        messages=(json.dumps({"role": "user", "content": "<script>alert(1)</script>"}),),
        events=(),
        final_files=(FileSnapshot(path="solution.py", content=task.initial_source),),
        termination="terminal",
    )
    context = make_context(history, Arm.C, lambda text: len(text.split()))
    return ReplayCase(
        history=history,
        contexts=(context,),
        reports=(
            ReportView(
                arm=Arm.C,
                repetition=0,
                text='{"code_saved":"no","note_saved":"no","verification":"not_run",'
                '"all_steps_complete":"no","summary":"<img src=x onerror=alert(1)>"}',
            ),
        ),
    )


def test_replay_is_self_contained_and_escapes_untrusted_content(tmp_path: Path) -> None:
    target = tmp_path / "index.html"
    render_viewer((case(),), target, phase="development", study_note="Actual development records.")
    html = target.read_text()
    assert "Context Fidelity" in html and "display-normal" in html
    assert "Human prose review pending" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "<img src=x onerror=" not in html
    assert "<script>alert(1)</script>" not in html
    assert 'src="http' not in html and 'href="http' not in html
    assert 'type="module"' not in html
    assert "No tool events" in html and "Context unavailable" in html
    assert html.count('class="comparison-slot"') == 2
    with pytest.raises(FileExistsError):
        render_viewer((case(),), target, phase="development")


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


@pytest.mark.parametrize("reviewed,error", [(False, True), (True, False), (True, True)])
def test_review_labels_are_explicit(tmp_path: Path, reviewed: bool, error: bool) -> None:
    original = case()
    report = original.reports[0]
    verdict = Verdict(
        report=parse_report(report.text),
        invalid_format=False,
        coverage=1,
        structured_unreliable=error,
        unreliable=error if reviewed else None,
        review_complete=reviewed,
        evidence_ids=(),
    )
    shown = ReplayCase(
        history=original.history,
        contexts=original.contexts,
        reports=(report.model_copy(update={"verdict": verdict}),),
    )
    path = tmp_path / "index.html"
    render_viewer((shown,), path, phase="heldout")
    html = path.read_text()
    expected = (
        "unreliable report"
        if reviewed and error
        else "supported report"
        if reviewed
        else "Structured discrepancy detected"
    )
    assert expected in html


def test_invalid_output_and_missing_report_are_visible(tmp_path: Path) -> None:
    original = case()
    b = make_context(original.history, Arm.B, len)
    shown = ReplayCase(
        history=original.history,
        contexts=(*original.contexts, b),
        reports=(ReportView(arm=Arm.C, repetition=0, text="<script>invalid</script>"),),
    )
    path = tmp_path / "index.html"
    render_viewer((shown,), path, phase="development")
    assert "Invalid required JSON" in path.read_text()
    assert "Reporting has not run" in path.read_text()


def test_local_figure_and_exact_event_are_rendered(tmp_path: Path) -> None:
    original = case()
    event = ToolEvent(
        event_id="event-1",
        tool_name="list_files",
        success=False,
        message="<b>tool error</b>",
        source_version=source_version(original.history.task.initial_source),
    )
    history = History.model_validate(original.history.model_dump() | {"events": (event,)})
    shown = ReplayCase(history=history, contexts=original.contexts, reports=original.reports)
    with pytest.raises(ValueError, match="missing"):
        render_viewer(
            (shown,), tmp_path / "index.html", phase="development", figures=("measured.png",)
        )
    (tmp_path / "figures").mkdir()
    (tmp_path / "figures/measured.png").write_bytes(b"test asset; never a model result")
    render_viewer((shown,), tmp_path / "index.html", phase="development", figures=("measured.png",))
    html = (tmp_path / "index.html").read_text()
    assert 'src="figures/measured.png"' in html
    assert "event-1" in html and "&lt;b&gt;tool error&lt;/b&gt;" in html


def test_viewer_rejects_a_verdict_for_different_raw_output() -> None:
    original = case().reports[0]
    verdict = Verdict(
        report=parse_report(original.text),
        invalid_format=False,
        coverage=1,
        structured_unreliable=False,
        unreliable=False,
        review_complete=True,
        evidence_ids=(),
    )
    with pytest.raises(ValueError, match="verdict"):
        ReportView(arm=Arm.C, repetition=0, text="different output", verdict=verdict)


def test_empty_viewer_and_unsafe_figure_name_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        render_viewer((), tmp_path / "empty.html", phase="heldout")
    with pytest.raises(ValueError):
        render_viewer(
            (case(),), tmp_path / "escape.html", phase="heldout", figures=("../evil.png",)
        )
