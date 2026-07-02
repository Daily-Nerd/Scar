"""scar draft-check (#117) — universal authoring trigger, git evidence only.

Real git repos throughout: every signal here (revert language, actual
`git revert` commits, reset reflog entries, churn) is a git fact, not a
content fact — same rationale as test_evidence.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from scar.cli import main
from scar.store import init_scars


# ---------------------------------------------------------------------------
# git fixtures
# ---------------------------------------------------------------------------

def _git(tmp: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(["git", "-C", str(tmp), *args], capture_output=True,
                          text=True, check=True, env=env).stdout.strip()


def _init_git(tmp: Path) -> None:
    _git(tmp, "init", "-q")
    _git(tmp, "config", "user.email", "t@t.t")
    _git(tmp, "config", "user.name", "t")


def _commit(tmp: Path, name: str, content: str = "x", message: str | None = None) -> str:
    (tmp / name).write_text(content)
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-q", "-m", message or name)
    return _git(tmp, "rev-parse", "HEAD")


def _commit_at(tmp: Path, name: str, content: str, message: str, epoch: int) -> str:
    (tmp / name).write_text(content)
    _git(tmp, "add", "-A")
    env = os.environ.copy()
    date = f"{epoch} +0000"
    env["GIT_AUTHOR_DATE"] = date
    env["GIT_COMMITTER_DATE"] = date
    _git(tmp, "commit", "-q", "-m", message, env=env)
    return _git(tmp, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    _init_git(tmp_path)
    init_scars(tmp_path)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init scars")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def state(tmp_path_factory, monkeypatch):
    d = tmp_path_factory.mktemp("scar-state")
    monkeypatch.setenv("SCAR_STATE_DIR", str(d))
    return d


def out_json(capsys):
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


# ---------------------------------------------------------------------------
# a. revert language in commit messages
# ---------------------------------------------------------------------------

def test_revert_message_trips(repo, state, capsys):
    _commit(repo, "a.py", "1", message="revert the auth change, back to the original")
    assert main(["draft-check"]) == 0
    out = capsys.readouterr().out
    assert "SCAR draft-check" in out
    assert "revert_language=1" in out


# ---------------------------------------------------------------------------
# b. actual `git revert` commits
# ---------------------------------------------------------------------------

def test_git_revert_commit_trips(repo, state, capsys):
    _commit(repo, "a.py", "1", message="add a")
    _commit(repo, "a.py", "2", message="change a")
    _git(repo, "revert", "--no-edit", "HEAD")
    assert main(["draft-check"]) == 0
    out = capsys.readouterr().out
    assert "SCAR draft-check" in out
    assert "revert_commits=1" in out


# ---------------------------------------------------------------------------
# b. reset --hard reflog entries
# ---------------------------------------------------------------------------

def test_reset_hard_reflog_trips(repo, state, capsys):
    _commit(repo, "a.py", "1", message="add a")
    _commit(repo, "a.py", "2", message="change a")
    _git(repo, "reset", "--hard", "HEAD~1")
    assert main(["draft-check"]) == 0
    out = capsys.readouterr().out
    assert "SCAR draft-check" in out
    assert "reset_hard=1" in out


# ---------------------------------------------------------------------------
# clean history -> silent
# ---------------------------------------------------------------------------

def test_clean_history_silent(repo, state, capsys):
    _commit(repo, "a.py", "1", message="implement feature")
    _commit(repo, "b.py", "1", message="add tests")
    assert main(["draft-check"]) == 0
    out = capsys.readouterr().out
    assert out == ""


def test_clean_history_json_shape(repo, state, capsys):
    _commit(repo, "a.py", "1", message="implement feature")
    assert main(["draft-check", "--json"]) == 0
    payload = out_json(capsys)
    assert payload["triggered"] is False


def test_triggered_json_shape(repo, state, capsys):
    _commit(repo, "a.py", "1", message="revert the auth change")
    assert main(["draft-check", "--json"]) == 0
    payload = out_json(capsys)
    assert payload["triggered"] is True
    assert payload["revert_language"] == 1
    assert "message" in payload


# ---------------------------------------------------------------------------
# c. churn: same file in >=4 of last 10 commits
# ---------------------------------------------------------------------------

def test_churn_boundary_four_of_ten_trips(repo, state, capsys):
    for i in range(10):
        name = "hot.py" if i % 3 == 0 else f"other{i}.py"  # hot.py at i=0,3,6,9 -> 4 hits
        _commit(repo, name, str(i), message=f"touch {i}")
    assert main(["draft-check"]) == 0
    out = capsys.readouterr().out
    assert "SCAR draft-check" in out
    assert "churn=1" in out


def test_churn_three_of_ten_does_not_trip(repo, state, capsys):
    for i in range(10):
        name = "hot.py" if i % 4 == 0 else f"other{i}.py"  # hot.py at i=0,4,8 -> 3 hits
        _commit(repo, name, str(i), message=f"touch {i}")
    assert main(["draft-check"]) == 0
    out = capsys.readouterr().out
    assert out == ""


# ---------------------------------------------------------------------------
# window: since last-check marker mtime, capped at 24h
# ---------------------------------------------------------------------------

def test_window_capped_at_24h_ignores_stale_marker(repo, state, capsys):
    """A marker mtime from 10 days ago must NOT reopen 10 days of history —
    the window is capped at 24h regardless of how stale the marker is."""
    from scar.draftcheck import lastcheck_marker

    now = int(time.time())
    old_marker_time = now - 10 * 86400
    outside_cap = now - 30 * 3600  # 30h ago: older than the 24h cap
    _commit_at(repo, "a.py", "1", "revert this old thing", outside_cap)

    marker = lastcheck_marker(state, repo)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    os.utime(marker, (old_marker_time, old_marker_time))

    assert main(["draft-check"]) == 0
    out = capsys.readouterr().out
    assert out == ""  # outside the 24h cap -> not seen


def test_window_since_marker_excludes_earlier_commits(repo, state, capsys):
    from scar.draftcheck import lastcheck_marker

    now = int(time.time())
    before_marker = now - 2 * 3600
    after_marker = now - 1800
    _commit_at(repo, "a.py", "1", "revert the earlier thing", before_marker)

    marker = lastcheck_marker(state, repo)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    marker_time = now - 3600
    os.utime(marker, (marker_time, marker_time))

    _commit_at(repo, "b.py", "1", "just a normal commit", after_marker)
    assert main(["draft-check"]) == 0
    out = capsys.readouterr().out
    assert out == ""  # only the pre-marker revert commit exists; excluded


# ---------------------------------------------------------------------------
# marker updates on every run
# ---------------------------------------------------------------------------

def test_lastcheck_marker_updates_on_every_run(repo, state, capsys):
    from scar.draftcheck import lastcheck_marker

    _commit(repo, "a.py", "1", message="implement feature")
    assert main(["draft-check"]) == 0
    marker = lastcheck_marker(state, repo)
    assert marker.exists()
    first_mtime = marker.stat().st_mtime

    time.sleep(0.05)
    assert main(["draft-check"]) == 0
    assert marker.stat().st_mtime >= first_mtime


# ---------------------------------------------------------------------------
# --from-hook throttle
# ---------------------------------------------------------------------------

def test_from_hook_throttles_repeat_nudges_within_an_hour(repo, state, capsys):
    _commit(repo, "a.py", "1", message="revert the change")
    assert main(["draft-check", "--from-hook"]) == 0
    first_out = capsys.readouterr().out
    assert "SCAR draft-check" in first_out

    # second commit still carries revert language in the window
    _commit(repo, "b.py", "1", message="revert this one too")
    assert main(["draft-check", "--from-hook"]) == 0
    second_out = capsys.readouterr().out
    assert second_out == ""  # throttled: silent


def test_direct_invocation_is_never_throttled(repo, state, capsys):
    _commit(repo, "a.py", "1", message="revert the change")
    assert main(["draft-check", "--from-hook"]) == 0
    capsys.readouterr()
    _commit(repo, "b.py", "1", message="revert this one too")
    assert main(["draft-check"]) == 0  # no --from-hook: throttle does not apply
    out = capsys.readouterr().out
    assert "SCAR draft-check" in out


# ---------------------------------------------------------------------------
# non-git dir: silent, never crashes
# ---------------------------------------------------------------------------

def test_non_git_dir_silent(tmp_path, state, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["draft-check"]) == 0
    assert capsys.readouterr().out == ""


def test_non_git_dir_json_silent(tmp_path, state, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["draft-check", "--json"]) == 0
    assert capsys.readouterr().out == ""


def test_fails_open_on_internal_error(repo, state, monkeypatch):
    """#117 mirrors #91's fail-open contract: whatever breaks internally
    (bad SCAR_STATE_DIR, a git surprise, anything) must still exit 0 — a
    hook can't gate a commit that already landed."""
    import scar.draftcheck as draftcheck

    def boom(*a, **k):
        raise RuntimeError("simulated internal failure")

    monkeypatch.setattr(draftcheck, "analyze", boom)
    _commit(repo, "a.py", "1", message="revert the change")
    assert main(["draft-check"]) == 0


