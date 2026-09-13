"""Hand-computed cluster effects and adversarial missingness fixtures."""

import pytest

from context_fidelity.analyze import Observation, PairKey, paired_effect
from context_fidelity.contracts import Arm


def pair(task: str, repetition: int = 0, environment: str = "normal") -> PairKey:
    return PairKey(
        task_id=task,
        history_id=f"{task}-{environment}",
        environment=environment,
        repetition=repetition,
    )


def obs(key: PairKey, arm: Arm, value: float | None) -> Observation:
    return Observation(key=key, arm=arm, value=value)


def test_average_repetitions_within_task_before_bootstrap() -> None:
    keys = (pair("a", 0), pair("a", 1), pair("b"))
    rows = (
        obs(keys[0], Arm.C, 0),
        obs(keys[0], Arm.D, 1),
        obs(keys[1], Arm.C, 0),
        obs(keys[1], Arm.D, 0),
        obs(keys[2], Arm.C, 1),
        obs(keys[2], Arm.D, 0),
    )
    result = paired_effect(rows, keys)
    assert result.effect == pytest.approx(0.25)  # mean(-0.5, +1), not mean(-1,0,+1)
    assert result.cluster_differences == (("a", -0.5), ("b", 1.0))
    assert result.left_mean == pytest.approx(0.5)
    assert result.right_mean == pytest.approx(0.25)
    assert result.ci_low == -0.5 and result.ci_high == 1.0
    assert result.missing_lower == result.missing_upper == pytest.approx(0.25)


def test_a_missing_repetition_excludes_entire_cluster_and_retains_bounds() -> None:
    keys = (pair("a", 0), pair("a", 1), pair("b"))
    rows = (
        obs(keys[0], Arm.C, 0),
        obs(keys[0], Arm.D, 1),
        obs(keys[1], Arm.C, None),
        obs(keys[1], Arm.D, 1),
        obs(keys[2], Arm.C, 1),
        obs(keys[2], Arm.D, 0),
    )
    result = paired_effect(rows, keys)
    assert result.effect == 1 and result.n_complete_clusters == 1
    assert result.excluded_tasks == ("a",)
    assert result.missing_left == 1 and result.missing_right == 0
    assert result.missing_lower == 0 and result.missing_upper == 0.25
    assert result.degenerate_interval


def test_unrecorded_planned_rows_are_missing_not_dropped() -> None:
    keys = (pair("a"), pair("b"))
    result = paired_effect((obs(keys[0], Arm.C, 0), obs(keys[1], Arm.D, 1)), keys)
    assert result.effect is None and result.ci_low is None and result.ci_high is None
    assert result.missing_left == result.missing_right == 1
    assert result.missing_lower == -1 and result.missing_upper == 0
    assert result.n_complete_clusters == 0


def test_both_arms_missing_has_full_unit_bounds() -> None:
    result = paired_effect((), (pair("a"),))
    assert (result.missing_lower, result.missing_upper) == (-1, 1)


def test_order_and_seed_are_reproducible() -> None:
    keys = tuple(pair(task) for task in ("a", "b", "c", "d"))
    rows = tuple(
        obs(key, arm, (index % 3) / 2)
        for index, (key, arm) in enumerate((key, arm) for key in keys for arm in (Arm.C, Arm.D))
    )
    assert paired_effect(rows, keys, seed=99) == paired_effect(
        tuple(reversed(rows)), tuple(reversed(keys)), seed=99
    )


def test_floor_interval_is_flagged_not_claimed_as_equivalence() -> None:
    keys = (pair("a"), pair("b"))
    rows = tuple(obs(key, arm, 0) for key in keys for arm in (Arm.C, Arm.D))
    result = paired_effect(rows, keys)
    assert result.effect == result.ci_low == result.ci_high == 0
    assert result.degenerate_interval


def test_other_arms_do_not_enter_comparison_and_direction_is_explicit() -> None:
    key = pair("a")
    rows = (obs(key, Arm.C, 0), obs(key, Arm.D, 1), obs(key, Arm.A, 0.7))
    assert paired_effect(rows, (key,)).effect == -1
    assert paired_effect(rows, (key,), left=Arm.D, right=Arm.C).effect == 1


@pytest.mark.parametrize(
    "mode",
    [
        "duplicate_observation",
        "duplicate_plan",
        "outside_plan",
        "empty_plan",
        "same_arm",
        "bad_resamples",
    ],
)
def test_invalid_design_cannot_produce_an_estimate(mode: str) -> None:
    key = pair("a")
    rows = (obs(key, Arm.C, 1),)
    keys = (key,)
    kwargs: dict[str, object] = {}
    if mode == "duplicate_observation":
        rows += rows
    elif mode == "duplicate_plan":
        keys += keys
    elif mode == "outside_plan":
        rows += (obs(pair("b"), Arm.C, 1),)
    elif mode == "empty_plan":
        keys = ()
    elif mode == "same_arm":
        kwargs = {"left": Arm.C, "right": Arm.C}
    else:
        kwargs = {"resamples": 0}
    with pytest.raises(ValueError):
        paired_effect(rows, keys, **kwargs)


@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan"), float("inf")])
def test_values_must_be_bounded_finite_rates(value: float) -> None:
    with pytest.raises(ValueError):
        obs(pair("a"), Arm.C, value)
