"""Hook subcommands: harness payload on stdin -> hook JSON on stdout.

These replace the three standalone hook scripts; behavior parity is the
contract, single library code path is the point.
"""

import io
import json

import pytest

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
evidence:
  - commit: aaa1111
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
    return tmp_path


def feed(monkeypatch, payload: dict):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))


def out_json(capsys):
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


# --- precheck (PreToolUse) ---

def test_precheck_injects_matching_scar(repo, monkeypatch, capsys):
    feed(monkeypatch, {"tool_input": {"file_path": str(repo / "payments" / "retry.py"),
                                      "new_string": "time.sleep(3)"}})
    assert main(["hook", "precheck"]) == 0
    ctx = out_json(capsys)["hookSpecificOutput"]["additionalContext"]
    assert "Sleep is 7s" in ctx


def test_precheck_silent_outside_scars_repo(tmp_path, monkeypatch, capsys):
    (tmp_path / ".git").mkdir()
    feed(monkeypatch, {"tool_input": {"file_path": str(tmp_path / "x.py"),
                                      "new_string": "y"}})
    assert main(["hook", "precheck"]) == 0
    assert out_json(capsys) is None


def test_precheck_warns_on_unparseable_scar(repo, monkeypatch, capsys):
    (repo / ".scars" / "0002-bad.fence.md").write_text("# no frontmatter\n")
    feed(monkeypatch, {"tool_input": {"file_path": str(repo / "payments" / "x.py"),
                                      "new_string": ""}})
    main(["hook", "precheck"])
    ctx = out_json(capsys)["hookSpecificOutput"]["additionalContext"]
    assert "0002-bad.fence.md" in ctx and "NEVER fire" in ctx


def test_precheck_never_crashes_on_garbage_stdin(repo, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json at all"))
    assert main(["hook", "precheck"]) == 0
    assert out_json(capsys) is None


def test_hook_on_interactive_tty_explains_instead_of_hanging(repo, monkeypatch, capsys):
    """Found live: `scar hook precheck` in a terminal blocks forever waiting
    for stdin. A tty invocation must print a hint and exit immediately."""
    class FakeTty(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setattr("sys.stdin", FakeTty())
    assert main(["hook", "precheck"]) == 0
    out = capsys.readouterr().out
    assert "stdin" in out and "hookSpecificOutput" not in out


def test_session_notice_on_tty_hints_and_emits_nothing(repo, monkeypatch, capsys):
    """Found live: session-notice printed the hint THEN emitted JSON anyway
    via the cwd fallback. Tty means hint + stop — never mixed output."""
    class FakeTty(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setattr("sys.stdin", FakeTty())
    assert main(["hook", "session-notice"]) == 0
    out = capsys.readouterr().out
    assert "stdin" in out and "hookSpecificOutput" not in out


# --- session-notice (SessionStart) ---

def test_session_notice_announces_convention_with_counts(repo, monkeypatch, capsys):
    feed(monkeypatch, {"cwd": str(repo)})
    assert main(["hook", "session-notice"]) == 0
    payload = out_json(capsys)
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert "1 active" in ctx and "template.md" in ctx and "candidates/" in ctx


def test_session_notice_silent_without_scars_dir(tmp_path, monkeypatch, capsys):
    (tmp_path / ".git").mkdir()
    feed(monkeypatch, {"cwd": str(tmp_path)})
    assert main(["hook", "session-notice"]) == 0
    assert out_json(capsys) is None


# --- stop-drafter (Stop) ---

def transcript(tmp_path, lines):
    t = tmp_path / "t.jsonl"
    t.write_text("\n".join(json.dumps(x) for x in lines))
    return t


def test_stop_drafter_blocks_once_on_abandonment(repo, monkeypatch, capsys, tmp_path):
    state = tmp_path / "state"
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    t = transcript(tmp_path, [
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "That failed, reverting to the original."}]}},
        {"type": "user", "message": {"content": [
            {"type": "text", "text": "yeah that broke, go back"}]}},
    ])
    feed(monkeypatch, {"session_id": "s1", "transcript_path": str(t), "cwd": str(repo)})
    assert main(["hook", "stop-drafter"]) == 0
    payload = out_json(capsys)
    assert payload["decision"] == "block"
    assert "candidates" in payload["reason"] and "template.md" in payload["reason"]
    # second stop in same session: marker prevents refire
    feed(monkeypatch, {"session_id": "s1", "transcript_path": str(t), "cwd": str(repo)})
    assert main(["hook", "stop-drafter"]) == 0
    assert out_json(capsys) is None


def test_stop_drafter_respects_stop_hook_active(repo, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("SCAR_STATE_DIR", str(tmp_path / "state"))
    feed(monkeypatch, {"stop_hook_active": True, "session_id": "s2",
                       "transcript_path": "/nonexistent", "cwd": str(repo)})
    assert main(["hook", "stop-drafter"]) == 0
    assert out_json(capsys) is None


def test_stop_drafter_silent_on_calm_session(repo, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("SCAR_STATE_DIR", str(tmp_path / "state"))
    t = transcript(tmp_path, [
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Implemented the feature, tests pass."}]}},
    ])
    feed(monkeypatch, {"session_id": "s3", "transcript_path": str(t), "cwd": str(repo)})
    assert main(["hook", "stop-drafter"]) == 0
    assert out_json(capsys) is None


def test_stop_drafter_silent_on_error_storm_without_revert(repo, monkeypatch, capsys, tmp_path):
    """Gate 0.4 FP pattern: tool errors + edit thrash are normal debugging,
    not abandonment (3 of 4 field FPs entered through this path)."""
    monkeypatch.setenv("SCAR_STATE_DIR", str(tmp_path / "state"))
    lines = [{"type": "assistant", "message": {"content": [
        {"type": "tool_result", "is_error": True}]}} for _ in range(6)]
    lines += [{"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Edit",
         "input": {"file_path": "src/thing.py"}}]}} for _ in range(5)]
    t = transcript(tmp_path, lines)
    feed(monkeypatch, {"session_id": "s5", "transcript_path": str(t), "cwd": str(repo)})
    assert main(["hook", "stop-drafter"]) == 0
    assert out_json(capsys) is None


def test_stop_drafter_silent_on_user_corrections_alone(repo, monkeypatch, capsys, tmp_path):
    """Gate 0.4 FP pattern: user corrections without assistant revert language
    were policy denials and cwd misses, not abandoned approaches."""
    monkeypatch.setenv("SCAR_STATE_DIR", str(tmp_path / "state"))
    t = transcript(tmp_path, [
        {"type": "user", "message": {"content": [
            {"type": "text", "text": "that doesn't work"}]}},
        {"type": "user", "message": {"content": [
            {"type": "text", "text": "still broken, try again"}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Fixed — the path was wrong, corrected it."}]}},
    ])
    feed(monkeypatch, {"session_id": "s6", "transcript_path": str(t), "cwd": str(repo)})
    assert main(["hook", "stop-drafter"]) == 0
    assert out_json(capsys) is None


def test_stop_drafter_logs_firing(repo, monkeypatch, capsys, tmp_path):
    state = tmp_path / "state"
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    t = transcript(tmp_path, [
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "abandoning this approach entirely"}]}},
    ])
    feed(monkeypatch, {"session_id": "s4", "transcript_path": str(t), "cwd": str(repo)})
    main(["hook", "stop-drafter"])
    log = (state / "drafter-log.jsonl").read_text()
    assert "s4" in log and "revert_language" in log
