"""Exercise the repository validator against actual temporary files."""

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_project.py"


def fixture(tmp_path):
    (tmp_path / "README.md").write_text("# Example\n\n[Rules](AGENTS.md)\n")
    (tmp_path / "AGENTS.md").write_text("# Rules\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "example"\n')
    (tmp_path / "uv.lock").write_text("version = 1\n")
    return tmp_path


def invoke(root):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(root)], text=True, capture_output=True, check=False
    )


def test_valid_local_link_passes(tmp_path):
    assert invoke(fixture(tmp_path)).returncode == 0


def test_missing_local_document_fails(tmp_path):
    root = fixture(tmp_path)
    (root / "AGENTS.md").unlink()
    assert invoke(root).returncode == 1


def test_external_urls_and_local_fragments_are_not_network_checks(tmp_path):
    root = fixture(tmp_path)
    (root / "README.md").write_text("[Web](https://example.org/) [Section](#example)")
    assert invoke(root).returncode == 0


def test_invalid_toml_fails(tmp_path):
    root = fixture(tmp_path)
    (root / "pyproject.toml").write_text("[broken")
    assert invoke(root).returncode == 1


def test_missing_lockfile_fails(tmp_path):
    root = fixture(tmp_path)
    (root / "uv.lock").unlink()
    assert invoke(root).returncode == 1


def test_mutable_action_reference_fails(tmp_path):
    root = fixture(tmp_path)
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(
        "jobs:\n  test:\n    steps:\n      - uses: actions/checkout@main\n"
    )
    assert invoke(root).returncode == 1


def test_pinned_action_reference_passes(tmp_path):
    root = fixture(tmp_path)
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(
        "jobs:\n  test:\n    steps:\n      - uses: org/action@" + "a" * 40 + "\n"
    )
    assert invoke(root).returncode == 0


def test_nonstandard_python_location_fails(tmp_path):
    root = fixture(tmp_path)
    (root / "experiment.py").write_text("print('bypasses source coverage')\n")
    assert invoke(root).returncode == 1


def test_agent_scratch_records_do_not_become_repository_documents(tmp_path):
    root = fixture(tmp_path)
    scratch = root / ".superpowers" / "sdd"
    scratch.mkdir(parents=True)
    (scratch / "review.md").write_text("[Transient reviewer artifact](missing-local-file.md)")
    assert invoke(root).returncode == 0
