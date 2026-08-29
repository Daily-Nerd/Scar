"""Native Codex hook payloads and plugin-facing hook commands (#243)."""

import io
import json

import pytest

from scar.cli import main
from scar.codex import PatchTarget, parse_apply_patch
from scar.store import init_scars

FENCE = r"""---
id: 1
type: fence
title: Sleep is 7s for vendor window
severity: critical
confidence: 0.9
created: 2026-08-28
authors: [mara]
anchors:
  - path: payments/
  - pattern: 'lower.{0,20}sleep'
evidence:
  - issue: 243
violation: 'sleep\([0-6]\)'
status: active
---

Do not lower the sleep.
"""

COMMAND_SCAR = r"""---
id: 2
type: deadend
title: Bare uv sync strips extras
severity: high
confidence: 0.9
created: 2026-08-28
authors: [mara]
anchors:
  - command: 'uv sync(?!.* --all-extras)'
evidence:
  - issue: 243
status: active
---

Always run uv sync --all-extras.
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    init_scars(tmp_path)
    (tmp_path / ".scars" / "0001-sleep.fence.md").write_text(FENCE)
    (tmp_path / ".scars" / "0002-uv.deadend.md").write_text(COMMAND_SCAR)
    (tmp_path / "payments").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCAR_STATE_DIR", str(tmp_path / "state"))
    return tmp_path


def feed(monkeypatch, payload):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))


def out_json(capsys):
    output = capsys.readouterr().out.strip()
    return json.loads(output) if output else None


def codex_patch(repo, patch, response=None, tool_use_id="exec-test-1"):
    payload = {
        "session_id": "session-test",
        "turn_id": "turn-test",
        "cwd": str(repo),
        "hook_event_name": "PreToolUse",
        "tool_name": "apply_patch",
        "tool_use_id": tool_use_id,
        "tool_input": {"command": patch},
    }
    if response is not None:
        payload["hook_event_name"] = "PostToolUse"
        payload["tool_response"] = response
    return payload


def test_parse_apply_patch_covers_add_update_move_delete_and_multiple_files():
    patch = """*** Begin Patch
*** Add File: payments/new.py
+time.sleep(7)
*** Update File: payments/old.py
*** Move to: payments/moved.py
@@
-time.sleep(7)
+time.sleep(3)
*** Delete File: payments/gone.py
*** End Patch"""
    assert parse_apply_patch(patch) == [
        PatchTarget("payments/new.py", "time.sleep(7)"),
        PatchTarget("payments/old.py", "time.sleep(3)"),
        PatchTarget("payments/moved.py", "time.sleep(3)"),
        PatchTarget("payments/gone.py", ""),
    ]


@pytest.mark.parametrize("patch", [
    "not a patch",
    "*** Begin Patch\n*** Unknown File: x.py\n+x = 1\n*** End Patch",
    "*** Begin Patch\n*** Add File: \n+x = 1\n*** End Patch",
    "*** Begin Patch\n*** Move to: x.py\n*** End Patch",
])
def test_parse_apply_patch_rejects_malformed_programs(patch):
    assert parse_apply_patch(patch) == []


def test_codex_pretool_injects_once_across_multiple_files(
        repo, monkeypatch, capsys):
    patch = """*** Begin Patch
*** Update File: payments/a.py
@@
+lower the sleep to 3
*** Update File: payments/b.py
@@
+lower the sleep to 4
*** End Patch"""
    feed(monkeypatch, codex_patch(repo, patch))
    assert main(["hook", "codex-pretool"]) == 0
    ctx = out_json(capsys)["hookSpecificOutput"]["additionalContext"]
    assert ctx.count("Sleep is 7s for vendor window") == 1
    assert "Do not lower the sleep." in ctx

    records = [json.loads(line) for line in
               (repo / "state" / "firing-log.jsonl").read_text().splitlines()]
    assert records == [{
        "ts": records[0]["ts"],
        "repo": str(repo),
        "target": str(repo / "payments" / "a.py"),
        "scar_ids": [1],
        "count": 1,
        # #266: which fired scars carried a violation: tripwire and could
        # therefore be recorded as violated. Always present, even when empty —
        # `[]` is "none armed", a MISSING key is "row predates the field".
        "armed_ids": [1],
        "demoted_ids": [],
        "runtime": "codex",
        "edit_id": "exec-test-1",
    }]


def test_codex_pretool_accepts_absolute_path_inside_repo(repo, monkeypatch, capsys):
    patch = f"""*** Begin Patch
