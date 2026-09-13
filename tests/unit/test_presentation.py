"""Measured values, explicit review state, and immutable presentation exports."""

import asyncio
import json
from pathlib import Path

import pytest
from inspect_ai.model import ModelOutput
from test_results import STAMP, audits, fixture, omit_context, refresh_report_freeze

from context_fidelity import presentation
from context_fidelity.artifacts import FreezeManifest, verify_freeze
from context_fidelity.contracts import Arm
from context_fidelity.inspect_export import InspectExport
from context_fidelity.results import load_results
from context_fidelity.score import ContextSupport, ProseReview


def replace_reports(run: Path, text: str, *, all_reports: bool = False) -> None:
    paths = sorted((run / "reports").glob("*.json"))
    for path in paths if all_reports else paths[:1]:
        data = json.loads(path.read_text())
        data["text"] = text
        data["output"] = ModelOutput.from_content(
            json.loads(data["output"])["model"], text
        ).model_dump_json()
        path.write_text(json.dumps(data))
        retained = (
            run
            / "histories"
            / data["history_id"]
            / f"report-{data['arm']}-{data['repetition']}/attempt-1/generation.json"
        )
        retained.write_text(json.dumps(data))
    refresh_report_freeze(run)


def reviewed_results(root: Path, run: Path):
    initial = load_results(root, run, analysis_id="view", created_at=STAMP)
    reviews = tuple(
        ProseReview(
            history_id=key.history_id,
            context_digest=key.context_digest,
            report_digest=key.report_digest,
            false_claim=False,
            unsupported_claim=False,
            reviewer_id="human-reviewer",
            reviewer_kind="human",
            blinded=True,
            unblinding_note="",
        )
        for key in initial.blinded.key
    )
    return load_results(
        root,
        run,
        analysis_id="view",
        created_at=STAMP,
        summary_audit=audits(initial),
        prose_reviews=reviews,
    )


@pytest.mark.parametrize("invalid", [False, True])
def test_completed_human_reviews_allow_primary_with_technical_missingness(
    tmp_path: Path, invalid: bool
) -> None:
    root, run = fixture(tmp_path, failure=True)
    if invalid:
        replace_reports(run, "invalid required JSON")
    results = reviewed_results(root, run)
    inputs = presentation.figure_inputs(results)
    assert inputs.info.review_status == ("resolved" if invalid else "human-reviewed")
    assert sum(row.n_planned for row in inputs.arms) == 32
    assert sum(row.n_observed for row in inputs.arms) == 31
    assert sum(row.technical_missing for row in results.arm_counts) == 1
    assert sum(cell.verdict.review_complete for cell in results.cells if cell.verdict) == (
        30 if invalid else 31
    )
    assert (
        sum(
            effect.estimate.missing_left + effect.estimate.missing_right
            for effect in results.effects
            if effect.metric == "unreliable"
        )
        == 1
    )


def test_all_invalid_outputs_resolve_primary_without_claiming_human_review(tmp_path: Path) -> None:
    root, run = fixture(tmp_path)
    replace_reports(run, "invalid required JSON", all_reports=True)
    results = load_results(root, run, analysis_id="view", created_at=STAMP)
    inputs = presentation.figure_inputs(results)
    assert inputs.info.review_status == "resolved"
    assert "required verdicts resolved" in inputs.info.caption("Primary results")
    assert "human review complete" not in inputs.info.caption("Primary results")
    assert all(cell.verdict and cell.verdict.unreliable for cell in results.cells)
    assert all(cell.verdict and not cell.verdict.review_complete for cell in results.cells)


def test_known_valid_errors_with_unreviewed_prose_remain_provisional(tmp_path: Path) -> None:
    root, run = fixture(tmp_path)
    replace_reports(
        run,
        json.dumps(
            {
                "code_saved": "yes",
                "note_saved": "yes",
                "verification": "passed",
                "all_steps_complete": "yes",
                "summary": "All work completed.",
            }
        ),
        all_reports=True,
    )
    initial = load_results(root, run, analysis_id="view", created_at=STAMP)
    results = load_results(
        root,
        run,
        analysis_id="view",
        created_at=STAMP,
        summary_audit=audits(initial),
    )
    assert all(cell.verdict and cell.verdict.unreliable for cell in results.cells)
    assert presentation.figure_inputs(results).info.review_status == "provisional"


def test_retention_counts_histories_and_keeps_unaudited_summaries_missing(tmp_path: Path) -> None:
    root, run = fixture(tmp_path)
    omit_context(run, Arm.D)
    initial = load_results(root, run, analysis_id="view", created_at=STAMP)
    audit = audits(initial, kind="assistant")
    first, *others = audit.supports
    # Unknown does not establish a fact; a false opposite is not retention either.
    audit = audit.model_copy(
        update={
            "supports": (
                ContextSupport.model_validate(
                    first.model_dump() | {"code_saved": "unknown", "verification": "passed"}
                ),
                *others,
            )
        }
    )
    results = load_results(
        root,
        run,
        analysis_id="view",
        created_at=STAMP,
        summary_audit=audit,
    )
    inputs = presentation.figure_inputs(results)
    assert inputs.info.review_status == "provisional"
    assert [(row.n_planned, row.n_audited, row.n_retained) for row in inputs.retention] == [
        (8, 7, 6),
        (8, 7, 7),
        (8, 7, 6),
        (8, 7, 7),
    ]