# ---------------------------------------------------------------------------
# exit code always 0
# ---------------------------------------------------------------------------

def test_exit_code_always_zero_on_trip(repo, state):
    _commit(repo, "a.py", "1", message="revert the change")
    assert main(["draft-check"]) == 0


def test_exit_code_always_zero_on_clean(repo, state):
    _commit(repo, "a.py", "1", message="implement feature")
    assert main(["draft-check"]) == 0


def test_exit_code_always_zero_without_scars_dir(tmp_path, state, monkeypatch):
    _init_git(tmp_path)
    (tmp_path / "a.py").write_text("1")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "revert the change"],
                   check=True)
    monkeypatch.chdir(tmp_path)
    assert main(["draft-check"]) == 0


# ---------------------------------------------------------------------------
# contract text shape (mirrors stop_drafter, hooks.py ~194-225)
# ---------------------------------------------------------------------------

def test_contract_text_mirrors_stop_drafter_shape(repo, state, capsys):
    _commit(repo, "a.py", "1", message="revert the change")
    assert main(["draft-check"]) == 0
    out = capsys.readouterr().out
    assert "candidates" in out and "<=15 lines" in out
    assert "template.md" in out
    assert "fp-log.txt" in out
    assert "draft-check" in out  # source tag for the fp-log branch
    assert "status: candidate" in out
