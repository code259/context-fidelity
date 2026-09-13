"""Scientific figure contracts: missingness, denominators, and real exports."""

from pathlib import Path

import pytest
from PIL import Image

from context_fidelity.analyze import Observation, PairKey, paired_effect
from context_fidelity.contracts import Arm
from context_fidelity.plots import (
    ArmMetric,
    FactRetention,
    PlotInfo,
    plot_arm_metrics,
    plot_fact_retention,
    plot_paired_effect,
)


def info(review_status: str = "provisional") -> PlotInfo:
    return PlotInfo.model_validate(
        {"run_id": "test-development", "phase": "development", "review_status": review_status}
    )


def metrics() -> tuple[ArmMetric, ...]:
    return (
        ArmMetric(arm=Arm.A, n_planned=8, n_observed=8, n_errors=2, mean_coverage=0.75),
        ArmMetric(arm=Arm.B, n_planned=8, n_observed=6, n_errors=1, mean_coverage=0.5),
        ArmMetric(arm=Arm.C, n_planned=8, n_observed=8, n_errors=0, mean_coverage=1),
        ArmMetric(arm=Arm.D, n_planned=8, n_observed=0, n_errors=0, mean_coverage=None),
    )


def retention() -> tuple[FactRetention, ...]:
    return tuple(
        FactRetention.model_validate(
            {"field": field, "n_planned": 8, "n_audited": audited, "n_retained": retained}
        )
        for field, audited, retained in (
            ("code_saved", 8, 8),
            ("note_saved", 6, 3),
            ("verification", 4, 1),
            ("all_steps_complete", 0, 0),
        )
    )


def test_missing_observations_are_not_zero_error_or_zero_retention() -> None:
    rows = metrics()
    assert rows[0].error_rate == 0.25
    assert rows[1].error_rate == pytest.approx(1 / 6)
    assert rows[2].error_rate == 0
    assert rows[3].error_rate is None
    facts = retention()
    assert [row.retention_rate for row in facts] == [1, 0.5, 0.25, None]


@pytest.mark.parametrize(
    "changes",
    [
        {"n_observed": 9},
        {"n_errors": 9},
        {"mean_coverage": None},
        {"n_observed": 0, "n_errors": 0},
        {"mean_coverage": float("nan")},
        {"mean_coverage": float("inf")},
        {"n_errors": True},
        {"arm": "rescue"},
    ],
)
def test_inconsistent_arm_denominators_and_nonfinite_values_are_rejected(
    changes: dict[str, object],
) -> None:
    data = metrics()[0].model_dump() | changes
    with pytest.raises(ValueError):
        ArmMetric.model_validate(data)


@pytest.mark.parametrize("changes", [{"n_audited": 9}, {"n_retained": 9}, {"field": "made_up"}])
def test_retention_cannot_exceed_audited_population(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        FactRetention.model_validate(retention()[0].model_dump() | changes)


def test_fact_with_no_eligible_history_remains_unavailable() -> None:
    row = FactRetention(field="verification", n_planned=0, n_audited=0, n_retained=0)
    assert row.retention_rate is None


def assert_exports(png: Path, pdf: Path) -> None:
    assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert pdf.read_bytes().startswith(b"%PDF-")
    with Image.open(png) as image:
        assert image.size[0] > 1500 and image.size[1] > 600
        assert image.info["dpi"] == pytest.approx((300, 300), abs=0.02)


def test_real_arm_and_retention_figures_export_vector_and_300_dpi(tmp_path: Path) -> None:
    arms = plot_arm_metrics(metrics(), tmp_path / "metrics", info=info())
    facts = plot_fact_retention(retention(), tmp_path / "retention", info=info("human-reviewed"))
    assert_exports(arms.png, arms.pdf)
    assert_exports(facts.png, facts.pdf)


@pytest.mark.parametrize("kind", ["duplicate_arms", "missing_arm", "duplicate_facts"])
def test_complete_unique_figure_categories_required(tmp_path: Path, kind: str) -> None:
    with pytest.raises(ValueError, match="exactly once"):
        if kind == "duplicate_arms":
            plot_arm_metrics(metrics() + (metrics()[0],), tmp_path / "plot", info=info())
        elif kind == "missing_arm":
            plot_arm_metrics(metrics()[:-1], tmp_path / "plot", info=info())
        else:
            plot_fact_retention(retention() + (retention()[0],), tmp_path / "plot", info=info())


def test_existing_export_is_preserved_before_any_output_is_written(tmp_path: Path) -> None:
    existing = tmp_path / "metrics.pdf"
    existing.write_bytes(b"original result")
    with pytest.raises(FileExistsError):
        plot_arm_metrics(metrics(), tmp_path / "metrics", info=info())
    assert existing.read_bytes() == b"original result"
    assert not (tmp_path / "metrics.png").exists()


def test_plot_titles_make_provisional_status_and_split_explicit() -> None:
    provisional = info().caption("Structured status errors")
    assert "Development" in provisional and "test-development" in provisional
    assert "provisional" in provisional and "human review pending" in provisional
    reviewed = info("human-reviewed").caption("Structured status errors")
    assert "human review complete" in reviewed
    assert "Structured" in reviewed  # Reviewed structured values remain structured values.


@pytest.mark.parametrize("missing", [False, True])
def test_paired_plot_uses_complete_clusters_and_keeps_missingness(
    tmp_path: Path, missing: bool
) -> None:
    keys = tuple(
        PairKey(task_id=task, history_id=task, environment="normal", repetition=0)
        for task in ("task-a", "task-b")
    )
    rows = (
        ()
        if missing
        else (
            Observation(key=keys[0], arm=Arm.C, value=0),
            Observation(key=keys[0], arm=Arm.D, value=1),
        )
    )
    result = paired_effect(rows, keys)
    files = plot_paired_effect(result, tmp_path / "paired", info=info())
    assert_exports(files.png, files.pdf)
    assert result.n_complete_clusters == (0 if missing else 1)


def test_primary_plot_cannot_claim_completed_human_review_from_provisional_input(
    tmp_path: Path,
) -> None:
    key = PairKey(task_id="t", history_id="t", environment="normal", repetition=0)
    result = paired_effect((), (key,))
    with pytest.raises(ValueError, match="human review"):
        plot_paired_effect(
            result, tmp_path / "primary", info=info(), metric="primary_unreliability"
        )
    files = plot_paired_effect(
        result, tmp_path / "primary", info=info("human-reviewed"), metric="primary_unreliability"
    )
    assert_exports(files.png, files.pdf)


def test_coverage_effect_uses_percentage_points_not_an_error_label(tmp_path: Path) -> None:
    key = PairKey(task_id="t", history_id="t", environment="normal", repetition=0)
    result = paired_effect(
        (Observation(key=key, arm=Arm.C, value=1), Observation(key=key, arm=Arm.D, value=0.75)),
        (key,),
    )
    files = plot_paired_effect(
        result, tmp_path / "coverage", info=info(), metric="factual_coverage"
    )
    assert_exports(files.png, files.pdf)


def test_effect_without_its_interval_is_rejected(tmp_path: Path) -> None:
    key = PairKey(task_id="t", history_id="t", environment="normal", repetition=0)
    broken = paired_effect((), (key,)).model_copy(update={"effect": 0.5})
    with pytest.raises(ValueError, match="requires its interval"):
        plot_paired_effect(broken, tmp_path / "broken", info=info())
    assert not (tmp_path / "broken.png").exists()