*** Update File: {repo / 'payments' / 'retry.py'}
@@
+lower the sleep to 3
*** End Patch"""
    feed(monkeypatch, codex_patch(repo, patch))
    assert main(["hook", "codex-pretool"]) == 0
    assert "Do not lower the sleep." in str(out_json(capsys))


def test_codex_pretool_ignores_path_outside_repo(repo, monkeypatch, capsys, tmp_path):
    outside = tmp_path.parent / "outside.py"
    patch = f"""*** Begin Patch
*** Add File: {outside}
+lower the sleep to 3
*** End Patch"""
    feed(monkeypatch, codex_patch(repo, patch))
    assert main(["hook", "codex-pretool"]) == 0
    assert out_json(capsys) is None
    assert not (repo / "state" / "firing-log.jsonl").exists()


def test_codex_pretool_command_uses_codex_runtime_and_edit_id(
        repo, monkeypatch, capsys):
    feed(monkeypatch, {
        "cwd": str(repo),
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_use_id": "exec-command-1",
        "tool_input": {"command": "uv sync"},
    })
    assert main(["hook", "codex-pretool"]) == 0
    assert "Always run uv sync --all-extras" in str(out_json(capsys))
    record = json.loads((repo / "state" / "firing-log.jsonl").read_text())
    assert record["runtime"] == "codex"
    assert record["edit_id"] == "exec-command-1"


def test_codex_pretool_preserves_global_three_scar_budget(
        repo, monkeypatch, capsys):
    for scar_id in range(3, 7):
        text = FENCE.replace("id: 1", f"id: {scar_id}").replace(
            "Sleep is 7s for vendor window", f"Budget scar {scar_id}")
        (repo / ".scars" / f"{scar_id:04d}-budget.fence.md").write_text(text)
    patch = """*** Begin Patch
*** Update File: payments/a.py
@@
+lower the sleep to 3
*** Update File: payments/b.py
@@
+lower the sleep to 4
*** End Patch"""
    feed(monkeypatch, codex_patch(repo, patch))
    assert main(["hook", "codex-pretool"]) == 0
    ctx = out_json(capsys)["hookSpecificOutput"]["additionalContext"]
    assert "3 match(es)" in ctx
    assert ctx.count("[fence #") == 3


def test_codex_posttool_emits_advisory_violation_and_correlates_log(
        repo, monkeypatch, capsys):
    patch = """*** Begin Patch
*** Update File: payments/retry.py
@@
+time.sleep(3)
*** End Patch"""
    response = "Exit code: 0\nOutput:\nSuccess. Updated the following files:\nM payments/retry.py\n"
    feed(monkeypatch, codex_patch(repo, patch, response, "exec-post-1"))
    assert main(["hook", "codex-posttool"]) == 0
    ctx = out_json(capsys)["hookSpecificOutput"]["additionalContext"]
    assert "Sleep is 7s for vendor window" in ctx
    assert "payments/retry.py now contains code" in ctx
    record = json.loads((repo / "state" / "firing-log.jsonl").read_text())
    assert record["runtime"] == "codex"
    assert record["edit_id"] == "exec-post-1"
    assert record["violation_ids"] == [1]


def test_codex_posttool_ignores_failed_apply_patch(repo, monkeypatch, capsys):
    patch = """*** Begin Patch
*** Update File: payments/retry.py
@@
+time.sleep(3)
*** End Patch"""
    feed(monkeypatch, codex_patch(repo, patch, "Exit code: 1\nPatch failed"))
    assert main(["hook", "codex-posttool"]) == 0
    assert out_json(capsys) is None


def test_codex_session_notice_does_not_check_claude_install(
        repo, monkeypatch, capsys):
    import scar.installer as installer

    monkeypatch.setattr(installer, "precheck_installed",
                        lambda: pytest.fail("Claude install check must not run"))
    feed(monkeypatch, {"cwd": str(repo), "hook_event_name": "SessionStart"})
    assert main(["hook", "codex-session-notice"]) == 0
    ctx = out_json(capsys)["hookSpecificOutput"]["additionalContext"]
    assert "Relevant scars are injected automatically" in ctx
    assert "NOT installed" not in ctx


def test_codex_hooks_fail_open_on_garbage_stdin(repo, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert main(["hook", "codex-pretool"]) == 0
    assert out_json(capsys) is None
