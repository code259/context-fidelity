"""Numeric research figures; callers provide measured, provenance-linked data.

Arm bars are descriptive observed-report rates, not independent-sample estimates.
Only ``plot_paired_effect`` shows uncertainty, using the task-cluster calculation
already performed by ``analyze.paired_effect``. No missing value becomes zero.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Self

from matplotlib import rc_context
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.typing import RcKeyType
from pydantic import Field, StrictInt, model_validator

from context_fidelity.analyze import PairedEstimate
from context_fidelity.contracts import Arm, Identifier, Record

Count = Annotated[StrictInt, Field(ge=0)]
PositiveCount = Annotated[StrictInt, Field(gt=0)]
Rate = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
StatusField = Literal["code_saved", "note_saved", "verification", "all_steps_complete"]
Metric = Literal["structured_error", "factual_coverage", "primary_unreliability"]

_ARMS = (Arm.A, Arm.B, Arm.C, Arm.D)
_ARM_LABELS = ("A\nNative", "B\nFull record", "C\nEvidence", "D\nSummary")
_COLORS = ("#56B4E9", "#0072B2", "#009E73", "#E69F00")  # Okabe–Ito
_FIELDS = ("code_saved", "note_saved", "verification", "all_steps_complete")
_STYLE: dict[RcKeyType, object] = {
    "font.family": "DejaVu Sans",
    "font.size": 11,
    "axes.titlesize": 12,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "pdf.fonttype": 42,
    "text.usetex": False,
}


class PlotInfo(Record):
    run_id: Identifier
    phase: Literal["development", "heldout"]
    review_status: Literal["provisional", "human-reviewed"]

    def caption(self, subject: str) -> str:
        review = (
            "provisional; human review pending"
            if self.review_status == "provisional"
            else "human review complete"
        )
        phase = "Development" if self.phase == "development" else "Held-out"
        return f"{subject} — {review}\n{phase} · {self.run_id}"


class ArmMetric(Record):
    arm: Literal[Arm.A, Arm.B, Arm.C, Arm.D]
    n_planned: PositiveCount
    n_observed: Count
    n_errors: Count
    mean_coverage: Rate | None

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if not self.n_errors <= self.n_observed <= self.n_planned:
            raise ValueError("errors <= observed <= planned is required")
        if (self.n_observed == 0) != (self.mean_coverage is None):
            raise ValueError("coverage must be unavailable exactly when no reports were observed")
        return self

    @property
    def error_rate(self) -> float | None:
        return self.n_errors / self.n_observed if self.n_observed else None


class FactRetention(Record):
    """Retention of a correct, decisive status fact in the ordinary summary.

    ``n_planned`` counts histories with that fact established by the full visible
    record. ``n_audited`` excludes unavailable or unaudited summaries. Omission,
    ambiguity, or a fabricated opposite claim is not retention.
    """

    field: StatusField
    n_planned: Count
    n_audited: Count
    n_retained: Count

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if not self.n_retained <= self.n_audited <= self.n_planned:
            raise ValueError("retained <= audited <= planned is required")
        return self

    @property
    def retention_rate(self) -> float | None:
        return self.n_retained / self.n_audited if self.n_audited else None


@dataclass(frozen=True)
class PlotFiles:
    png: Path
    pdf: Path


def _export(figure: Figure, destination: Path) -> PlotFiles:
    """Export an extensionless destination without overwriting prior results."""
    files = PlotFiles(png=Path(f"{destination}.png"), pdf=Path(f"{destination}.pdf"))
    try:
        for path in (files.png, files.pdf):
            if path.exists():
                raise FileExistsError(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        for path, fmt in ((files.png, "png"), (files.pdf, "pdf")):
            with path.open("xb") as stream:
                figure.savefig(stream, format=fmt, dpi=300, bbox_inches="tight")
    finally:
        figure.clear()
    return files


def _rate_bar(axis: Axes, index: int, rate: float | None, color: str, label: str) -> None:
    if rate is None:
        axis.text(index, 5, "unavailable", ha="center", va="bottom", fontsize=9, rotation=90)
    else:
        axis.bar(index, 100 * rate, color=color, width=0.6)
        axis.text(index, 100 * rate + 2, f"{100 * rate:.1f}%", ha="center", fontsize=10)
    axis.text(
        index,
        -0.32,
        label,
        transform=axis.get_xaxis_transform(),
        ha="center",
        va="top",
        fontsize=9,
    )


def _rate_axis(axis: Axes, labels: tuple[str, ...], ylabel: str) -> None:
    axis.set_ylim(0, 112)
    axis.set_yticks((0, 25, 50, 75, 100))
    axis.set_xticks(range(len(labels)), labels)
    axis.set_xlim(-0.6, len(labels) - 0.4)
    axis.set_ylabel(ylabel)
    axis.grid(axis="y", alpha=0.18)
    axis.set_axisbelow(True)


def plot_arm_metrics(
    rows: tuple[ArmMetric, ...], destination: Path, *, info: PlotInfo
) -> PlotFiles:
    """Plot structured errors and factual coverage with observed/planned counts."""
    if sorted(row.arm for row in rows) != list(_ARMS):
        raise ValueError("each main arm must appear exactly once")
    ordered = sorted(rows, key=lambda row: row.arm)
    with rc_context(_STYLE):
        figure = Figure(figsize=(9.6, 4.6))
        errors, coverage = figure.subplots(1, 2)
        figure.suptitle(info.caption("Structured status results"), fontsize=13)
        for index, row in enumerate(ordered):
            denominator = f"observed {row.n_observed}/{row.n_planned}\nerrors {row.n_errors}"
            _rate_bar(errors, index, row.error_rate, _COLORS[index], denominator)
            _rate_bar(
                coverage,
                index,
                row.mean_coverage,
                _COLORS[index],
                f"observed {row.n_observed}/{row.n_planned}",
            )
        _rate_axis(errors, _ARM_LABELS, "Reports with structured error (%)")
        _rate_axis(coverage, _ARM_LABELS, "Correct, supported status fields (%)")
        figure.text(
            0.5,
            0.025,
            "Observed-report summaries; missing reports excluded, not scored as zero. "
            "Prose errors are not included.",
            ha="center",
            fontsize=9,
        )
        figure.subplots_adjust(left=0.08, right=0.98, top=0.75, bottom=0.34, wspace=0.32)
        return _export(figure, destination)


def plot_fact_retention(
    rows: tuple[FactRetention, ...], destination: Path, *, info: PlotInfo
) -> PlotFiles:
    """Plot ordinary-summary retention among audited, eligible histories."""
    if sorted(row.field for row in rows) != sorted(_FIELDS):
        raise ValueError("each of the four status fields must appear exactly once")
    ordered = sorted(rows, key=lambda row: _FIELDS.index(row.field))
    with rc_context(_STYLE):
        figure = Figure(figsize=(8.8, 4.8))
        axis = figure.subplots()
        figure.suptitle(info.caption("Ordinary-summary fact retention"), fontsize=13)
        for index, row in enumerate(ordered):
            _rate_bar(
                axis,
                index,
                row.retention_rate,
                _COLORS[index],
                f"retained {row.n_retained}/{row.n_audited}\n"
                f"audited {row.n_audited}/{row.n_planned} eligible",
            )
        _rate_axis(
            axis,
            ("Code saved", "Note saved", "Verification", "All steps complete"),
            "Correct decisive facts retained (%)",
        )
        figure.text(
            0.5,
            0.025,
            "Denominator: audited summaries with a status established in the full visible record.",
            ha="center",
            fontsize=9,
        )
        figure.subplots_adjust(left=0.10, right=0.98, top=0.75, bottom=0.30)
        return _export(figure, destination)


def plot_paired_effect(
    estimate: PairedEstimate,
    destination: Path,
    *,
    info: PlotInfo,
    metric: Metric = "structured_error",
) -> PlotFiles:
    """Show task differences, the paired interval, and missing-data bounds."""
    if metric == "primary_unreliability" and info.review_status != "human-reviewed":
        raise ValueError("primary unreliability requires completed human review")
    labels = {
        "structured_error": "Structured error rate",
        "factual_coverage": "Factual coverage",
        "primary_unreliability": "Primary unreliable-report rate",
    }
    subject = labels[metric]
    contrast = f"{estimate.left.value} minus {estimate.right.value}"
    with rc_context(_STYLE):
        figure = Figure(figsize=(8.8, max(4.8, 3.4 + 0.26 * len(estimate.cluster_differences))))
        axis = figure.subplots()
        figure.suptitle(info.caption(f"{subject}: {contrast}"), fontsize=13)
        names: list[str] = []
        for index, (task, difference) in enumerate(estimate.cluster_differences):
            axis.scatter(100 * difference, index, color="#0072B2", marker="o", s=28)
            names.append(task)
        index = len(names)
        if estimate.effect is not None:
            # ``paired_effect`` supplies all three jointly or leaves all three absent.
            if estimate.ci_low is None or estimate.ci_high is None:
                raise ValueError("paired effect requires its interval")
            axis.plot(
                (100 * estimate.ci_low, 100 * estimate.ci_high),
                (index, index),
                color="#009E73",
                linewidth=3,
            )
            axis.scatter(100 * estimate.effect, index, color="#009E73", marker="D", s=45)
            names.append("Paired mean · 95% CI")
        else:
            axis.text(0, index, "No complete task clusters", ha="center", fontsize=10)
            names.append("Paired mean unavailable")
        axis.plot(
            (100 * estimate.missing_lower, 100 * estimate.missing_upper),
            (index + 1, index + 1),
            color="#E69F00",
            linewidth=3,
        )
        names.append("All-planned missingness bounds")
        axis.set_yticks(range(len(names)), names)
        axis.set_ylim(len(names) - 0.4, -0.7)
        axis.set_xlim(-105, 105)
        axis.axvline(0, color="#666666", linestyle="--", linewidth=0.8)
        axis.set_xlabel(f"{contrast} (percentage points)")
        axis.grid(axis="x", alpha=0.18)
        note = (
            f"Complete tasks {estimate.n_complete_clusters}/{estimate.n_planned_clusters}; "
            f"missing cells {estimate.left.value}: {estimate.missing_left}, "
            f"{estimate.right.value}: {estimate.missing_right}.\n"
            f"{estimate.resamples:,} task-cluster bootstrap resamples; "
            "orange bounds are not a confidence interval."
        )
        if estimate.degenerate_interval:
            note += "\nCollapsed interval does not establish equivalence."
        figure.text(0.5, 0.025, note, ha="center", fontsize=9)
        figure.subplots_adjust(left=0.32, right=0.97, top=0.75, bottom=0.27)
        return _export(figure, destination)
