"""Run records must survive round-trips and reject accidental replacement."""

import json
from pathlib import Path

import pytest

from context_fidelity.artifacts import (
    FrozenFile,
    freeze_files,
    load_tasks,
    save_json,
    verify_freeze,
)


def test_save_json_is_readable_and_exclusive(tmp_path: Path) -> None:
    target = tmp_path / "run" / "record.json"
    save_json(target, {"value": "λ", "samples": [1, 2]})
    assert json.loads(target.read_text()) == {"value": "λ", "samples": [1, 2]}
    with pytest.raises(FileExistsError):
        save_json(target, {"value": "changed"})
    assert json.loads(target.read_text())["value"] == "λ"
    assert list(target.parent.iterdir()) == [target]


def test_serialization_error_leaves_no_partial_artifact(tmp_path: Path) -> None:
    target = tmp_path / "record.json"
    with pytest.raises(ValueError):
        save_json(target, {"value": float("nan")})
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_freeze_detects_content_changes_and_missing_files(tmp_path: Path) -> None:
    source = tmp_path / "task.json"
    source.write_text('{"initial": true}\n')
    manifest = freeze_files(tmp_path, [source], created_at="2026-09-13T00:00:00Z")
    assert tuple(item.path for item in manifest.files) == ("task.json",)
    verify_freeze(tmp_path, manifest)
    source.write_text('{"initial": false}\n')
    with pytest.raises(ValueError, match="changed"):
        verify_freeze(tmp_path, manifest)
    source.unlink()
    with pytest.raises(ValueError, match="missing"):
        verify_freeze(tmp_path, manifest)


def test_freeze_rejects_empty_duplicate_or_outside_paths(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    inside = root / "file"
    inside.write_text("content")
    outside = tmp_path / "private"
    outside.write_text("unrelated")
    for paths in ([], [inside, inside], [outside], [root / "absent"]):
        with pytest.raises(ValueError):
            freeze_files(root, paths, created_at="now")


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    external = tmp_path / "external"
    external.write_text("unrelated")
    link = root / "linked"
    link.symlink_to(external)
    with pytest.raises(ValueError, match="outside"):
        freeze_files(root, [link], created_at="now")


def test_tasks_loaded_in_stable_order_and_checked_for_split() -> None:
    tasks = load_tasks(Path("tasks/dev"), split="dev")
    assert len(tasks) == 4
    assert [task.task_id for task in tasks] == sorted(task.task_id for task in tasks)
    assert all(len(task.tests) >= 3 for task in tasks)
    with pytest.raises(ValueError, match="split"):
        load_tasks(Path("tasks/dev"), split="heldout")


def test_empty_or_duplicate_task_set_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty"):
        load_tasks(tmp_path)
    content = Path("tasks/dev/dev-clamp.json").read_text()
    (tmp_path / "first.json").write_text(content)
    (tmp_path / "second.json").write_text(content)
    with pytest.raises(ValueError, match="duplicate"):
        load_tasks(tmp_path)


@pytest.mark.parametrize("path", ["/absolute", "../escape", ".", ""])
def test_manifest_cannot_request_paths_outside_the_repository(path: str) -> None:
    with pytest.raises(ValueError):
        FrozenFile(path=path, sha256="a" * 64)


def test_typed_records_serialize_without_mutable_conversion(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"data\r\n")
    manifest = freeze_files(tmp_path, [source], created_at="now")
    save_json(tmp_path / "freeze.json", manifest)
    assert json.loads((tmp_path / "freeze.json").read_text())["files"][0]["path"] == "source"
