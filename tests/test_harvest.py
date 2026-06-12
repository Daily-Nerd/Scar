"""Harvest heuristics over a synthetic git history."""

import subprocess

import pytest

from scar.harvest import harvest


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def history(tmp_path):
    git(tmp_path.parent, "init", "-q", "-b", "main", str(tmp_path))
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    # component that will die
    comp = tmp_path / "apps" / "shortlived"
    comp.mkdir(parents=True)
    (comp / "deploy.yaml").write_text("replicas: 1\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "feat: add shortlived app")
    # flapping value
    (comp / "deploy.yaml").write_text("replicas: 3\n")
    git(tmp_path, "commit", "-qam", "feat: scale up")
    (comp / "deploy.yaml").write_text("replicas: 1\n")
    git(tmp_path, "commit", "-qam", "fix: revert replicas, instance broke")
    # delete the component
    (comp / "deploy.yaml").unlink()
    comp.rmdir()
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "chore: remove shortlived app")
    return tmp_path


def test_finds_revert_shaped_commits(history):
    result = harvest(history)
    assert any("revert" in c["subject"].lower() for c in result["reverts"])


def test_finds_deleted_components(history):
    result = harvest(history)
    assert any(c["component"] == "apps/shortlived" for c in result["deleted_components"])


def test_finds_value_flapping(history):
    result = harvest(history)
    flaps = result["flapping"]
    assert any(f["key"] == "replicas" and "1 -> 3 -> 1" in f["sequence"] for f in flaps)


def test_clean_repo_yields_empty_sections(tmp_path):
    git(tmp_path.parent, "init", "-q", "-b", "main", str(tmp_path))
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("x")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "feat: initial")
    result = harvest(tmp_path)
    assert result["reverts"] == [] and result["deleted_components"] == []
