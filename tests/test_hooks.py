"""Hook subcommands: harness payload on stdin -> hook JSON on stdout.

These replace the three standalone hook scripts; behavior parity is the
contract, single library code path is the point.
"""

import io
import json
import time

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
    monkeypatch.setenv("SCAR_STATE_DIR", str(tmp_path / "state"))
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


# --- firing log (#106) ---

def test_precheck_logs_firing_when_scars_fire(repo, monkeypatch, capsys, tmp_path):
    state = tmp_path / "state"
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    feed(monkeypatch, {"tool_input": {"file_path": str(repo / "payments" / "retry.py"),
                                      "new_string": "time.sleep(3)"}})
    assert main(["hook", "precheck"]) == 0
    rec = json.loads((state / "firing-log.jsonl").read_text(encoding="utf-8").strip())
    assert rec["scar_ids"] == [1]
    assert rec["count"] == 1
    assert rec["repo"] == str(repo)
    assert "target" in rec and "ts" in rec


def test_precheck_logs_nothing_when_no_scars_fire(repo, monkeypatch, capsys, tmp_path):
    state = tmp_path / "state"
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    feed(monkeypatch, {"tool_input": {"file_path": str(repo / "unrelated" / "x.py"),
                                      "new_string": "pass"}})
    assert main(["hook", "precheck"]) == 0
    assert not (state / "firing-log.jsonl").exists()


def test_precheck_log_write_failure_fails_open(repo, monkeypatch, capsys, tmp_path):
    """#91: a broken log destination must never raise out of precheck or block
    the injection itself. SCAR_STATE_DIR points at a plain FILE (not a dir),
    so mkdir()-ing the log's parent fails with a real OSError."""
    state_stub = tmp_path / "not_a_dir"
    state_stub.write_text("blocker")
    monkeypatch.setenv("SCAR_STATE_DIR", str(state_stub))
    feed(monkeypatch, {"tool_input": {"file_path": str(repo / "payments" / "retry.py"),
                                      "new_string": "time.sleep(3)"}})
    assert main(["hook", "precheck"]) == 0        # no raise
    ctx = out_json(capsys)["hookSpecificOutput"]["additionalContext"]
    assert "Sleep is 7s" in ctx                    # injection still succeeds


def test_precheck_fails_open_on_internal_error(repo, monkeypatch, capsys):
    """#91.6: precheck must honor the never-fail contract. If the ranking/render
    path raises unexpectedly, it must fail OPEN — inject nothing, exit 0 — not
    propagate the exception and break the agent's edit."""
    import scar.hooks as hooks

    def boom(*a, **k):
        raise RuntimeError("simulated internal failure")

    monkeypatch.setattr(hooks, "rank_matches_for_edit", boom)
    feed(monkeypatch, {"tool_input": {"file_path": str(repo / "payments" / "retry.py"),
                                      "new_string": "time.sleep(3)"}})
    assert main(["hook", "precheck"]) == 0        # clean exit, no raise
    assert out_json(capsys) is None               # nothing injected


# --- precheck tiering (precision engine) ---

PATTERNED_FENCE = FENCE.replace(
    "anchors:\n  - path: payments/",
    'anchors:\n  - path: payments/\n  - pattern: "lower.{0,10}sleep"')


def test_path_only_match_demotes_to_one_liner(repo, monkeypatch, capsys):
    feed(monkeypatch, {"tool_input": {"file_path": str(repo / "payments" / "retry.py"),
                                      "new_string": "x = 1"}})
    assert main(["hook", "precheck"]) == 0
    ctx = out_json(capsys)["hookSpecificOutput"]["additionalContext"]
    assert "Sleep is 7s" in ctx                      # label line stays visible
    assert "path-only match" in ctx                  # demotion reason shown
    assert "Do not lower the sleep." not in ctx      # body demoted away


def test_content_pattern_match_gets_full_body(repo, monkeypatch, capsys):
    (repo / ".scars" / "0001-vendor.fence.md").write_text(PATTERNED_FENCE)
    feed(monkeypatch, {"tool_input": {"file_path": str(repo / "payments" / "retry.py"),
                                      "new_string": "lower the sleep to 3"}})
    assert main(["hook", "precheck"]) == 0
    ctx = out_json(capsys)["hookSpecificOutput"]["additionalContext"]
    assert "Do not lower the sleep." in ctx          # full body present


def test_firing_log_records_demoted_ids(repo, monkeypatch, capsys):
    feed(monkeypatch, {"tool_input": {"file_path": str(repo / "payments" / "retry.py"),
                                      "new_string": "x = 1"}})
    assert main(["hook", "precheck"]) == 0
    log = (repo / "state" / "firing-log.jsonl").read_text().strip().splitlines()
    rec = json.loads(log[-1])
    assert rec["demoted_ids"] == [1]
    assert rec["scar_ids"] == [1]


# --- repetition collapse (4h cooldown) ---

SECOND_FENCE = """\
---
id: 2
type: fence
title: Retry needs backoff
severity: critical
confidence: 0.9
created: 2026-06-09
authors: [mara]
anchors:
  - path: payments/
evidence:
  - commit: bbb2222
status: active
---

Do not retry without backoff.
"""


