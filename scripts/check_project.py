"""Validate repository configuration, source placement, and local Markdown file links."""

import argparse
import re
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml

IGNORED = {
    ".git",
    ".venv",
    ".cache",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".hypothesis",
    "runs",
    "models",
    "checkpoints",
    "dist",
    "build",
}
LINK = re.compile(r"\]\(([^)]+)\)")
ACTION = re.compile(r"[^\s@]+@[a-fA-F0-9]{40}")


def project_files(root: Path) -> Iterator[Path]:
    for path in root.rglob("*"):
        if path.is_file() and not (set(path.relative_to(root).parts) & IGNORED):
            yield path


def action_errors(node: Any, path: Path) -> list[str]:
    errors = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "uses":
                if not isinstance(value, str) or not (
                    value.startswith("./") or ACTION.fullmatch(value)
                ):
                    errors.append(f"{path}: action must use a full commit SHA: {value}")
            else:
                errors.extend(action_errors(value, path))
    elif isinstance(node, list):
        for value in node:
            errors.extend(action_errors(value, path))
    return errors


def validate(root: Path) -> list[str]:
    errors = []
    for required in ("README.md", "AGENTS.md", "pyproject.toml", "uv.lock"):
        if not (root / required).is_file():
            errors.append(f"Missing {required}")
    for path in project_files(root):
        relative = path.relative_to(root)
        try:
            if path.suffix in {".toml", ".lock"}:
                tomllib.loads(path.read_text())
            elif path.suffix in {".yaml", ".yml"}:
                data = yaml.safe_load(path.read_text())
                if relative.parts[:2] == (".github", "workflows"):
                    errors.extend(action_errors(data, relative))
            elif path.suffix == ".md":
                for raw in LINK.findall(path.read_text()):
                    link = raw.strip("<>")
                    parsed = urlsplit(link)
                    if parsed.scheme or parsed.netloc or not parsed.path:
                        continue
                    target = (path.parent / unquote(parsed.path)).resolve()
                    if not target.is_relative_to(root.resolve()) or not target.exists():
                        errors.append(f"{relative}: missing/outside-repository link {link}")
            elif path.suffix == ".py" and relative.parts[0] not in {
                "src",
                "scripts",
                "tests",
                "tasks",
                "sandbox",
            }:
                errors.append(f"{relative}: application source must live under src/")
        except (OSError, ValueError, yaml.YAMLError) as exc:
            errors.append(f"{relative}: invalid file: {exc}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args()
    errors = validate(args.root.resolve())
    if errors:
        print("\n".join(errors))
        return 1
    print("Project configuration and local Markdown file links passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