@pytest.mark.parametrize("phase", ["collection", "report"])
def test_changed_frozen_input_is_rejected_before_creating_package(
    tmp_path: Path, phase: str
) -> None:
    root, run = fixture(tmp_path)
    results = load_results(root, run, analysis_id="view", created_at=STAMP)
    path = run / ("collect.json" if phase == "collection" else "report.json")
    path.write_text(path.read_text() + "\n")
    destination = tmp_path / "analysis"
    with pytest.raises(ValueError, match="frozen file changed"):
        asyncio.run(presentation.publish_analysis(results, run, destination))
    assert not destination.exists()


def test_inputs_preserve_pending_support_and_planned_denominators(tmp_path: Path) -> None:
    root, run = fixture(tmp_path)
    results = load_results(root, run, analysis_id="view", created_at=STAMP)
    inputs = presentation.figure_inputs(results)
    assert inputs.info.review_status == "provisional"
    assert inputs.info.phase == "development"
    assert [row.n_planned for row in inputs.arms] == [8] * 4
    assert [row.n_observed for row in inputs.arms] == [8, 8, 8, 0]
    assert inputs.arms[-1].mean_coverage is None
    assert [(row.n_planned, row.n_audited) for row in inputs.retention] == [(8, 0)] * 4


def export_record(history, context, generation, **kwargs):
    path = kwargs["destination"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"native scored log boundary")
    return InspectExport(
        source_log=str(kwargs["raw_log"]),
        source_sha256="0" * 64,
        scored_log=str(path),
        run_id=kwargs["run_id"],
        history_id=history.history_id,
        arm=context.arm,
        repetition=generation.repetition,
        review_status="pending",
    )


@pytest.mark.parametrize("resolved", [False, True])
def test_export_uses_native_logs_and_freezes_only_a_complete_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resolved: bool,
) -> None:
    root, run = fixture(tmp_path, failure=resolved)
    if resolved:
        replace_reports(run, "invalid required JSON")
        results = reviewed_results(root, run)
    else:
        results = load_results(root, run, analysis_id="view", created_at=STAMP)
    calls = []

    async def export(history, context, generation, verdict, **kwargs):
        calls.append((history.history_id, context.arm, generation.repetition))
        return export_record(history, context, generation, **kwargs)

    monkeypatch.setattr(presentation, "export_scored_report", export)
    destination = tmp_path / "analysis"
    asyncio.run(presentation.publish_analysis(results, run, destination))
    assert len(calls) == (31 if resolved else 24)
    assert (destination / "figures/arms.png").read_bytes().startswith(b"\x89PNG")
    assert (destination / "figures/retention.pdf").read_bytes().startswith(b"%PDF")
    manifest = FreezeManifest.model_validate_json((destination / "package-freeze.json").read_text())
    verify_freeze(destination, manifest)
    assert {entry.path for entry in manifest.files} == {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file() and path.name != "package-freeze.json"
    }
    index = json.loads((destination / "inspect-index.json").read_text())
    assert len(index) == 32
    statuses = [entry["status"] for entry in index]
    assert statuses.count("technical_missing") == (1 if resolved else 0)
    assert statuses.count("support_audit_pending") == (0 if resolved else 8)
    assert (destination / "figures/C-D-unreliable.pdf").exists() == resolved
    assert (destination / "figures/B-A-unreliable.png").exists() == resolved
    verify_freeze(run, results.collection_freeze)
    verify_freeze(run, results.report_freeze)
    original = (destination / "results.json").read_bytes()
    with pytest.raises(FileExistsError):
        asyncio.run(presentation.publish_analysis(results, run, destination))
    assert (destination / "results.json").read_bytes() == original


def test_input_changed_during_export_prevents_complete_package_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, run = fixture(tmp_path)
    results = load_results(root, run, analysis_id="view", created_at=STAMP)
    # This test isolates the export/freeze boundary; real figure files are tested above.
    for name in ("plot_arm_metrics", "plot_fact_retention", "plot_paired_effect"):
        monkeypatch.setattr(presentation, name, lambda *args, **kwargs: None)

    async def export(history, context, generation, verdict, **kwargs):
        path = run / "report.json"
        path.write_text(path.read_text() + "\n")
        return export_record(history, context, generation, **kwargs)

    monkeypatch.setattr(presentation, "export_scored_report", export)
    destination = tmp_path / "analysis"
    with pytest.raises(ValueError, match="frozen file changed"):
        asyncio.run(
            presentation.publish_analysis(results, run, destination, progress=lambda _: None)
        )
    assert (destination / "results.json").exists()
    assert not (destination / "package-freeze.json").exists()


def test_failed_native_export_has_no_complete_package_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, run = fixture(tmp_path)
    results = load_results(root, run, analysis_id="view", created_at=STAMP)

    async def fail(*args, **kwargs):
        raise ValueError("raw evidence mismatch")

    monkeypatch.setattr(presentation, "export_scored_report", fail)
    destination = tmp_path / "analysis"
    with pytest.raises(ValueError, match="raw evidence mismatch"):
        asyncio.run(presentation.publish_analysis(results, run, destination))
    assert not (destination / "package-freeze.json").exists()
    assert (destination / "results.json").exists()
