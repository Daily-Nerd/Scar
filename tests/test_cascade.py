"""Windsurf/Cascade hook adapter: Cascade JSON on stdin -> exit code out.

Cascade has no additionalContext equivalent — the only channel into the
agent's context is a blocking exit, so injection here is block-once. These
tests pin the two halves that make that safe: the block fires exactly once
per (trajectory, scar, target), and nothing about a Cascade payload can ever
bounce an edit twice or crash the editor.
"""

import io
import json
import time

import pytest

from scar import cascade
from scar.cli import main
from scar.store import init_scars

FENCE = """\
---
id: 1
type: fence
title: Sleep is 7s for vendor window
severity: critical
confidence: 0.9
created: 2026-06-09
authors: [mara]
anchors:
  - path: payments/
  - pattern: "lower.{0,10}sleep"
evidence:
  - commit: aaa1111
status: active
---

Do not lower the sleep. The vendor window is 7s and anything shorter drops
the settlement callback on the floor.
"""

COMMAND_SCAR = """\
---
id: 2
type: deadend
title: Bare uv sync strips extras
severity: high
confidence: 0.9
created: 2026-07-30
authors: ["kib"]
anchors:
  - command: "uv sync(?!.* --all-extras)"
evidence:
  - issue: 175
status: active
---

Always run uv sync --all-extras.
"""

