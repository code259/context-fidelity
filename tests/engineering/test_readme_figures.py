"""README figures are drawn from committed results, with labels taken from the inputs."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "readme_figures.py"
NAMES = {"errors.png", "history-length.png", "per-task.png", "summary-retention.png"}
PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(scope="module")
def figures():
    spec = importlib.util.spec_from_file_location("readme_figures", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def labels(fig):
    texts = [text.get_text() for ax in fig.axes for text in ax.texts]
    title = fig.axes[0].get_title(loc="left")
    plt.close(fig)
    return title, texts


def test_main_writes_every_png_from_committed_results(figures, tmp_path):
    out = tmp_path / "figures"
    figures.main(out)

    assert {path.name for path in out.iterdir()} == NAMES
    for name in NAMES:
        assert (out / name).read_bytes()[:8] == PNG


def test_error_labels_follow_arm_counts(figures):
    row = {"reports": 10, "structured_errors": 2, "ai_assessed_errors": 3}
    fig = figures.errors({"arm_counts": [{"arm": arm, **row} for arm in "ABCD"]})

    assert labels(fig) == ("Report errors by context", ["2/10", "3/10"] * 4)


def test_per_task_uses_only_the_c_minus_d_structured_estimate(figures):
    wanted = {
        "comparison": "C-D",
        "metric": "structured_unreliable",
        "estimate": {
            "effect": 0.25,
            "ci_low": 0.0,
            "ci_high": 0.5,
            "cluster_differences": [["t01-alpha", 0.5], ["t02-beta", 0.0]],
        },
    }
    decoy = {**wanted, "metric": "coverage", "estimate": {}}
    fig = figures.per_task({"effects": [decoy, wanted]})
    ticks = [tick.get_text() for tick in fig.axes[0].get_yticklabels()]

    assert ticks == ["alpha", "beta", "Mean, 95% CI"]
    assert labels(fig) == ("Error difference by task", ["← evidence better", "summary better →"])


def test_retention_and_history_labels(figures):
    retained = figures.retention(
        {"retention": [{"field": "note_saved", "n_retained": 3, "n_audited": 4}]}
    )
    lengths = figures.history_size({"payload_tokens": {arm: {"mean": 1234.4} for arm in "ABCD"}})

    assert labels(retained) == ("What the summaries kept", ["3/4"])
    assert labels(lengths) == ("History length by context", ["1,234"] * 4)


def test_program_writes_to_the_directory_argument(tmp_path):
    out = tmp_path / "out"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(out)],
        capture_output=True,
        text=True,
        env={**os.environ, "MPLCONFIGDIR": str(tmp_path / "mpl")},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert {path.name for path in out.iterdir()} == NAMES
