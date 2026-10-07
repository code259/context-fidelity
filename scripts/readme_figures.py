"""Draw the README figures from committed held-out and AI-review results.

Inputs are read-only result files under ``results/``. Outputs go to
``docs/images/figures``. The frozen analysis figures in ``results/`` stay as published.
"""

import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
METRICS = ROOT / "results/heldout-002/metrics.json"
FIGURE_INPUTS = ROOT / "results/heldout-002/figure-inputs.json"
AI_RESULTS = ROOT / "results/ai-review-001/provenance/ai-results.json"
OUT = ROOT / "docs/images/figures"

ARMS = ("A", "B", "C", "D")
LABELS = {
    "A": "Native\nhistory",
    "B": "Full\ntranscript",
    "C": "Compact\nevidence",
    "D": "Model\nsummary",
}
# Okabe-Ito colours, matching the analysis package.
COLOURS = {"A": "#56B4E9", "B": "#0072B2", "C": "#009E73", "D": "#E69F00"}
INK = "#222222"
MUTED = "#6b6b6b"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _style(ax: Axes) -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(colors=INK)
    ax.grid(axis="y", color="#e6e6e6", linewidth=0.8)
    ax.set_axisbelow(True)


def _save(fig: Any, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / name, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def errors(ai: Any) -> None:
    counts = {row["arm"]: row for row in ai["arm_counts"]}
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    width = 0.38
    for i, arm in enumerate(ARMS):
        row = counts[arm]
        n = row["reports"]
        for offset, key, alpha in (
            (-width / 2, "structured_errors", 0.55),
            (width / 2, "ai_assessed_errors", 1.0),
        ):
            value = row[key]
            ax.bar(i + offset, 100 * value / n, width, color=COLOURS[arm], alpha=alpha)
            ax.text(
                i + offset, 100 * value / n + 1, f"{value}/{n}", ha="center", fontsize=9, color=INK
            )
    ax.set_xticks(range(len(ARMS)), [LABELS[a] for a in ARMS])
    ax.set_ylabel("Reports with an error (%)")
    ax.set_ylim(0, 55)
    ax.set_title("Report errors by context", loc="left", fontsize=13, color=INK)
    handles = [
        Rectangle((0, 0), 1, 1, color=MUTED, alpha=0.55),
        Rectangle((0, 0), 1, 1, color=MUTED),
    ]
    ax.legend(
        handles,
        ["Status fields only", "Status fields + prose (AI review)"],
        frameon=False,
        loc="upper left",
    )
    _style(ax)
    _save(fig, "errors.png")


def history_size(metrics: Any) -> None:
    tokens = metrics["payload_tokens"]
    fig, ax = plt.subplots(figsize=(7.5, 2.8))
    for i, arm in enumerate(ARMS):
        mean = tokens[arm]["mean"]
        ax.barh(i, mean, color=COLOURS[arm])
        ax.text(mean + 40, i, f"{mean:,.0f}", va="center", fontsize=9, color=INK)
    ax.set_yticks(range(len(ARMS)), [LABELS[a].replace("\n", " ") for a in ARMS])
    ax.invert_yaxis()
    ax.set_xlabel("Mean tokens of history given to the reporter")
    ax.set_xlim(0, 4000)
    ax.set_title("History length by context", loc="left", fontsize=13, color=INK)
    _style(ax)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color="#e6e6e6", linewidth=0.8)
    _save(fig, "history-length.png")


def per_task(metrics: Any) -> None:
    effect = next(
        e["estimate"]
        for e in metrics["effects"]
        if e["comparison"] == "C-D" and e["metric"] == "structured_unreliable"
    )
    tasks = [task for task, _ in effect["cluster_differences"]]
    diffs = [100 * value for _, value in effect["cluster_differences"]]
    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    y = list(range(len(tasks)))
    ax.axvline(0, color=MUTED, linestyle="--", linewidth=1)
    ax.scatter(diffs, y, color=COLOURS["B"], zorder=3)
    mean_y = len(tasks) + 0.6
    ax.plot(
        [100 * effect["ci_low"], 100 * effect["ci_high"]],
        [mean_y, mean_y],
        color=COLOURS["C"],
        linewidth=4,
    )
    ax.scatter([100 * effect["effect"]], [mean_y], color=COLOURS["C"], marker="D", s=70, zorder=3)
    ax.set_yticks([*y, mean_y], [t.split("-", 1)[1] for t in tasks] + ["Mean, 95% CI"])
    ax.invert_yaxis()
    ax.set_xlim(-60, 60)
    ax.set_xlabel("Compact evidence minus summary, error rate (percentage points)")
    ax.text(-58, -0.9, "← evidence better", fontsize=9, color=MUTED)
    ax.text(58, -0.9, "summary better →", fontsize=9, color=MUTED, ha="right")
    ax.set_title("Error difference by task", loc="left", fontsize=13, color=INK, pad=18)
    _style(ax)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color="#e6e6e6", linewidth=0.8)
    _save(fig, "per-task.png")


def retention(figure_inputs: Any) -> None:
    names = {
        "code_saved": "Code saved",
        "note_saved": "Note saved",
        "verification": "Test result",
        "all_steps_complete": "All steps done",
    }
    rows = figure_inputs["retention"]
    fig, ax = plt.subplots(figsize=(7.5, 3.2))
    for i, row in enumerate(rows):
        share = 100 * row["n_retained"] / row["n_audited"]
        ax.bar(i, share, 0.6, color=COLOURS["D"])
        ax.text(
            i,
            share + 2,
            f"{row['n_retained']}/{row['n_audited']}",
            ha="center",
            fontsize=9,
            color=INK,
        )
    ax.set_xticks(range(len(rows)), [names[row["field"]] for row in rows])
    ax.set_ylabel("Summaries stating it correctly (%)")
    ax.set_ylim(0, 112)
    ax.set_title("What the summaries kept", loc="left", fontsize=13, color=INK)
    _style(ax)
    _save(fig, "summary-retention.png")


def main() -> None:
    metrics = _load(METRICS)
    errors(_load(AI_RESULTS))
    history_size(metrics)
    per_task(metrics)
    retention(_load(FIGURE_INPUTS))


if __name__ == "__main__":
    main()