VIOLATION_FENCE = r"""---
id: 9
type: fence
title: Sleep cap must stay under 6s
severity: critical
confidence: 0.9
created: 2026-06-09
authors: [mara]
anchors:
  - path: payments/
evidence:
  - commit: ddd4444
violation: "sleep\((?:[0-6])\)"
status: active
---

Do not lower the sleep.
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    init_scars(tmp_path)
    (tmp_path / ".scars" / "0001-vendor.fence.md").write_text(FENCE)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCAR_STATE_DIR", str(tmp_path / "state"))
    return tmp_path


def feed(monkeypatch, payload: dict):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))


def write_payload(repo, new_string: str, trajectory: str = "t1",
                  rel: str = "payments/retry.py") -> dict:
    return {"agent_action_name": "pre_write_code", "trajectory_id": trajectory,
            "tool_info": {"file_path": str(repo / rel),
                          "edits": [{"old_string": "x = 1",
                                     "new_string": new_string}]}}


def command_payload(repo, command: str, trajectory: str = "t1") -> dict:
    return {"agent_action_name": "pre_run_command", "trajectory_id": trajectory,
            "tool_info": {"command_line": command, "cwd": str(repo)}}


# --- pre_write_code: block once, then let the retry through ---

def test_pre_write_code_blocks_on_content_signal(repo, monkeypatch, capsys):
    feed(monkeypatch, write_payload(repo, "lower the sleep to 3"))
    assert main(["cascade-hook"]) == cascade.BLOCK_EXIT
    err = capsys.readouterr().err
    assert "Sleep is 7s for vendor window" in err
    assert "Do not lower the sleep." in err


def test_second_identical_action_proceeds(repo, monkeypatch, capsys):
    feed(monkeypatch, write_payload(repo, "lower the sleep to 3"))
    assert main(["cascade-hook"]) == cascade.BLOCK_EXIT
    capsys.readouterr()
    feed(monkeypatch, write_payload(repo, "lower the sleep to 3"))
    assert main(["cascade-hook"]) == 0
    assert capsys.readouterr().err == ""


def test_new_trajectory_blocks_again(repo, monkeypatch, capsys):
    feed(monkeypatch, write_payload(repo, "lower the sleep to 3", trajectory="t1"))
    assert main(["cascade-hook"]) == cascade.BLOCK_EXIT
    capsys.readouterr()
    feed(monkeypatch, write_payload(repo, "lower the sleep to 3", trajectory="t2"))
    assert main(["cascade-hook"]) == cascade.BLOCK_EXIT


def test_expired_fire_state_blocks_again(repo, monkeypatch, capsys):
    feed(monkeypatch, write_payload(repo, "lower the sleep to 3"))
    assert main(["cascade-hook"]) == cascade.BLOCK_EXIT
    capsys.readouterr()
    state_path = cascade.fire_state_path()
    stale = {k: v - cascade.FIRE_TTL_SECONDS - 60
             for k, v in json.loads(state_path.read_text(encoding="utf-8")).items()}
    state_path.write_text(json.dumps(stale), encoding="utf-8")
    feed(monkeypatch, write_payload(repo, "lower the sleep to 3"))
    assert main(["cascade-hook"]) == cascade.BLOCK_EXIT


def test_path_only_match_never_blocks(repo, monkeypatch, capsys):
    """Precision bar here is higher than the Claude tier: a path-proximity
    match proves the file, not the act, and a false block cancels a
    user-visible action. Those go to the UI (stdout) and never block."""
    feed(monkeypatch, write_payload(repo, "x = 1"))
    assert main(["cascade-hook"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Sleep is 7s for vendor window" in captured.out


def test_unmatched_edit_is_silent(repo, monkeypatch, capsys):
    feed(monkeypatch, write_payload(repo, "x = 1", rel="unrelated/x.py"))
    assert main(["cascade-hook"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_stderr_stays_compact(repo, monkeypatch, capsys):
    """The conservative tier: one rule line per scar, never the full body —
    stderr renders as an error in the Cascade UI."""
    feed(monkeypatch, write_payload(repo, "lower the sleep to 3"))
    main(["cascade-hook"])
    err = capsys.readouterr().err
    assert "settlement callback on the floor" not in err
    assert len(err) < 600


# --- pre_run_command ---

def test_pre_run_command_blocks_on_command_anchor(repo, monkeypatch, capsys):
    (repo / ".scars" / "0002-uv-sync.deadend.md").write_text(COMMAND_SCAR)
    feed(monkeypatch, command_payload(repo, "uv sync"))
    assert main(["cascade-hook"]) == cascade.BLOCK_EXIT
    err = capsys.readouterr().err
    assert "Bare uv sync strips extras" in err


def test_pre_run_command_second_run_proceeds(repo, monkeypatch, capsys):
    (repo / ".scars" / "0002-uv-sync.deadend.md").write_text(COMMAND_SCAR)
    feed(monkeypatch, command_payload(repo, "uv sync"))
    assert main(["cascade-hook"]) == cascade.BLOCK_EXIT
    capsys.readouterr()
    feed(monkeypatch, command_payload(repo, "uv sync"))
    assert main(["cascade-hook"]) == 0


def test_pre_run_command_silent_on_innocent_command(repo, monkeypatch, capsys):
    (repo / ".scars" / "0002-uv-sync.deadend.md").write_text(COMMAND_SCAR)
    feed(monkeypatch, command_payload(repo, "uv sync --all-extras"))
    assert main(["cascade-hook"]) == 0
    assert capsys.readouterr().err == ""


# --- post_write_code (violation tripwire; post-hooks can never block) ---

def test_post_write_code_reports_violation_without_blocking(repo, monkeypatch, capsys):
    (repo / ".scars" / "0009-sleep.fence.md").write_text(VIOLATION_FENCE)
    feed(monkeypatch, {"agent_action_name": "post_write_code", "trajectory_id": "t1",
                       "tool_info": {"file_path": str(repo / "payments" / "retry.py"),
                                     "edits": [{"old_string": "a",
                                                "new_string": "time.sleep(3)"}]}})
    assert main(["cascade-hook"]) == 0
    assert "Sleep cap must stay under 6s" in capsys.readouterr().out


def test_post_write_code_logs_violation(repo, monkeypatch, capsys):
    (repo / ".scars" / "0009-sleep.fence.md").write_text(VIOLATION_FENCE)
    feed(monkeypatch, {"agent_action_name": "post_write_code", "trajectory_id": "t1",
                       "tool_info": {"file_path": str(repo / "payments" / "retry.py"),
                                     "edits": [{"old_string": "a",
                                                "new_string": "time.sleep(3)"}]}})
    main(["cascade-hook"])
    rec = json.loads((repo / "state" / "firing-log.jsonl")
                     .read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["violation_ids"] == [9]
    assert rec["runtime"] == "windsurf"


# --- firing log parity (#106) ---

def test_block_is_recorded_in_the_firing_log(repo, monkeypatch, capsys):
    feed(monkeypatch, write_payload(repo, "lower the sleep to 3"))
    main(["cascade-hook"])
    rec = json.loads((repo / "state" / "firing-log.jsonl")
                     .read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["scar_ids"] == [1]
    assert rec["repo"] == str(repo)
    assert rec["runtime"] == "windsurf"
    assert "ts" in rec and "target" in rec


def test_ui_only_showings_are_not_logged_as_demoted(repo, monkeypatch, capsys):
    """`demoted_ids` means 'shown to the model, collapsed to one line' and
    stays a subset of scar_ids. A Cascade stdout line reaches the human only,
    so it belongs in neither field."""
    (repo / ".scars" / "0002-uv-sync.deadend.md").write_text(
        COMMAND_SCAR.replace("  - command:", "  - path: payments/\n  # - command:"))
    feed(monkeypatch, write_payload(repo, "lower the sleep to 3"))
    main(["cascade-hook"])
    assert "Bare uv sync strips extras" in capsys.readouterr().out  # UI-only
    rec = json.loads((repo / "state" / "firing-log.jsonl")
                     .read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["scar_ids"] == [1]
    assert rec["demoted_ids"] == []


def test_nothing_surfaced_logs_nothing(repo, monkeypatch, capsys):
    """A path-only match reaches the human, never the model — logging it as a
    firing would inflate the firing count with showings the agent never saw."""
    feed(monkeypatch, write_payload(repo, "x = 1"))
    main(["cascade-hook"])
    assert not (repo / "state" / "firing-log.jsonl").exists()


def test_stats_counts_a_cascade_firing(repo, monkeypatch, capsys):
    feed(monkeypatch, write_payload(repo, "lower the sleep to 3"))
    main(["cascade-hook"])
    capsys.readouterr()
    assert main(["stats", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total_firings"] == 1
    assert data["per_scar"][0]["id"] == 1


# --- never break the user's editor ---

def test_garbage_stdin_proceeds(repo, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json at all"))
    assert main(["cascade-hook"]) == 0
    assert capsys.readouterr().err == ""


def test_empty_payload_proceeds(repo, monkeypatch, capsys):
    feed(monkeypatch, {})
    assert main(["cascade-hook"]) == 0


def test_unknown_event_proceeds(repo, monkeypatch, capsys):
    feed(monkeypatch, {"agent_action_name": "pre_read_code",
                       "tool_info": {"file_path": str(repo / "payments" / "x.py")}})
    assert main(["cascade-hook"]) == 0


def test_missing_tool_info_proceeds(repo, monkeypatch, capsys):
    feed(monkeypatch, {"agent_action_name": "pre_write_code", "trajectory_id": "t1"})
    assert main(["cascade-hook"]) == 0


def test_outside_a_scars_repo_proceeds(tmp_path, monkeypatch, capsys):
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("SCAR_STATE_DIR", str(tmp_path / "state"))
    feed(monkeypatch, {"agent_action_name": "pre_write_code", "trajectory_id": "t1",
                       "tool_info": {"file_path": str(tmp_path / "x.py"),
                                     "edits": [{"new_string": "lower the sleep"}]}})
    assert main(["cascade-hook"]) == 0


def test_internal_error_proceeds(repo, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("simulated internal failure")

    monkeypatch.setattr(cascade, "rank_matches_for_edit", boom)
    feed(monkeypatch, write_payload(repo, "lower the sleep to 3"))
    assert main(["cascade-hook"]) == 0
    assert capsys.readouterr().err == ""


def test_unrecordable_fire_state_never_blocks(repo, monkeypatch, capsys):
    """Blocking without being able to record the fire would bounce the same
    action forever — a retry that can never pass. Only block when the fire
    is durably recorded."""
    monkeypatch.setattr(cascade, "_remember_fired", lambda keys: False)
    feed(monkeypatch, write_payload(repo, "lower the sleep to 3"))
    assert main(["cascade-hook"]) == 0


def test_tty_invocation_hints_instead_of_hanging(repo, monkeypatch, capsys):
    class FakeTty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr("sys.stdin", FakeTty())
    assert main(["cascade-hook"]) == 0
    assert "stdin" in capsys.readouterr().out


# --- fire state hygiene (no session-end signal to clean up on) ---

def test_fire_state_is_size_capped(repo, monkeypatch):
    now = time.time()
    cascade.fire_state_path().parent.mkdir(parents=True, exist_ok=True)
    cascade.fire_state_path().write_text(json.dumps(
        {f"t1|{i}|f.py": now - i for i in range(cascade.FIRE_STATE_MAX + 200)}),
        encoding="utf-8")
    assert cascade._remember_fired(["t1|new|f.py"])
    kept = json.loads(cascade.fire_state_path().read_text(encoding="utf-8"))
    assert len(kept) <= cascade.FIRE_STATE_MAX
    assert "t1|new|f.py" in kept


def test_expired_entries_are_dropped_on_write(repo, monkeypatch):
    now = time.time()
    cascade.fire_state_path().parent.mkdir(parents=True, exist_ok=True)
    cascade.fire_state_path().write_text(json.dumps(
        {"t1|1|old.py": now - cascade.FIRE_TTL_SECONDS - 1}), encoding="utf-8")
    cascade._remember_fired(["t1|1|new.py"])
    kept = json.loads(cascade.fire_state_path().read_text(encoding="utf-8"))
    assert "t1|1|old.py" not in kept and "t1|1|new.py" in kept


def test_corrupt_fire_state_does_not_crash(repo, monkeypatch, capsys):
    cascade.fire_state_path().parent.mkdir(parents=True, exist_ok=True)
    cascade.fire_state_path().write_text("{not json", encoding="utf-8")
    feed(monkeypatch, write_payload(repo, "lower the sleep to 3"))
    assert main(["cascade-hook"]) == cascade.BLOCK_EXIT
