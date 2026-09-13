"""Paired task-cluster estimates with explicit planned-cell missingness.

Each task is one independent unit. A complete-case estimate excludes the whole
task if any required environment/repetition pair is missing. Bounds retain every
planned task and allow each missing rate to range independently from zero to one.
"""

from collections import defaultdict
from collections.abc import Sequence
from statistics import fmean

import numpy as np
from pydantic import Field

from context_fidelity.contracts import Arm, Identifier, Record


class PairKey(Record):
    task_id: Identifier
    history_id: Identifier
    environment: str
    repetition: int = Field(ge=0)


class Observation(Record):
    key: PairKey
    arm: Arm
    value: float | None = Field(ge=0, le=1, allow_inf_nan=False)


class PairedEstimate(Record):
    left: Arm
    right: Arm
    effect: float | None
    ci_low: float | None
    ci_high: float | None
    left_mean: float | None
    right_mean: float | None
    n_planned_pairs: int
    n_planned_clusters: int
    n_complete_clusters: int
    missing_left: int
    missing_right: int
    missing_lower: float
    missing_upper: float
    cluster_differences: tuple[tuple[str, float], ...]
    excluded_tasks: tuple[str, ...]
    degenerate_interval: bool
    seed: int
    resamples: int


def paired_effect(
    observations: Sequence[Observation],
    planned: Sequence[PairKey],
    *,
    left: Arm = Arm.C,
    right: Arm = Arm.D,
    resamples: int = 10_000,
    seed: int = 1701,
) -> PairedEstimate:
    """Estimate ``left - right`` after averaging all cells within each task."""
    if not planned or len(set(planned)) != len(planned):
        raise ValueError("planned pairs must be nonempty and unique")
    if left == right or resamples < 1:
        raise ValueError("distinct arms and positive resamples are required")
    plan = set(planned)
    values: dict[tuple[PairKey, Arm], float | None] = {}
    for observation in observations:
        if observation.arm not in {left, right}:
            continue
        if observation.key not in plan:
            raise ValueError("observation is outside the declared design")
        index = (observation.key, observation.arm)
        if index in values:
            raise ValueError("duplicate observation")
        values[index] = observation.value

    by_task: dict[str, list[PairKey]] = defaultdict(list)
    for key in sorted(planned, key=lambda k: (k.task_id, k.history_id, k.repetition)):
        by_task[key.task_id].append(key)
    differences: list[tuple[str, float]] = []
    left_means: list[float] = []
    right_means: list[float] = []
    lower_bounds: list[float] = []
    upper_bounds: list[float] = []
    excluded: list[str] = []
    missing_left = missing_right = 0

    for task_id, keys in sorted(by_task.items()):
        lhs = [values.get((key, left)) for key in keys]
        rhs = [values.get((key, right)) for key in keys]
        missing_left += sum(value is None for value in lhs)
        missing_right += sum(value is None for value in rhs)
        lower_bounds.append(
            fmean(
                (a if a is not None else 0) - (b if b is not None else 1)
                for a, b in zip(lhs, rhs, strict=True)
            )
        )
        upper_bounds.append(
            fmean(
                (a if a is not None else 1) - (b if b is not None else 0)
                for a, b in zip(lhs, rhs, strict=True)
            )
        )
        if any(value is None for value in (*lhs, *rhs)):
            excluded.append(task_id)
            continue
        left_mean = fmean(value for value in lhs if value is not None)
        right_mean = fmean(value for value in rhs if value is not None)
        left_means.append(left_mean)
        right_means.append(right_mean)
        differences.append((task_id, left_mean - right_mean))

    effect: float | None = None
    low: float | None = None
    high: float | None = None
    if differences:
        deltas = np.asarray([value for _, value in differences], dtype=np.float64)
        effect = float(deltas.mean())
        rng = np.random.default_rng(seed)
        samples = deltas[rng.integers(0, len(deltas), size=(resamples, len(deltas)))].mean(axis=1)
        limits = np.quantile(samples, [0.025, 0.975], method="linear")
        low, high = float(limits[0]), float(limits[1])
    return PairedEstimate(
        left=left,
        right=right,
        effect=effect,
        ci_low=low,
        ci_high=high,
        left_mean=fmean(left_means) if left_means else None,
        right_mean=fmean(right_means) if right_means else None,
        n_planned_pairs=len(planned),
        n_planned_clusters=len(by_task),
        n_complete_clusters=len(differences),
        missing_left=missing_left,
        missing_right=missing_right,
        missing_lower=fmean(lower_bounds),
        missing_upper=fmean(upper_bounds),
        cluster_differences=tuple(differences),
        excluded_tasks=tuple(excluded),
        degenerate_interval=low is not None and low == high,
        seed=seed,
        resamples=resamples,
    )