def _precheck_ctx(repo, monkeypatch, capsys, new_string):
    feed(monkeypatch, {"tool_input": {"file_path": str(repo / "payments" / "retry.py"),
                                      "new_string": new_string}})
    assert main(["hook", "precheck"]) == 0
    return out_json(capsys)["hookSpecificOutput"]["additionalContext"]


def test_recent_full_body_showing_collapses_to_one_liner(repo, monkeypatch, capsys):
    (repo / ".scars" / "0001-vendor.fence.md").write_text(PATTERNED_FENCE)
    first = _precheck_ctx(repo, monkeypatch, capsys, "lower the sleep to 3")
    assert "Do not lower the sleep." in first        # first: full body
    second = _precheck_ctx(repo, monkeypatch, capsys, "lower the sleep to 3")
    assert "Sleep is 7s" in second                   # still visible
    assert "already shown" in second                 # collapse reason
    assert "Do not lower the sleep." not in second   # body collapsed


def _write_log_line(repo, ts, scar_ids, demoted_ids):
    state = repo / "state"
    state.mkdir(parents=True, exist_ok=True)
    rec = {"ts": ts, "repo": str(repo),
           "target": str(repo / "payments" / "retry.py"),
           "scar_ids": scar_ids, "count": len(scar_ids),
           "demoted_ids": demoted_ids}
    with open(state / "firing-log.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def test_stale_showing_does_not_collapse(repo, monkeypatch, capsys):
    (repo / ".scars" / "0001-vendor.fence.md").write_text(PATTERNED_FENCE)
    stale = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 5 * 3600))
    _write_log_line(repo, stale, [1], [])
    ctx = _precheck_ctx(repo, monkeypatch, capsys, "lower the sleep to 3")
    assert "Do not lower the sleep." in ctx          # 5h old — outside window


def test_demoted_showing_does_not_suppress_later_content_match(repo, monkeypatch, capsys):
    (repo / ".scars" / "0001-vendor.fence.md").write_text(PATTERNED_FENCE)
    fresh = time.strftime("%Y-%m-%dT%H:%M:%S")
    _write_log_line(repo, fresh, [1], [1])           # shown only as one-liner
    ctx = _precheck_ctx(repo, monkeypatch, capsys, "lower the sleep to 3")
    assert "Do not lower the sleep." in ctx          # one-liner != seen body


def test_malformed_firing_log_timestamp_does_not_abort_precheck(repo, monkeypatch, capsys):
    """If a firing-log line has ts=null or non-string ts, _recently_fired must
    skip that record (not abort with TypeError), so precheck still injects scars."""
    (repo / ".scars" / "0001-vendor.fence.md").write_text(PATTERNED_FENCE)
    # Write a malformed log line with ts=None
    state = repo / "state"
    state.mkdir(parents=True, exist_ok=True)
    rec = {"ts": None, "repo": str(repo),
           "target": str(repo / "payments" / "retry.py"),
           "scar_ids": [1], "count": 1, "demoted_ids": []}
    with open(state / "firing-log.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    # Run precheck with a content-signal match
    ctx = _precheck_ctx(repo, monkeypatch, capsys, "lower the sleep to 3")
    # The full body must still be injected (not suppressed by the TypeError)
    assert "Do not lower the sleep." in ctx


def test_non_dict_firing_log_line_does_not_abort_precheck(repo, monkeypatch, capsys):
    """A firing-log line that is syntactically valid JSON but not a dict (bare
    `null` or `[]`) parses fine via json.loads, but rec.get("repo") then raises
    AttributeError (None/list has no .get). That exception escapes the narrow
    per-step except clauses in _recently_fired, propagates into precheck()'s
    outer `except Exception: return 0`, and fails precheck open (injects
    nothing) — permanently, since the bad line never leaves the log. Each
    per-line iteration must be guarded so one malformed line is skipped, not
    fatal."""
    (repo / ".scars" / "0001-vendor.fence.md").write_text(PATTERNED_FENCE)
    state = repo / "state"
    state.mkdir(parents=True, exist_ok=True)
    with open(state / "firing-log.jsonl", "a", encoding="utf-8") as fh:
        fh.write("null\n")
        fh.write("[]\n")
    ctx = _precheck_ctx(repo, monkeypatch, capsys, "lower the sleep to 3")
    assert "Do not lower the sleep." in ctx


def test_mixed_full_and_demoted_showing_logs_strict_subset(repo, monkeypatch, capsys):
    (repo / ".scars" / "0001-vendor.fence.md").write_text(PATTERNED_FENCE)
    (repo / ".scars" / "0002-retry.fence.md").write_text(SECOND_FENCE)
    ctx = _precheck_ctx(repo, monkeypatch, capsys, "lower the sleep to 3")
    assert "Do not lower the sleep." in ctx           # scar 1: content signal, full body
    assert "Retry needs backoff" in ctx               # scar 2: label visible
    assert "Do not retry without backoff." not in ctx  # scar 2: body demoted (path-only)
    log = (repo / "state" / "firing-log.jsonl").read_text().strip().splitlines()
    rec = json.loads(log[-1])
    assert rec["scar_ids"] == [1, 2]
    assert rec["demoted_ids"] == [2]
    assert set(rec["demoted_ids"]) < set(rec["scar_ids"])
