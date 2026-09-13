"""Package measured analysis, publication figures, and native Inspect scored logs."""

from collections.abc import Callable
from pathlib import Path

from context_fidelity.artifacts import freeze_files, save_json, verify_freeze
from context_fidelity.contracts import Record
from context_fidelity.evidence import derive_truth
from context_fidelity.inspect_export import export_scored_report
from context_fidelity.plots import (
    ArmMetric,
    FactRetention,
    Metric,
    PlotInfo,
    StatusField,
    plot_arm_metrics,
    plot_fact_retention,
    plot_paired_effect,
)
from context_fidelity.results import Metric as ResultMetric
from context_fidelity.results import RunResults, write_results
from context_fidelity.score import visible_support

FIELDS: tuple[StatusField, ...] = ("code_saved", "note_saved", "verification", "all_steps_complete")
PLOT_METRICS: dict[ResultMetric, Metric] = {
    "structured_unreliable": "structured_error",
    "coverage": "factual_coverage",
    "unreliable": "primary_unreliability",
}


class FigureInputs(Record):
    info: PlotInfo
    arms: tuple[ArmMetric, ...]
    retention: tuple[FactRetention, ...]


def figure_inputs(results: RunResults) -> FigureInputs:
    """Compute plot denominators from completed scores and audited evidence."""
    completed = [cell for cell in results.cells if cell.generation is not None]
    reviewed = bool(completed) and all(
        cell.verdict and cell.verdict.review_complete for cell in completed
    )
    resolved = bool(completed) and all(
        cell.verdict and (cell.verdict.review_complete or cell.verdict.invalid_format)
        for cell in completed
    )
    supports = (
        {row.history_id: row for row in results.summary_audit.supports}
        if results.summary_audit
        else {}
    )
    retention = []
    for field in FIELDS:
        planned = audited = retained = 0
        for case in results.cases:
            established = getattr(visible_support(case.history), field)
            if established == "unknown" or established != getattr(
                derive_truth(case.history), field
            ):
                continue
            planned += 1
            audit = supports.get(case.history.history_id)
            if audit is not None:
                audited += 1
                retained += int(getattr(audit, field) == established)
        retention.append(
            FactRetention(field=field, n_planned=planned, n_audited=audited, n_retained=retained)
        )
    return FigureInputs(
        info=PlotInfo(
            run_id=results.plan.run_id,
            phase="development" if results.plan.split == "dev" else "heldout",
            review_status="human-reviewed"
            if reviewed
            else "resolved"
            if resolved
            else "provisional",
        ),
        arms=tuple(
            ArmMetric.model_validate(
                {
                    "arm": row.arm,
                    "n_planned": row.planned,
                    "n_observed": row.scored,
                    "n_errors": row.structured_unreliable,
                    "mean_coverage": row.mean_coverage,
                }
            )
            for row in results.arm_counts
        ),
        retention=tuple(retention),
    )


async def publish_analysis(
    results: RunResults,
    run_dir: Path,
    destination: Path,
    *,
    progress: Callable[[str], None] = print,
) -> None:
    """Write an exclusive package; the final manifest signals complete export.

    An interrupted export remains available for diagnosis. It has no completion
    manifest and must never be presented as a finished package or overwritten.
    """
    verify_freeze(run_dir, results.collection_freeze)
    verify_freeze(run_dir, results.report_freeze)
    write_results(results, destination)
    inputs = figure_inputs(results)
    save_json(destination / "figure-inputs.json", inputs)
    figures = destination / "figures"
    plot_arm_metrics(inputs.arms, figures / "arms", info=inputs.info)
    plot_fact_retention(inputs.retention, figures / "retention", info=inputs.info)
    for comparison in results.effects:
        if comparison.metric == "unreliable" and inputs.info.review_status == "provisional":
            continue
        plot_paired_effect(
            comparison.estimate,
            figures / f"{comparison.comparison}-{comparison.metric}",
            info=inputs.info,
            metric=PLOT_METRICS[comparison.metric],
        )
    cases = {case.history.history_id: case for case in results.cases}
    exports: list[dict[str, object]] = []
    for cell in results.cells:
        if cell.generation is None or cell.verdict is None:
            exports.append(
                {
                    "report_id": cell.report_id,
                    "status": "technical_missing"
                    if cell.generation is None
                    else "support_audit_pending",
                }
            )
            continue
        case = cases[cell.key.history_id]
        context = next(item for item in case.contexts if item.arm == cell.arm)
        raw = (
            run_dir
            / "histories"
            / cell.key.history_id
            / f"report-{cell.arm}-{cell.key.repetition}/attempt-1"
            / Path(cell.generation.log_path).name
        )
        progress(f"export Inspect {cell.report_id}")
        export = await export_scored_report(
            case.history,
            context,
            cell.generation,
            cell.verdict,
            run_id=results.plan.run_id,
            raw_log=raw,
            destination=destination / "inspect" / f"{cell.report_id}.eval",
        )
        exports.append(
            {
                "report_id": cell.report_id,
                "status": "exported",
                "provenance": export.model_dump(mode="json"),
            }
        )
    save_json(destination / "inspect-index.json", exports)
    # Recheck after asynchronous exports so changed inputs cannot receive a
    # completion marker merely because they matched when packaging began.
    verify_freeze(run_dir, results.collection_freeze)
    verify_freeze(run_dir, results.report_freeze)
    save_json(
        destination / "package-freeze.json",
        freeze_files(
            destination,
            sorted(path for path in destination.rglob("*") if path.is_file()),
            created_at=results.created_at,
        ),
    )
