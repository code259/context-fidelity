"""Exclusive artifact writes and content-addressed protocol freezes."""

import hashlib
import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Literal, Self

from pydantic import BaseModel, Field, StrictStr, field_validator, model_validator

from context_fidelity.contracts import Digest, Record, TaskSpec


class FrozenFile(Record):
    path: StrictStr
    sha256: Digest

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or str(path) in {"", "."}:
            raise ValueError("frozen paths must stay inside the repository")
        return value


class FreezeManifest(Record):
    created_at: StrictStr = Field(min_length=1)
    files: tuple[FrozenFile, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_paths(self) -> Self:
        if len({item.path for item in self.files}) != len(self.files):
            raise ValueError("duplicate frozen path")
        return self


def save_json(path: Path, value: object) -> None:
    """Atomically create a JSON artifact without replacing an existing record."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    encoded = (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".record-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink()


def _inside(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"path is outside repository: {path.name}")
    if not resolved.is_file():
        raise ValueError(f"frozen file is missing: {path.name}")
    return resolved


def freeze_files(root: Path, paths: Sequence[Path], *, created_at: str) -> FreezeManifest:
    """Hash exact file bytes; never read a path outside the declared root."""
    entries = []
    for path in paths:
        resolved = _inside(root, path)
        entries.append(
            FrozenFile(
                path=resolved.relative_to(root.resolve()).as_posix(),
                sha256=hashlib.sha256(resolved.read_bytes()).hexdigest(),
            )
        )
    return FreezeManifest(created_at=created_at, files=tuple(sorted(entries, key=lambda f: f.path)))


def verify_freeze(root: Path, manifest: FreezeManifest) -> None:
    """Reject missing, escaped, or changed experimental inputs before execution."""
    for item in manifest.files:
        path = _inside(root, root / item.path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != item.sha256:
            raise ValueError(f"frozen file changed: {item.path}")


def load_tasks(
    directory: Path, *, split: Literal["dev", "heldout"] | None = None
) -> tuple[TaskSpec, ...]:
    tasks = tuple(
        TaskSpec.model_validate_json(path.read_bytes()) for path in sorted(directory.glob("*.json"))
    )
    if not tasks:
        raise ValueError("task directory is empty")
    if len({task.task_id for task in tasks}) != len(tasks):
        raise ValueError("duplicate task ID")
    if split is not None and any(task.split != split for task in tasks):
        raise ValueError("task split differs from requested split")
    return tuple(sorted(tasks, key=lambda task: task.task_id))
