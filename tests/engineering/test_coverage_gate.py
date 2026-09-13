"""Reject weak coverage even when a combined score appears acceptable."""

import json
import subprocess
import sys
from pathlib import Path
from runpy import run_path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_coverage.py"


def summary(statements=100, covered=100, branches=20, covered_branches=20):
    return {
        "num_statements": statements,
        "covered_lines": covered,
        "num_branches": branches,
        "covered_branches": covered_branches,
    }


def invoke(tmp_path, totals, files=None, branch_coverage=True):
    report = {
        "meta": {"branch_coverage": branch_coverage},
        "totals": totals,
        "files": files or {"src/context_fidelity/adapter.py": {"summary": totals}},
    }
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(report))
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "totals, expected",
    [
        (summary(covered=90, covered_branches=18), 0),
        (summary(covered=89), 1),
        (summary(covered_branches=17), 1),
        (summary(branches=0, covered_branches=0), 0),
        (summary(statements=0, covered=0, branches=0, covered_branches=0), 1),
    ],
)
def test_overall_thresholds_are_independent(tmp_path, totals, expected):
    assert invoke(tmp_path, totals).returncode == expected


@pytest.mark.parametrize("name", ["evidence", "contexts", "score", "analyze"])
@pytest.mark.parametrize("low", [summary(covered=94), summary(covered_branches=18)])
def test_high_total_cannot_hide_a_weak_critical_module(tmp_path, name, low):
    files = {f"src/context_fidelity/{name}.py": {"summary": low}}
    assert invoke(tmp_path, summary(), files).returncode == 1


def test_critical_module_accepts_exact_boundary(tmp_path):
    data = summary(covered=95, covered_branches=19)
    files = {"src/context_fidelity/score.py": {"summary": data}}
    assert invoke(tmp_path, summary(), files).returncode == 0


def test_missing_branch_measurement_fails(tmp_path):
    assert invoke(tmp_path, summary(), branch_coverage=False).returncode == 1


@pytest.mark.parametrize("field, value", [("covered_lines", -1), ("covered_lines", 101)])
def test_invalid_counts_fail_closed(tmp_path, field, value):
    data = summary()
    data[field] = value
    assert invoke(tmp_path, data).returncode == 1


def test_malformed_report_fails(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text("{}")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(path)], capture_output=True, check=False
    )
    assert result.returncode == 1


def test_empty_file_measurements_are_rejected():
    check_report = run_path(str(SCRIPT))["check_report"]
    with pytest.raises(ValueError, match="No file coverage"):
        check_report({"meta": {"branch_coverage": True}, "files": {}})
