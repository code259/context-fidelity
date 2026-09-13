"""Enforce statement and branch coverage independently, including critical modules."""

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

CRITICAL_MODULES = {"evidence.py", "contexts.py", "score.py", "analyze.py"}


def check_summary(data: Mapping[str, Any], minimum: int, label: str) -> list[str]:
    errors = []
    for total_key, covered_key, kind in (
        ("num_statements", "covered_lines", "statements"),
        ("num_branches", "covered_branches", "branches"),
    ):
        total, covered = data[total_key], data[covered_key]
        if type(total) is not int or type(covered) is not int or not 0 <= covered <= total:
            raise ValueError(f"{label}: invalid {kind} counts")
        if total == 0:
            if kind == "statements":
                errors.append(f"{label}: no measured statements")
            continue
        if 100 * covered < minimum * total:
            errors.append(f"{label}: {kind} {100 * covered / total:.2f}% < {minimum}%")
    return errors


def check_report(report: dict[str, Any]) -> list[str]:
    if report["meta"]["branch_coverage"] is not True:
        raise ValueError("Branch measurement is required")
    files = report["files"]
    if not isinstance(files, dict) or not files:
        raise ValueError("No file coverage was measured")
    errors = check_summary(report["totals"], 90, "overall")
    for filename, record in files.items():
        if Path(filename).name in CRITICAL_MODULES:
            errors.extend(check_summary(record["summary"], 95, filename))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        errors = check_report(json.loads(args.report.read_text()))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Invalid coverage report: {exc}")
        return 1
    if errors:
        print("\n".join(errors))
        return 1
    print("Coverage requirements satisfied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
