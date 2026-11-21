"""Internal hook failures are recorded, never discarded (#420).

Hooks fail open: an internal error exits 0 so the user's edit or commit is
never blocked. The failure itself used to vanish with it, which made a hook
that crashes on every run look exactly like a hook with nothing to say.
"""
import ast
import io
import json
import subprocess
from pathlib import Path

import pytest

from scar import cascade, codex, hooks
from scar.cli import main
from scar.store import init_scars

SRC = Path(__file__).resolve().parent.parent / "src" / "scar"


def boom(*_a, **_k):
    raise RuntimeError("simulated internal failure")


def _stdin(monkeypatch, payload):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))


def _failures():
    path = hooks.hook_failures_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    init_scars(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Each in-scope handler: exit 0, exactly one record with its own name
# ---------------------------------------------------------------------------

EDIT = {"tool_input": {"file_path": "/tmp/x.py", "content": "x"}}
COMMAND = {"tool_input": {"command": "ls"}}


def _precheck(monkeypatch):
    monkeypatch.setattr(hooks.ScarStore, "discover", boom)
    _stdin(monkeypatch, EDIT)
    return hooks.precheck()


def _posttool(monkeypatch):
    monkeypatch.setattr(hooks.ScarStore, "discover", boom)
    _stdin(monkeypatch, EDIT)
    return hooks.posttool()


def _precheck_command(monkeypatch):
    monkeypatch.setattr(hooks.ScarStore, "discover", boom)
    _stdin(monkeypatch, COMMAND)
    return hooks.precheck_command()


def _codex_pretool(monkeypatch):
    monkeypatch.setattr(codex, "_targets", boom)
    _stdin(monkeypatch, {"tool_name": "apply_patch", "tool_input": {}})
    return codex.pretool()


def _codex_posttool(monkeypatch):
    monkeypatch.setattr(codex, "_targets", boom)
    _stdin(monkeypatch, {"tool_name": "apply_patch",
                         "tool_response": {"exit_code": 0}})
    return codex.posttool()


def _codex_session_notice(monkeypatch):
    monkeypatch.setattr(codex, "_session_notice_payload", boom)
    _stdin(monkeypatch, {})
    return codex.session_notice()


def _cascade(monkeypatch):
    monkeypatch.setitem(cascade.EVENTS, "pre_write_code", boom)
    _stdin(monkeypatch, {"agent_action_name": "pre_write_code"})
    return cascade.cascade_hook()


HANDLERS = [
    ("precheck", _precheck),
    ("posttool", _posttool),
    ("precheck-command", _precheck_command),
    ("codex-pretool", _codex_pretool),
    ("codex-posttool", _codex_posttool),
    ("codex-session-notice", _codex_session_notice),
    ("cascade-hook", _cascade),
]


@pytest.mark.parametrize("name,drive", HANDLERS, ids=[n for n, _ in HANDLERS])
def test_handler_fails_open_and_records_one_line(name, drive, repo, monkeypatch, capsys):
    assert drive(monkeypatch) == 0
    rows = _failures()
    assert len(rows) == 1
    assert rows[0]["hook"] == name
    assert rows[0]["exc_type"] == "RuntimeError"
    assert "simulated internal failure" in rows[0]["message"]
    assert rows[0]["ts"]
    assert rows[0]["repo"]
    assert capsys.readouterr().out == ""


def test_firing_log_writer_records_its_own_failure(repo, monkeypatch):
    from scar.store import ScarStore
    monkeypatch.setattr(hooks, "firing_log_path", boom)
    hooks._log_firing(ScarStore(repo, repo / ".scars"), "x.py", [])
    rows = _failures()
    assert [(r["hook"], r["exc_type"]) for r in rows] == [("firing-log", "RuntimeError")]


def test_violation_log_writer_records_its_own_failure(repo, monkeypatch):
    from scar.store import ScarStore
    monkeypatch.setattr(hooks, "firing_log_path", boom)
    hooks._log_violation_firing(ScarStore(repo, repo / ".scars"), "x.py", [])
    rows = _failures()
    assert [(r["hook"], r["exc_type"]) for r in rows] == [("violation-log", "RuntimeError")]


def test_draft_check_fails_open_and_records(repo, monkeypatch):
    import scar.draftcheck as draftcheck
    monkeypatch.setattr(draftcheck, "analyze", boom)
    assert main(["draft-check", "--from-hook"]) == 0
    rows = _failures()
    assert [(r["hook"], r["exc_type"]) for r in rows] == [("draft-check", "RuntimeError")]


# ---------------------------------------------------------------------------
# The recorder itself
# ---------------------------------------------------------------------------

def test_recorder_never_raises_on_an_unusable_state_dir(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("not a dir")
    monkeypatch.setenv("SCAR_STATE_DIR", str(blocker / "sub"))
    hooks.record_hook_failure("precheck", RuntimeError("x"))  # must not raise


def test_a_failing_handler_still_exits_zero_with_an_unusable_state_dir(
        tmp_path, repo, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("not a dir")
    monkeypatch.setenv("SCAR_STATE_DIR", str(blocker / "sub"))
    assert _precheck(monkeypatch) == 0


def test_recorder_never_raises_on_a_hostile_exception(monkeypatch):
    class Hostile(Exception):
        def __str__(self):
            raise ValueError("str() is broken")

    hooks.record_hook_failure("precheck", Hostile())  # must not raise


def test_message_is_truncated_and_stays_one_line(repo):
    hooks.record_hook_failure("precheck", RuntimeError("a\nb" + "z" * 5000))
    (row,) = _failures()
    assert len(row["message"]) <= hooks.HOOK_FAILURE_MESSAGE_MAX
    assert "\n" not in json.dumps(row)


def test_size_cap_keeps_the_newest_half(repo, monkeypatch):
    monkeypatch.setattr(hooks, "HOOK_FAILURE_LOG_MAX_BYTES", 2000)
    for i in range(60):
        hooks.record_hook_failure("precheck", RuntimeError(f"failure number {i}"))
    path = hooks.hook_failures_path()
    assert path.stat().st_size <= 2000
    rows = _failures()
    assert rows[-1]["message"] == "failure number 59"
    assert 0 < len(rows) < 60
    numbers = [int(r["message"].rsplit(" ", 1)[1]) for r in rows]
    assert numbers == sorted(numbers)
    assert all(isinstance(r, dict) for r in rows)  # no half-cut line survives


def test_below_the_cap_nothing_is_dropped(repo):
    for i in range(5):
        hooks.record_hook_failure("precheck", RuntimeError(f"n {i}"))
    assert len(_failures()) == 5


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------

def test_doctor_reports_a_recorded_failure_per_hook(repo, capsys):
    hooks.record_hook_failure("precheck", RuntimeError("first"))
    hooks.record_hook_failure("precheck", KeyError("second"))
    hooks.record_hook_failure("posttool", ValueError("other"))
    assert main(["agent", "doctor"]) == 0
    out = capsys.readouterr().out
    assert "precheck: 2 internal failure(s)" in out
    assert "KeyError" in out and "second" in out
    assert "posttool: 1 internal failure(s)" in out
    assert "ValueError" in out


def test_doctor_prints_nothing_extra_without_a_file(repo, capsys):
    from scar.agent import doctor
    assert not hooks.hook_failures_path().exists()
    assert not any("internal failure" in ln for ln in doctor(repo))


def test_doctor_prints_nothing_extra_for_an_empty_file(repo):
    from scar.agent import doctor
    hooks.hook_failures_path().write_text("")
    assert not any("internal failure" in ln for ln in doctor(repo))


def test_doctor_survives_garbage_lines(repo):
    from scar.agent import doctor
    hooks.hook_failures_path().write_text("not json\n[]\n{\"hook\": 3}\n")
    doctor(repo)  # must not raise


def test_doctor_exit_code_does_not_change(repo, capsys):
    hooks.record_hook_failure("precheck", RuntimeError("x"))
    assert main(["agent", "doctor"]) == 0


def test_doctor_shows_the_age_of_the_last_failure(repo, capsys):
    hooks.record_hook_failure("precheck", RuntimeError("fresh"))
    assert main(["agent", "doctor"]) == 0
    assert "last 0 day(s) ago" in capsys.readouterr().out


def test_doctor_age_counts_days_from_the_last_row(repo):
    from datetime import datetime, timedelta
    from scar.agent import doctor
    old = (datetime.now() - timedelta(days=3, hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    hooks.hook_failures_path().write_text(json.dumps(
        {"ts": old, "hook": "posttool", "exc_type": "KeyError", "message": "m"}) + "\n")
    (line,) = [ln for ln in doctor(repo) if "internal failure" in ln]
    assert "last 3 day(s) ago" in line
    assert "internal failure(s)" in line  # wording the doctor colouring matches


def test_doctor_without_a_readable_timestamp_still_lists_the_failure(repo):
    from scar.agent import doctor
    hooks.hook_failures_path().write_text(json.dumps(
        {"ts": "garbage", "hook": "posttool", "exc_type": "KeyError", "message": "m"}) + "\n")
    (line,) = [ln for ln in doctor(repo) if "internal failure" in ln]
    assert "day(s) ago" not in line
    assert "KeyError" in line


# ---------------------------------------------------------------------------
# The dispatcher fails open for every kind (#428)
# ---------------------------------------------------------------------------

def _all_kinds():
    return ([(k, hooks.HANDLERS) for k in hooks.HANDLERS]
            + [(k, codex.HANDLERS) for k in codex.HANDLERS])


KINDS = _all_kinds()


@pytest.mark.parametrize("kind,table", KINDS, ids=[k for k, _ in KINDS])
def test_dispatcher_converts_an_internal_error_to_exit_zero(
        kind, table, repo, monkeypatch, capsys):
    monkeypatch.setitem(table, kind, boom)
    assert main(["hook", kind]) == 0
    (row,) = _failures()
    assert row["hook"] == kind
    assert row["exc_type"] == "RuntimeError"
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("kind,table", KINDS, ids=[k for k, _ in KINDS])
@pytest.mark.parametrize("payload", [[1, 2], "text", 7],
                         ids=["list", "string", "number"])
def test_a_non_object_payload_exits_zero_and_records_one_line_for_every_kind(
        kind, table, payload, repo, monkeypatch):
    _stdin(monkeypatch, payload)
    assert main(["hook", kind]) == 0
    assert [r["hook"] for r in _failures()] == [kind]


def test_a_non_string_cwd_exits_zero_and_is_recorded(repo, monkeypatch):
    _stdin(monkeypatch, {"cwd": 12})
    assert main(["hook", "session-notice"]) == 0
    assert [r["hook"] for r in _failures()] == ["session-notice"]


def test_a_scar_file_with_invalid_utf8_does_not_break_session_start(
        repo, monkeypatch, capsys):
    (repo / ".scars" / "0001-bad.md").write_bytes(b"---\ntitle: \xff\xfe\n---\n")
    _stdin(monkeypatch, {"cwd": str(repo)})
    assert main(["hook", "session-notice"]) == 0
    assert [r["hook"] for r in _failures()] == ["session-notice"]


def test_a_transcript_line_that_is_a_json_scalar_does_not_break_stop_drafter(
        repo, monkeypatch):
    transcript = repo / "t.jsonl"
    transcript.write_text("7\n[1]\n")
    _stdin(monkeypatch, {"cwd": str(repo), "session_id": "s1",
                         "transcript_path": str(transcript)})
    assert main(["hook", "stop-drafter"]) == 0


def test_an_unwritable_state_dir_does_not_break_stop_drafter(
        tmp_path, repo, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("not a dir")
    monkeypatch.setenv("SCAR_STATE_DIR", str(blocker / "sub"))
    transcript = repo / "t.jsonl"
    transcript.write_text(json.dumps({"type": "assistant", "message": {
        "content": "I'll revert that, it did not work"}}) + "\n")
    _stdin(monkeypatch, {"cwd": str(repo), "session_id": "s2",
                         "transcript_path": str(transcript)})
    assert main(["hook", "stop-drafter"]) == 0


def test_a_deliberate_nonzero_exit_is_not_turned_into_zero(repo, monkeypatch):
    monkeypatch.setitem(hooks.HANDLERS, "precheck", lambda: 2)
    assert main(["hook", "precheck"]) == 2
    assert _failures() == []


def test_the_dispatcher_does_not_swallow_keyboard_interrupt(repo, monkeypatch):
    def interrupted():
        raise KeyboardInterrupt
    monkeypatch.setitem(hooks.HANDLERS, "precheck", interrupted)
    with pytest.raises(KeyboardInterrupt):
        main(["hook", "precheck"])
    assert _failures() == []


# ---------------------------------------------------------------------------
# draft-check by hand speaks on stderr; from the hook it stays silent
# ---------------------------------------------------------------------------

def test_draft_check_by_hand_prints_one_stderr_line(repo, monkeypatch, capsys):
    import scar.draftcheck as draftcheck
    monkeypatch.setattr(draftcheck, "analyze", boom)
    assert main(["draft-check"]) == 0
    cap = capsys.readouterr()
    assert cap.out == ""
    lines = cap.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("scar draft-check: internal error (RuntimeError: ")
    assert str(hooks.hook_failures_path()) in lines[0]
    assert "exit code stays 0" in lines[0]


def test_draft_check_from_hook_prints_nothing(repo, monkeypatch, capsys):
    import scar.draftcheck as draftcheck
    monkeypatch.setattr(draftcheck, "analyze", boom)
    assert main(["draft-check", "--from-hook"]) == 0
    cap = capsys.readouterr()
    assert (cap.out, cap.err) == ("", "")


# ---------------------------------------------------------------------------
# Structural guard: no silent catch-all can appear
# ---------------------------------------------------------------------------

# Every other catch-all in the package is a narrow, expected fallback or a
# non-hook helper. Pinned by (module, function) so a NEW silent handler fails
# this test until someone classifies it on purpose.
OUT_OF_SCOPE = {
    ("cascade.py", "_remember_fired"),    # block-once state write, returns False
    ("cli.py", "_firing_reviews"),        # status/lint helper, "nothing to escalate"
    ("cli.py", "_stdin_is_a_terminal"),   # capability probe
    ("cli.py", "_dedup_key"),             # unparseable log row, skipped
    ("cli.py", "_parse_window_bound"),    # bad date text -> None
    ("cli.py", "_filter_window"),         # undated rows are counted, not lost
    ("firing_log.py", "read_firing_log_records"),  # blanket per-line reader (scar 12)
    ("gc.py", "truncate_firing_log"),     # cleans the temp file, then re-raises
    ("hooks.py", "_context_bytes"),       # optional field, omitted when unknown
    ("hooks.py", "repo_key"),             # path fallback when resolve fails
    ("hooks.py", "_recently_fired"),      # blanket per-line reader
    ("hooks.py", "_read_hook_failures"),  # blanket per-line reader of this very log
    ("hooks.py", "record_hook_failure"),  # the innermost guard, must not raise
    ("mcp.py", "_version"),               # cosmetic
    ("mcp.py", "serve"),                  # error is returned to the client
    ("orphan.py", "detect_revivals"),     # per-scar skip in an analysis loop
    ("orphan.py", "detect_orphans"),
    ("orphan.py", "detect_partial_rot"),
    ("orphan.py", "detect_symbol_drift"),
    ("store.py", "write_active_scar"),    # cleans the temp file, then re-raises
    ("symbols.py", "_language"),         # optional dependency probe
}


def _catch_alls(source):
    """Yield (function, handler) for every except Exception, BaseException or
    bare except in the source."""
    def walk(node, fn):
        for child in ast.iter_child_nodes(node):
            inner = (child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                     else fn)
            if isinstance(child, ast.ExceptHandler):
                names = ast.unparse(child.type) if child.type else "bare"
                if "Exception" in names or names == "bare":
                    yield fn, child
            yield from walk(child, inner)
    yield from walk(ast.parse(source), "<module>")


def _records(handler):
    return any(isinstance(n, ast.Call)
               and (getattr(n.func, "id", None) == "record_hook_failure"
                    or getattr(n.func, "attr", None) == "record_hook_failure")
               for n in ast.walk(handler))


def _unclassified(module, source):
    return [(module, fn, h.lineno) for fn, h in _catch_alls(source)
            if (module, fn) not in OUT_OF_SCOPE and not _records(h)]


def test_every_catch_all_records_or_is_pinned_out_of_scope():
    problems = []
    for path in sorted(SRC.glob("*.py")):
        problems += _unclassified(path.name, path.read_text(encoding="utf-8"))
    assert problems == []


def test_the_pinned_list_has_no_stale_entries():
    live = {(p.name, fn)
            for p in SRC.glob("*.py")
            for fn, _h in _catch_alls(p.read_text(encoding="utf-8"))}
    assert OUT_OF_SCOPE - live == set()


def test_the_guard_bites_on_a_silent_handler():
    bad = (
        "def newhook():\n"
        "    try:\n"
        "        work()\n"
        "    except Exception:\n"
        "        return 0\n"
    )
    assert _unclassified("hooks.py", bad) == [("hooks.py", "newhook", 4)]


def test_the_guard_passes_a_recording_handler():
    good = (
        "def newhook():\n"
        "    try:\n"
        "        work()\n"
        "    except Exception as exc:\n"
        "        record_hook_failure('newhook', exc)\n"
        "        return 0\n"
    )
    assert _unclassified("hooks.py", good) == []
