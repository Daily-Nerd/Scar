"""CLI surface: each command's contract, exercised through main()."""

import json
import subprocess
from pathlib import Path

import pytest

from scar import symbols
from scar.cli import main
from scar.store import ScarStore, init_scars

symbols_extra = pytest.mark.skipif(
    not symbols.symbols_available(), reason="tree-sitter extra not installed")

CANDIDATE = """\
---
type: deadend
title: Tried X, failed
severity: medium
confidence: 0.7
created: 2026-06-10
authors: ["claude-code"]
anchors:
  - path: src/
evidence:
  - commit: abc1234
status: candidate
---

Why X failed.
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    # A real (empty) git repo: `git ls-files` returns rc 0 with no output, so the
    # tracked set is legitimately empty. A bare `.git/` dir is NOT a valid repo —
    # git fails there (rc 128), which orphan detection now surfaces instead of
    # mistaking for an empty tracked set (#91).
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_init_creates_layout_and_reports(repo, capsys):
    assert main(["init"]) == 0
    assert (repo / ".scars" / "template.md").exists()
    assert ".scars" in capsys.readouterr().out


# --- init example seed (#136 item 3) ---

def test_init_seeds_lintclean_example_candidate(repo, capsys):
    """Fresh init drops one self-describing example scar in candidates/ so the
    format and lifecycle are visible before real pain accumulates (#136). It
    must be a real, lint-clean candidate — parseable by the one true parser."""
    from scar.lint import lint_text
    from scar.model import parse_scar_text
    assert main(["init"]) == 0
    out = capsys.readouterr().out
    example = repo / ".scars" / "candidates" / "example-first-scar.md"
    assert example.exists()
    assert "example-first-scar.md" in out
    text = example.read_text(encoding="utf-8")
    scar = parse_scar_text(text)
    assert scar.status == "candidate"
    assert scar.path_anchors or scar.pattern_anchors
    assert "promote" in scar.body.lower()
    assert "delete" in scar.body.lower()
    errors = [f for f in lint_text(text) if f.level == "error"]
    assert errors == []


def test_init_no_seed_flag_skips_example(repo, capsys):
    assert main(["init", "--no-seed"]) == 0
    assert not (repo / ".scars" / "candidates" / "example-first-scar.md").exists()


def test_init_never_reseeds_an_existing_scars_dir(repo, capsys):
    """The user deleting the example is a decision, not damage — re-running
    init on an existing .scars/ must not resurrect it (#136)."""
    assert main(["init"]) == 0
    example = repo / ".scars" / "candidates" / "example-first-scar.md"
    example.unlink()
    assert main(["init"]) == 0
    assert not example.exists()


# --- init guided first-run (#136 item 2) ---

def test_init_prints_harvest_onramp_when_history_exists(repo, capsys):
    """A repo with commits has minable history — init must point the user at
    harvest and hook install so first-run value isn't zero (#136). The
    suggested commands must be runnable as printed."""
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "feat: a")
    assert main(["init"]) == 0
    out = capsys.readouterr().out
    assert "scar harvest --top-k 10" in out
    assert "scar harvest --write 5" in out
    assert "scar hook install" in out
    assert "scar hook install --git" in out


def test_init_output_unchanged_in_empty_git_repo(repo, capsys):
    """Zero commits = nothing to harvest — the on-ramp would suggest commands
    that come back empty. Output stays exactly the two legacy lines (#136)."""
    assert main(["init"]) == 0
    out = capsys.readouterr().out
    assert "harvest" not in out
    assert "hook" not in out
    assert "initialized" in out


def test_init_output_unchanged_outside_git(tmp_path, monkeypatch, capsys):
    """No git at all — same legacy output, and init must not crash on the
    history probe."""
    work = tmp_path / "plain"
    work.mkdir()
    monkeypatch.chdir(work)
    assert main(["init"]) == 0
    out = capsys.readouterr().out
    assert "harvest" not in out
    assert "initialized" in out


def test_lint_clean_repo_exits_zero(repo, capsys):
    init_scars(repo)
    assert main(["lint"]) == 0


def test_lint_broken_scar_exits_nonzero_names_file(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "0001-bad.deadend.md").write_text("# nope\n")
    assert main(["lint"]) == 1
    assert "0001-bad.deadend.md" in capsys.readouterr().out


def test_version_flag_prints_version_and_exits_zero(capsys):
    import re

    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("scar ")
    assert re.search(r"\d+\.\d+", out)


def test_root_parser_uses_rich_help_formatter():
    from rich_argparse import RichHelpFormatter

    from scar.cli import build_parser

    parser = build_parser()
    assert parser.formatter_class is RichHelpFormatter


def _subparsers_choices(parser):
    import argparse

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    raise AssertionError("no subparsers action found")


def test_subparser_uses_rich_help_formatter():
    from rich_argparse import RichHelpFormatter

    from scar.cli import build_parser

    lint = _subparsers_choices(build_parser())["lint"]
    assert lint.formatter_class is RichHelpFormatter


def test_nested_agent_subparser_uses_rich_help_formatter():
    from rich_argparse import RichHelpFormatter

    from scar.cli import build_parser

    agent = _subparsers_choices(build_parser())["agent"]
    config = _subparsers_choices(agent)["config"]
    assert config.formatter_class is RichHelpFormatter


def test_help_still_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_status_counts(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "candidates" / "x.md").write_text(CANDIDATE)
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "0 active" in out and "1 candidate" in out


def test_promote_assigns_id_and_reports(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "candidates" / "tried-x.md").write_text(CANDIDATE)
    assert main(["promote", "tried-x", "--reviewer", "kibukx"]) == 0
    assert (repo / ".scars" / "0001-tried-x.deadend.md").exists()


def test_promote_unknown_candidate_fails(repo, capsys):
    init_scars(repo)
    assert main(["promote", "nope"]) == 1


def test_promote_reviewer_falls_back_to_git_user_name(repo, capsys):
    init_scars(repo)
    subprocess.run(["git", "config", "user.name", "Repo Reviewer"],
                   cwd=repo, check=True)
    (repo / ".scars" / "candidates" / "tried-x.md").write_text(CANDIDATE)
    assert main(["promote", "tried-x"]) == 0
    text = (repo / ".scars" / "0001-tried-x.deadend.md").read_text()
    assert "Repo Reviewer" in text
    assert "reviewer: Repo Reviewer (from git config)" in capsys.readouterr().out


def test_promote_reviewer_flag_wins_over_git_config(repo, capsys):
    init_scars(repo)
    subprocess.run(["git", "config", "user.name", "Repo Reviewer"],
                   cwd=repo, check=True)
    (repo / ".scars" / "candidates" / "tried-x.md").write_text(CANDIDATE)
    assert main(["promote", "tried-x", "--reviewer", "kibukx"]) == 0
    text = (repo / ".scars" / "0001-tried-x.deadend.md").read_text()
    assert "kibukx" in text
    assert "Repo Reviewer" not in text


def test_promote_without_reviewer_or_git_identity_keeps_authors(
        repo, capsys, monkeypatch):
    # Blind git to global/system config so user.name is genuinely unset.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    init_scars(repo)
    (repo / ".scars" / "candidates" / "tried-x.md").write_text(CANDIDATE)
    assert main(["promote", "tried-x"]) == 0
    text = (repo / ".scars" / "0001-tried-x.deadend.md").read_text()
    assert 'authors: ["claude-code"]' in text


def test_check_lists_scars_for_path(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "candidates" / "tried-x.md").write_text(CANDIDATE)
    main(["promote", "tried-x", "--reviewer", "k"])
    (repo / "src").mkdir()
    assert main(["check", "src/thing.py"]) == 0
    assert "Tried X, failed" in capsys.readouterr().out


def test_inject_emits_hook_json(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "candidates" / "tried-x.md").write_text(CANDIDATE)
    main(["promote", "tried-x", "--reviewer", "k"])
    capsys.readouterr()  # flush promote's human output before machine-mode JSON
    assert main(["inject", "--path", "src/thing.py", "--content", ""]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "Tried X" in payload["hookSpecificOutput"]["additionalContext"]


def _active_scar(id: int, title: str) -> str:
    return (
        f"---\nid: {id}\ntype: deadend\ntitle: {title}\nseverity: medium\n"
        f"confidence: 0.7\ncreated: 2026-06-10\nauthors: [k]\nanchors:\n"
        f"  - path: src/\nevidence:\n  - commit: abc1234\nstatus: active\n---\n\nBody.\n")


def test_inject_top_k_clamped_to_three(repo, capsys):
    # #91.7: --top-k is a format-level guarantee (max 3 injected), not a tuning
    # knob. A caller passing --top-k 50 must still cap at 3.
    init_scars(repo)
    for i in range(1, 6):  # 5 scars all firing on src/thing.py
        (repo / ".scars" / f"000{i}-s{i}.deadend.md").write_text(
            _active_scar(i, f"Scar number {i}"))
    assert main(["inject", "--path", "src/thing.py", "--content", "",
                 "--top-k", "50"]) == 0
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "3 match(es)" in ctx
    assert "match(es)" in ctx and "5 match(es)" not in ctx


def test_inject_diff_with_binary_file_never_crashes(repo, capsys, tmp_path):
    init_scars(repo)
    bad = tmp_path / "binary.diff"
    bad.write_bytes(b"\xff\xfe\x00garbage\x80")
    assert main(["inject", "--diff", str(bad)]) == 0


def test_inject_silent_when_no_match(repo, capsys):
    init_scars(repo)
    assert main(["inject", "--path", "docs/x.md", "--content", ""]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_inject_accepts_unified_diff(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "candidates" / "tried-x.md").write_text(CANDIDATE)
    main(["promote", "tried-x", "--reviewer", "k"])
    capsys.readouterr()
    diff = """\
diff --git a/src/thing.py b/src/thing.py
--- a/src/thing.py
+++ b/src/thing.py
@@ -0,0 +1 @@
+print("x")
"""
    assert main(["inject", "--diff", diff]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "Tried X" in payload["hookSpecificOutput"]["additionalContext"]


def _patterned_scar(id: int, title: str) -> str:
    return _active_scar(id, title).replace(
        "anchors:\n  - path: src/",
        'anchors:\n  - path: src/\n  - pattern: "forbidden_call"')


def test_inject_demotes_path_only_match(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "0001-s1.deadend.md").write_text(_active_scar(1, "Scar one"))
    assert main(["inject", "--path", "src/thing.py", "--content", "x = 1"]) == 0
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "Scar one" in ctx                 # label line stays
    assert "path-only match" in ctx          # reason visible
    assert "Body." not in ctx                # body demoted


def test_inject_diff_mode_full_body_on_content_hit(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "0001-s1.deadend.md").write_text(_patterned_scar(1, "Scar one"))
    diff = """\
diff --git a/src/thing.py b/src/thing.py
--- a/src/thing.py
+++ b/src/thing.py
@@ -0,0 +1 @@
+forbidden_call()
"""
    assert main(["inject", "--diff", diff]) == 0
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "Body." in ctx                    # full body on content signal


def test_inject_diff_mode_demotes_path_only_match(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "0001-s1.deadend.md").write_text(_active_scar(1, "Scar one"))
    diff = """\
diff --git a/src/thing.py b/src/thing.py
--- a/src/thing.py
+++ b/src/thing.py
@@ -0,0 +1 @@
+x = 1
"""
    assert main(["inject", "--diff", diff]) == 0
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "Scar one" in ctx                 # label line stays
    assert "path-only match" in ctx          # reason visible
    assert "Body." not in ctx                # body demoted


def test_agent_config_prints_opencode_mcp_snippet(repo, capsys):
    assert main(["agent", "config", "opencode"]) == 0
    out = capsys.readouterr().out
    assert '"command": ["scar", "mcp"]' in out


def test_agent_doctor_reports_agents_file(repo, capsys):
    (repo / "AGENTS.md").write_text("# rules\n")
    assert main(["agent", "doctor"]) == 0
    assert "AGENTS.md: present" in capsys.readouterr().out


@pytest.mark.parametrize("target, token", [
    ("codex", "AGENTS.md"),
    ("cursor", "mcpServers"),
    ("opencode", "opencode.jsonc"),
    ("windsurf", "Cascade"),
])
def test_agent_config_returns_setup_text_per_target(target, token):
    """Every supported runtime returns its own distinctive setup text."""
    from scar.agent import config
    assert token in config(target)


@pytest.mark.parametrize("target", ["codex", "cursor", "opencode", "windsurf"])
def test_agent_config_includes_draft_check_block_for_every_target(target):
    """#117: every runtime's setup text carries the reciprocal-duty +
    draft-check block, runtime-neutral wording, same block for all four."""
    from scar.agent import config
    text = config(target)
    assert "scar draft-check" in text
    assert "Reciprocal duty" in text
    assert ".scars/candidates/" in text
    assert "status: candidate" in text


def test_agent_config_unknown_target_raises_value_error():
    """The unknown-target path (agent.py:64) raises ValueError naming the input
    and the valid choices. This is the real error path — argparse's choices=
    guards main(), so config() is where the guard actually lives."""
    from scar.agent import config
    with pytest.raises(ValueError) as exc:
        config("bogus")
    assert "bogus" in str(exc.value)
    assert "codex" in str(exc.value)  # lists the valid targets


def test_cmd_agent_catches_unknown_target_and_returns_one(capsys):
    """_cmd_agent's try/except → return 1 net. NOTE: main(["agent","config",
    "bogus"]) cannot reach it — argparse choices= rejects 'bogus' with
    SystemExit(2) first — so the catch is exercised via a forged namespace."""
    import argparse

    from scar.cli import _cmd_agent
    ns = argparse.Namespace(agent_command="config", target="bogus")
    assert _cmd_agent(ns) == 1
    assert "bogus" in capsys.readouterr().out


def test_agent_doctor_reports_missing_agents_and_scars(tmp_path):
    """Both ternaries take their 'missing' branch when neither file exists."""
    from scar.agent import doctor
    lines = doctor(tmp_path)
    assert "AGENTS.md: missing" in lines
    assert ".scars/: missing" in lines


def test_agent_doctor_reports_present_scars_dir(tmp_path):
    """The .scars/ ternary takes its 'present' branch when the dir exists."""
    from scar.agent import doctor
    (tmp_path / ".scars").mkdir()
    assert ".scars/: present" in doctor(tmp_path)


def test_agent_doctor_scar_binary_fallback_when_not_on_path(tmp_path, monkeypatch):
    """shutil.which → None drives the 'not found on PATH' fallback."""
    import scar.agent as agent
    monkeypatch.setattr(agent.shutil, "which", lambda name: None)
    assert any("scar binary: not found on PATH" in ln for ln in agent.doctor(tmp_path))


def test_agent_doctor_scar_binary_reports_resolved_path(tmp_path, monkeypatch):
    """shutil.which → a path reports that resolved path (the non-fallback side)."""
    import scar.agent as agent
    monkeypatch.setattr(agent.shutil, "which", lambda name: "/usr/local/bin/scar")
    assert any("scar binary: /usr/local/bin/scar" in ln for ln in agent.doctor(tmp_path))


# ---------------------------------------------------------------------------
# plugin_resolve() (#113) — mirrors plugin/hooks/run.sh's resolution order
# (PATH, then a fixed candidate-dir list) so `scar agent doctor` can report
# what the *plugin* wrapper would resolve, distinct from find_scar() (which
# is venv-aware and only serves `scar hook install`).
# ---------------------------------------------------------------------------

def test_plugin_resolve_prefers_path(monkeypatch):
    import scar.agent as agent
    monkeypatch.setattr(agent.shutil, "which", lambda name: "/usr/bin/scar")
    assert agent.plugin_resolve() == "/usr/bin/scar"


def test_plugin_resolve_falls_back_to_candidate_dir_when_path_empty(tmp_path, monkeypatch):
    import scar.agent as agent
    monkeypatch.setattr(agent.shutil, "which", lambda name: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    cand = tmp_path / ".local" / "bin" / "scar"
    cand.parent.mkdir(parents=True)
    cand.write_text("#!/bin/sh\necho scar\n")
    cand.chmod(0o755)
    assert agent.plugin_resolve() == str(cand)


def test_plugin_resolve_checks_candidate_dirs_in_declared_order(tmp_path, monkeypatch):
    """Every dir in _PLUGIN_CANDIDATE_DIRS must be reachable — write to the
    LAST candidate and confirm it's still found (proves the loop doesn't stop
    early on a dir that merely doesn't exist)."""
    import scar.agent as agent
    monkeypatch.setattr(agent.shutil, "which", lambda name: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    last = agent._PLUGIN_CANDIDATE_DIRS[-1]
    cand = Path(last.replace("~", str(tmp_path), 1))
    cand.parent.mkdir(parents=True)
    cand.write_text("#!/bin/sh\necho scar\n")
    cand.chmod(0o755)
    assert agent.plugin_resolve() == str(cand)


def test_plugin_resolve_returns_none_when_unresolvable(tmp_path, monkeypatch):
    import scar.agent as agent
    monkeypatch.setattr(agent.shutil, "which", lambda name: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert agent.plugin_resolve() is None


def test_agent_doctor_reports_plugin_resolution_when_found(tmp_path, monkeypatch):
    import scar.agent as agent
    monkeypatch.setattr(agent, "plugin_resolve", lambda: "/usr/bin/scar")
    assert any("plugin PATH resolution: /usr/bin/scar" in ln for ln in agent.doctor(tmp_path))


def test_agent_doctor_reports_plugin_unresolvable(tmp_path, monkeypatch):
    import scar.agent as agent
    monkeypatch.setattr(agent, "plugin_resolve", lambda: None)
    assert any("plugin PATH resolution: not resolvable — plugin hooks will no-op" in ln
               for ln in agent.doctor(tmp_path))


# ---------------------------------------------------------------------------
# plugin/hooks/run.sh (#113) — the wrapper plugin.json invokes instead of a
# bare `scar`, so a missing binary is visible (session-notice) instead of a
# silent forever no-op. Run with a scrubbed PATH + fake HOME to control
# resolution end to end.
# ---------------------------------------------------------------------------

_RUN_SH = Path(__file__).resolve().parents[1] / "plugin" / "hooks" / "run.sh"


def _run_wrapper(kind, home, path="/usr/bin:/bin"):
    return subprocess.run(
        ["/bin/sh", str(_RUN_SH), kind],
        env={"HOME": str(home), "PATH": path},
        input="{}", capture_output=True, text=True, timeout=30)


def test_run_sh_unresolvable_session_notice_warns(tmp_path):
    proc = _run_wrapper("session-notice", tmp_path)
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    ctx = out["hookSpecificOutput"]
    assert ctx["hookEventName"] == "SessionStart"
    assert "scar-cli binary was not found" in ctx["additionalContext"]
    assert "uv tool install scar-cli" in ctx["additionalContext"]


def test_run_sh_unresolvable_precheck_is_silent(tmp_path):
    proc = _run_wrapper("precheck", tmp_path)
    assert proc.returncode == 0
    assert proc.stdout == ""


def test_run_sh_resolves_from_candidate_dir_and_execs(tmp_path):
    fake = tmp_path / ".local" / "bin" / "scar"
    fake.parent.mkdir(parents=True)
    fake.write_text("#!/bin/sh\necho \"invoked hook $2\"\n")
    fake.chmod(0o755)
    proc = _run_wrapper("precheck", tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "invoked hook precheck"


def test_why_on_parent_dir_surfaces_descendant_anchors(repo, capsys):
    """Asking a parent directory for its history must include scars anchored
    deeper inside it — found live: `scar why research` missed a landmine
    anchored at research/experiments/track-a/."""
    init_scars(repo)
    deep = CANDIDATE.replace("  - path: src/", "  - path: src/experiments/track-a/")
    (repo / ".scars" / "candidates" / "deep.md").write_text(deep)
    main(["promote", "deep", "--reviewer", "k"])
    (repo / "src").mkdir()
    capsys.readouterr()
    assert main(["why", "src"]) == 0
    assert "Tried X, failed" in capsys.readouterr().out


def test_no_scars_dir_commands_fail_gracefully(repo, capsys):
    assert main(["status"]) == 1
    assert ".scars" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Orphan surfaces (Issue #33). A firing scar with a dead path anchor and an
# empty (or absent) git index is, by construction, an orphan.
# ---------------------------------------------------------------------------

ORPHAN_SCAR = """\
---
id: 1
type: deadend
title: Anchored to a path that no longer exists
severity: medium
confidence: 0.8
created: 2026-06-10
authors: ["claude-code"]
anchors:
  - path: src/long_gone/
evidence:
  - commit: abc1234
status: active
---

The module this anchored to was deleted.
"""

NO_ANCHOR_SCAR = """\
---
id: 2
type: fence
title: Scar that protects nothing
severity: low
confidence: 0.5
created: 2026-06-10
authors: ["claude-code"]
anchors:
evidence:
  - commit: def5678
status: active
---

No anchors at all.
"""


def test_lint_warns_on_detected_orphan_exit_zero(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "0001-gone.deadend.md").write_text(ORPHAN_SCAR)
    assert main(["lint"]) == 0  # warning only, never fails by default
    out = capsys.readouterr().out
    assert "orphan" in out.lower()
    assert "#1" in out
    assert "src/long_gone/" in out  # which anchor is dead


def test_lint_fail_orphans_flag_exits_one(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "0001-gone.deadend.md").write_text(ORPHAN_SCAR)
    assert main(["lint", "--fail-orphans"]) == 1
    assert "orphan" in capsys.readouterr().out.lower()


def test_lint_no_anchor_scar_labeled_distinctly(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "0002-empty.fence.md").write_text(NO_ANCHOR_SCAR)
    main(["lint"])
    out = capsys.readouterr().out.lower()
    assert "no anchors" in out  # distinct from "all anchors dead"


PERSISTED_ORPHAN = ORPHAN_SCAR.replace("status: active", "status: orphaned").replace("id: 1", "id: 3")

MULTI_ANCHOR_ORPHAN = """\
---
id: 5
type: deadend
title: Both anchors dead
severity: high
confidence: 0.9
created: 2026-06-10
authors: ["claude-code"]
anchors:
  - path: src/dead_dir/
  - pattern: "OldClassName"
evidence:
  - commit: aaa1111
status: active
---

Both the path and the pattern are gone.
"""


def test_orphan_command_lists_failed_anchors_readonly(repo, capsys):
    init_scars(repo)
    f = repo / ".scars" / "0005-both.deadend.md"
    f.write_text(MULTI_ANCHOR_ORPHAN)
    before = f.read_text()
    assert main(["orphan"]) == 0
    out = capsys.readouterr().out
    assert "#5" in out
    assert "src/dead_dir/" in out
    assert "OldClassName" in out
    assert f.read_text() == before  # read-only: file untouched


def test_orphan_apply_persists_status_with_dated_note(repo, capsys, monkeypatch):
    import scar.cli as cli
    monkeypatch.setattr(cli.time, "strftime", lambda fmt: "2026-06-13")
    init_scars(repo)
    f = repo / ".scars" / "0005-both.deadend.md"
    f.write_text(MULTI_ANCHOR_ORPHAN)
    assert main(["orphan", "--apply", "--id", "5", "--reason", "module deleted in #99"]) == 0
    text = f.read_text()
    assert "status: orphaned" in text
    assert "2026-06-13" in text
    assert "module deleted in #99" in text
    assert "src/dead_dir/" in text  # failed anchors recorded in the note


def test_orphan_apply_rejects_unknown_id(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "0005-both.deadend.md").write_text(MULTI_ANCHOR_ORPHAN)
    assert main(["orphan", "--apply", "--id", "999", "--reason", "x"]) == 1
    assert "not orphan-detected" in capsys.readouterr().out


DEAD_ANCHOR_CANDIDATE = """\
---
type: deadend
title: Anchored to a vanished path
severity: medium
confidence: 0.7
created: 2026-06-10
authors: ["claude-code"]
anchors:
  - path: src/never_existed/
evidence:
  - commit: abc1234
status: candidate
---

Why X failed.
"""


def test_promote_warns_when_anchors_all_dead_but_succeeds(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "candidates" / "dead.md").write_text(DEAD_ANCHOR_CANDIDATE)
    rc = main(["promote", "dead", "--reviewer", "k"])
    out = capsys.readouterr().out
    assert rc == 0  # advisory is NON-blocking
    assert (repo / ".scars" / "0001-dead.deadend.md").exists()
    assert "anchor" in out.lower() and "advisory" in out.lower()


def test_lint_reverse_hint_fires_when_orphaned_anchors_return(tmp_path, monkeypatch, capsys):
    """A persisted-orphaned scar whose anchor path is tracked again should
    surface a 're-activate' hint."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    init_scars(tmp_path)
    # anchor path now exists and is tracked → anchors live again
    (tmp_path / "src" / "revived").mkdir(parents=True)
    (tmp_path / "src" / "revived" / "mod.py").write_text("x = 1\n")
    revived = ORPHAN_SCAR.replace("status: active", "status: orphaned") \
        .replace("src/long_gone/", "src/revived/")
    (tmp_path / ".scars" / "0001-back.deadend.md").write_text(revived)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    assert main(["lint"]) == 0
    out = capsys.readouterr().out.lower()
    assert "live again" in out and "#1" in out


# ---------------------------------------------------------------------------
# Evidence-reachability surface (Issue #43, scar #5's expiry condition).
# A commit-SHA receipt that doesn't resolve from HEAD is advisory: warned,
# counted, never gated. Needs a real git repo (reachability is a git fact).
# ---------------------------------------------------------------------------

EVIDENCE_SCAR = """\
---
id: 1
type: deadend
title: Cites a commit that no longer resolves
severity: medium
confidence: 0.8
created: 2026-06-10
authors: ["claude-code"]
anchors:
  - path: src/
evidence:
  - commit: deadbeef
status: active
---

The cited SHA was orphaned by a history rewrite.
"""


def test_lint_warns_on_unreachable_evidence_sha(tmp_path, monkeypatch, capsys):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    init_scars(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text("x = 1\n")
    (tmp_path / ".scars" / "0001-stale.deadend.md").write_text(EVIDENCE_SCAR)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    assert main(["lint"]) == 0  # advisory, never fails by default
    out = capsys.readouterr().out
    assert "evidence-unreachable" in out.lower()
    assert "#1" in out
    assert "deadbeef" in out
    assert "1 unreachable-evidence" in out  # counted in the summary


def test_status_reports_detected_and_persisted_orphan_counts(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "0001-gone.deadend.md").write_text(ORPHAN_SCAR)        # detected
    (repo / ".scars" / "0003-already.deadend.md").write_text(PERSISTED_ORPHAN)  # persisted
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "1 orphan-detected" in out
    assert "1 orphaned" in out  # persisted, separately counted


# ---------------------------------------------------------------------------
# Partial-rot surfaces (Issue #35). A firing scar with a mix of live and dead
# anchors is advisory — named, but never an orphan and never a blocking gate.
# Needs a REAL git repo so a live anchor has a tracked path to resolve against.
# ---------------------------------------------------------------------------

PARTIAL_ROT_SCAR = """\
---
id: 7
type: landmine
title: One anchor alive, one rotted
severity: medium
confidence: 0.8
created: 2026-06-10
authors: ["claude-code"]
anchors:
  - path: src/live/
  - path: src/gone/
evidence:
  - commit: abc1234
status: active
---

Protects two things; one of them was deleted.
"""


def _git_repo_with_partial_rot(tmp_path):
    """Real git repo: src/live/ is tracked (anchor lives), src/gone/ is not
    (anchor dead) → the scar partially rots."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    init_scars(tmp_path)
    (tmp_path / "src" / "live").mkdir(parents=True)
    (tmp_path / "src" / "live" / "mod.py").write_text("x = 1\n")
    (tmp_path / ".scars" / "0007-rot.landmine.md").write_text(PARTIAL_ROT_SCAR)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)


def test_lint_surfaces_partial_rot_naming_dead_anchor(tmp_path, monkeypatch, capsys):
    _git_repo_with_partial_rot(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["lint"]) == 0  # advisory only, never fails by default
    out = capsys.readouterr().out
    assert "#7" in out
    assert "src/gone/" in out          # the dead anchor is named
    assert "src/live/" not in out      # the live anchor is NOT flagged
    assert "partial" in out.lower()    # labeled as partial rot, not orphan
    assert "orphan-detected: scar #7" not in out  # never reported as an orphan


def test_status_reports_partial_rot_count(tmp_path, monkeypatch, capsys):
    _git_repo_with_partial_rot(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "1 partial-rot" in out
    assert "0 orphan-detected" in out  # a partial-rot scar is NOT an orphan


def test_orphan_command_reports_partial_rot_count(tmp_path, monkeypatch, capsys):
    _git_repo_with_partial_rot(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["orphan"]) == 0
    out = capsys.readouterr().out
    assert "no orphan-detected scars" in out  # zero true orphans
    assert "1 partial-rot" in out             # but partial rot surfaced separately
    assert "#7" in out


# ---------------------------------------------------------------------------
# Issue #109: rename-following for orphaned path anchors.
# ---------------------------------------------------------------------------

RENAMED_ORPHAN_SCAR = """\
---
id: 9
type: deadend
title: Anchored to a path that was renamed
severity: medium
confidence: 0.8
created: 2026-06-10
authors: ["claude-code"]
anchors:
  - path: src/old_home.py
evidence:
  - commit: abc1234
status: active
---

The anchored file was git mv'd, not deleted.
"""


def _git_repo_with_renamed_anchor(tmp_path):
    """Real git repo: src/old_home.py existed, then was `git mv`d to
    src/new_home.py — the scar still anchors the pre-rename path."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    init_scars(tmp_path)
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "old_home.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add old_home.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "mv", "src/old_home.py", "src/new_home.py"],
                   cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "rename to new_home.py"], cwd=tmp_path, check=True)
    (tmp_path / ".scars" / "0009-renamed.deadend.md").write_text(RENAMED_ORPHAN_SCAR)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add scar"], cwd=tmp_path, check=True)


def test_orphan_command_reports_rename_target(tmp_path, monkeypatch, capsys):
    _git_repo_with_renamed_anchor(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["orphan"]) == 0
    out = capsys.readouterr().out
    assert "#9" in out
    assert "src/old_home.py" in out
    assert "src/new_home.py" in out
    assert "renamed" in out.lower()


def test_lint_reports_rename_target(tmp_path, monkeypatch, capsys):
    _git_repo_with_renamed_anchor(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["lint"]) == 0
    out = capsys.readouterr().out
    assert "src/old_home.py" in out
    assert "src/new_home.py" in out
    assert "renamed" in out.lower()


def test_status_reports_rename_target(tmp_path, monkeypatch, capsys):
    _git_repo_with_renamed_anchor(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "src/old_home.py" in out
    assert "src/new_home.py" in out
    assert "renamed" in out.lower()


def test_orphan_command_deleted_not_renamed_has_no_rename_text(repo, capsys):
    """Deleted-not-renamed keeps the plain 'all anchors dead' wording — no
    'renamed:' text ever appears for a scar with no rename target (#109)."""
    init_scars(repo)
    (repo / ".scars" / "0001-gone.deadend.md").write_text(ORPHAN_SCAR)
    assert main(["orphan"]) == 0
    out = capsys.readouterr().out
    assert "#1" in out
    assert "renamed" not in out.lower()


def test_orphan_fix_renames_rewrites_anchor_line_and_unorphans(tmp_path, monkeypatch, capsys):
    _git_repo_with_renamed_anchor(tmp_path)
    monkeypatch.chdir(tmp_path)
    f = tmp_path / ".scars" / "0009-renamed.deadend.md"
    before = f.read_text()

    assert main(["orphan", "--fix-renames"]) == 0
    out = capsys.readouterr().out
    assert "#9" in out
    assert "fixed" in out.lower()

    after = f.read_text()
    assert after != before
    before_lines = before.split("\n")
    after_lines = after.split("\n")
    assert len(before_lines) == len(after_lines)
    diffs = [(b, a) for b, a in zip(before_lines, after_lines) if b != a]
    assert diffs == [("  - path: src/old_home.py", "  - path: src/new_home.py")]

    # Re-run: the scar is no longer orphan-detected now that its anchor
    # resolves to the (renamed, still-tracked) file.
    assert main(["orphan"]) == 0
    out2 = capsys.readouterr().out
    assert "#9" not in out2
    assert "no orphan-detected scars" in out2


def test_orphan_fix_renames_leaves_unresolvable_scar_untouched(repo, capsys):
    """No rename target at all (plain delete) → --fix-renames makes no edit
    and says so; read-only stays the default without the flag."""
    init_scars(repo)
    f = repo / ".scars" / "0001-gone.deadend.md"
    f.write_text(ORPHAN_SCAR)
    before = f.read_text()
    assert main(["orphan", "--fix-renames"]) == 0
    out = capsys.readouterr().out
    assert "#1" in out
    assert "not fixed" in out.lower()
    assert f.read_text() == before


def test_orphan_fix_renames_is_opt_in_default_orphan_never_writes(tmp_path, monkeypatch):
    _git_repo_with_renamed_anchor(tmp_path)
    monkeypatch.chdir(tmp_path)
    f = tmp_path / ".scars" / "0009-renamed.deadend.md"
    before = f.read_text()
    assert main(["orphan"]) == 0  # no --fix-renames
    assert f.read_text() == before


# ---------------------------------------------------------------------------
# Harvest ranking surfaces + label instrument (Issue #38, batch 2)
# ---------------------------------------------------------------------------

def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def harvest_repo(tmp_path):
    """Synthetic git history yielding harvest candidates across sections:
    a deleted component, a revert-shaped commit, and a flapping value."""
    work = tmp_path / "work"
    work.mkdir()
    _git(work.parent, "init", "-q", "-b", "main", str(work))
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    comp = work / "apps" / "shortlived"
    comp.mkdir(parents=True)
    (comp / "deploy.yaml").write_text("replicas: 1\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "feat: add shortlived app")
    (comp / "deploy.yaml").write_text("replicas: 3\n")
    _git(work, "commit", "-qam", "feat: scale up")
    (comp / "deploy.yaml").write_text("replicas: 1\n")
    _git(work, "commit", "-qam", "fix: revert replicas, instance broke")
    (comp / "deploy.yaml").unlink()
    comp.rmdir()
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "chore: remove shortlived app")
    return work


def test_harvest_prints_id_and_score_per_candidate(harvest_repo, capsys):
    """Each candidate line must surface its stable id and score so a human can
    reference it (the id is what `scar harvest --label` consumes)."""
    assert main(["harvest", str(harvest_repo)]) == 0
    out = capsys.readouterr().out
    from scar.harvest import harvest
    result = harvest(harvest_repo)
    # pick any non-empty section's first candidate; its id and score must print
    for section in result.values():
        for c in section:
            assert c["id"] in out, f"id {c['id']} not surfaced in harvest output"
            # score printed to one decimal, e.g. "3.0"
            assert f"{c['score']:.1f}" in out, f"score {c['score']} not surfaced"


def test_harvest_top_k_returns_n_highest_across_sections(harvest_repo, capsys):
    """--top-k N shows exactly the N highest-scoring candidates across ALL
    sections, ranked by raw score (no cross-type normalization)."""
    from scar.harvest import harvest
    result = harvest(harvest_repo)
    all_cands = [c for section in result.values() for c in section]
    expected = sorted(all_cands, key=lambda c: c["score"], reverse=True)[:2]

    assert main(["harvest", str(harvest_repo), "--top-k", "2"]) == 0
    out = capsys.readouterr().out
    # exactly the 2 highest ids appear; the rest do not
    for c in expected:
        assert c["id"] in out, f"top-k missed expected id {c['id']}"
    excluded = [c for c in all_cands if c not in expected]
    for c in excluded:
        assert c["id"] not in out, f"top-k leaked excluded id {c['id']}"
    # ranked descending: first expected id appears before the second
    assert out.index(expected[0]["id"]) < out.index(expected[1]["id"])


def _first_candidate_id(repo):
    from scar.harvest import harvest
    result = harvest(repo)
    for section in result.values():
        for c in section:
            return c["id"]
    raise AssertionError("fixture produced no candidates")


def test_harvest_label_appends_jsonl_line(harvest_repo, tmp_path, capsys, monkeypatch):
    """--label <id> keep appends one well-formed JSONL line with all fields."""
    import scar.cli as cli
    labels = tmp_path / "labels.jsonl"
    monkeypatch.setattr(cli, "LABELS_PATH_OVERRIDE", labels)
    monkeypatch.setattr(cli.time, "strftime", lambda fmt: "2026-06-13")
    cid = _first_candidate_id(harvest_repo)

    assert main(["harvest", str(harvest_repo), "--label", cid, "keep",
                 "--note", "real deadend"]) == 0
    lines = labels.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["id"] == cid
    assert rec["label"] == "keep"
    assert rec["note"] == "real deadend"
    assert rec["date"] == "2026-06-13"
    assert "repo" in rec


def test_harvest_label_creates_parent_dir(harvest_repo, tmp_path, monkeypatch):
    """The labels dir/file is created on first write if missing."""
    import scar.cli as cli
    labels = tmp_path / "nested" / "experiments" / "harvest" / "labels.jsonl"
    monkeypatch.setattr(cli, "LABELS_PATH_OVERRIDE", labels)
    cid = _first_candidate_id(harvest_repo)
    assert not labels.parent.exists()
    assert main(["harvest", str(harvest_repo), "--label", cid, "discard"]) == 0
    assert labels.exists()


def test_harvest_label_appends_not_overwrites(harvest_repo, tmp_path, monkeypatch):
    """A second --label appends; it does not clobber the first line."""
    import scar.cli as cli
    labels = tmp_path / "labels.jsonl"
    monkeypatch.setattr(cli, "LABELS_PATH_OVERRIDE", labels)
    cid = _first_candidate_id(harvest_repo)
    main(["harvest", str(harvest_repo), "--label", cid, "keep"])
    main(["harvest", str(harvest_repo), "--label", cid, "discard"])
    assert len(labels.read_text(encoding="utf-8").splitlines()) == 2


def test_harvest_precision_reports_at_n_and_lift(harvest_repo, tmp_path, capsys, monkeypatch):
    """--precision reads labels.jsonl, reports precision@N, base rate and lift."""
    import scar.cli as cli
    labels = tmp_path / "labels.jsonl"
    monkeypatch.setattr(cli, "LABELS_PATH_OVERRIDE", labels)
    cid = _first_candidate_id(harvest_repo)
    assert main(["harvest", str(harvest_repo), "--label", cid, "keep"]) == 0
    capsys.readouterr()

    assert main(["harvest", str(harvest_repo), "--precision"]) == 0
    out = capsys.readouterr().out.lower()
    assert "precision@" in out
    assert "base rate" in out
    assert "lift" in out
    assert "1 labeled" in out  # exactly one label recorded


def test_harvest_precision_no_labels_is_friendly(harvest_repo, tmp_path, capsys, monkeypatch):
    """With no labels yet, --precision explains how to start and exits 0."""
    import scar.cli as cli
    monkeypatch.setattr(cli, "LABELS_PATH_OVERRIDE", tmp_path / "labels.jsonl")
    assert main(["harvest", str(harvest_repo), "--precision"]) == 0
    out = capsys.readouterr().out.lower()
    assert "no labels" in out
    assert "--label" in out


def test_harvest_precision_at_override(harvest_repo, tmp_path, capsys, monkeypatch):
    """--at overrides the default N set."""
    import scar.cli as cli
    labels = tmp_path / "labels.jsonl"
    monkeypatch.setattr(cli, "LABELS_PATH_OVERRIDE", labels)
    cid = _first_candidate_id(harvest_repo)
    main(["harvest", str(harvest_repo), "--label", cid, "keep"])
    capsys.readouterr()
    assert main(["harvest", str(harvest_repo), "--precision", "--at", "1"]) == 0
    out = capsys.readouterr().out
    assert "precision@1" in out
    assert "precision@5" not in out  # default set suppressed by override


def test_harvest_label_rejects_unknown_id(harvest_repo, tmp_path, capsys, monkeypatch):
    """An id not present in the current harvest is rejected; nothing appended."""
    import scar.cli as cli
    labels = tmp_path / "labels.jsonl"
    monkeypatch.setattr(cli, "LABELS_PATH_OVERRIDE", labels)
    assert main(["harvest", str(harvest_repo), "--label", "deadbeef00", "keep"]) == 1
    out = capsys.readouterr().out
    assert "not a harvest candidate" in out.lower() or "unknown" in out.lower()
    assert not labels.exists()


def test_harvest_label_rejects_bogus_label(harvest_repo, tmp_path, capsys, monkeypatch):
    """Only keep/discard are valid; a third value is rejected (precision_at_n
    contract depends on exactly these two)."""
    import scar.cli as cli
    labels = tmp_path / "labels.jsonl"
    monkeypatch.setattr(cli, "LABELS_PATH_OVERRIDE", labels)
    cid = _first_candidate_id(harvest_repo)
    assert main(["harvest", str(harvest_repo), "--label", cid, "maybe"]) == 1
    out = capsys.readouterr().out
    assert "keep" in out.lower() and "discard" in out.lower()
    assert not labels.exists()


def test_harvest_label_date_is_monkeypatchable(harvest_repo, tmp_path, monkeypatch):
    """The date field comes from time.strftime, monkeypatchable for determinism."""
    import scar.cli as cli
    labels = tmp_path / "labels.jsonl"
    monkeypatch.setattr(cli, "LABELS_PATH_OVERRIDE", labels)
    monkeypatch.setattr(cli.time, "strftime", lambda fmt: "1999-12-31")
    cid = _first_candidate_id(harvest_repo)
    main(["harvest", str(harvest_repo), "--label", cid, "keep"])
    rec = json.loads(labels.read_text(encoding="utf-8").splitlines()[0])
    assert rec["date"] == "1999-12-31"


def test_labels_path_defaults_under_scars_dir(harvest_repo):
    # #106: experiments/harvest/labels.jsonl leaked an untracked dir into every
    # adopter's working tree. Default now lives inside .scars/ instead.
    import scar.cli as cli
    assert cli.LABELS_PATH_OVERRIDE is None
    assert cli._labels_path(harvest_repo) == harvest_repo / ".scars" / "harvest-labels.jsonl"


def test_harvest_label_writes_to_new_default_path_not_old(harvest_repo):
    cid = _first_candidate_id(harvest_repo)
    assert main(["harvest", str(harvest_repo), "--label", cid, "keep"]) == 0
    assert (harvest_repo / ".scars" / "harvest-labels.jsonl").exists()
    assert not (harvest_repo / "experiments" / "harvest" / "labels.jsonl").exists()


# --- harvest --write (#136: cold-start bridge) ---

@pytest.fixture
def harvest_write_repo(tmp_path):
    """Synthetic history with LIVE-path signals (unlike harvest_repo, whose
    signal paths all die): a surviving flapping config, a DO-NOT comment on a
    tracked file, a revert whose file survives — plus an initialized .scars/."""
    work = tmp_path / "work"
    work.mkdir()
    _git(work.parent, "init", "-q", "-b", "main", str(work))
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    cfg = work / "deploy.yaml"
    cfg.write_text("replicas: 1\n")
    (work / "keep.py").write_text("x = 1  # DO NOT remove: breaks prod boot\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "feat: initial config")
    cfg.write_text("replicas: 3\n")
    _git(work, "commit", "-qam", "feat: scale up")
    cfg.write_text("replicas: 1\n")
    _git(work, "commit", "-qam", "fix: revert replicas, instance broke")
    init_scars(work)
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "chore: scar init")
    return work


def test_harvest_write_creates_reviewable_candidate_files(harvest_write_repo, capsys):
    """--write N renders the top-N live-anchored candidates as candidate files
    in .scars/candidates/ — parseable, lint-clean, status candidate, with a
    provenance body pointing the human at promote-or-delete (#136)."""
    from scar.store import parse_scar_text
    from scar.lint import lint_text
    cand_dir = harvest_write_repo / ".scars" / "candidates"
    before = set(cand_dir.glob("*.md"))
    assert main(["harvest", str(harvest_write_repo), "--write", "2"]) == 0
    out = capsys.readouterr().out
    written = set(cand_dir.glob("*.md")) - before
    assert len(written) == 2
    for f in written:
        text = f.read_text(encoding="utf-8")
        scar = parse_scar_text(text)
        assert scar.status == "candidate"
        assert scar.type in ("deadend", "fence")
        assert scar.path_anchors, f"{f.name}: no path anchor"
        assert scar.evidence, f"{f.name}: no evidence"
        assert "harvest" in scar.body.lower()
        # The promote instruction must be runnable as written: promote matches
        # against candidate FILENAMES, so a .scars/candidates/ path prefix
        # makes the suggested command fail (verified live).
        assert f"`scar promote {f.name}`" in scar.body
        errors = [fi for fi in lint_text(text) if fi.level == "error"]
        assert errors == [], f"{f.name}: lint errors {errors}"
        assert f.name in out  # each written file is reported


def test_harvest_write_anchors_only_tracked_paths(harvest_repo, capsys):
    """Candidates whose paths are gone from the tree (harvest_repo's all die)
    are skipped — a dead anchor would be an orphan at birth — and the skip is
    reported, never silent (#136)."""
    init_scars(harvest_repo)
    cand_dir = harvest_repo / ".scars" / "candidates"
    before = set(cand_dir.glob("*.md"))
    assert main(["harvest", str(harvest_repo), "--write", "5"]) == 0
    out = capsys.readouterr().out.lower()
    written = set(cand_dir.glob("*.md")) - before
    tracked = set(subprocess.run(
        ["git", "ls-files"], cwd=harvest_repo, capture_output=True,
        text=True, check=True).stdout.splitlines())
    from scar.store import parse_scar_text
    for f in written:
        scar = parse_scar_text(f.read_text(encoding="utf-8"))
        for anchor in scar.path_anchors:
            assert anchor in tracked, f"{f.name}: dead anchor {anchor}"
    assert "skipped" in out


def test_harvest_write_is_idempotent(harvest_write_repo, capsys):
    """A second --write run must not duplicate or overwrite existing candidate
    files — same slugs are skipped and reported (#136)."""
    cand_dir = harvest_write_repo / ".scars" / "candidates"
    assert main(["harvest", str(harvest_write_repo), "--write", "2"]) == 0
    first = {f.name: f.read_text(encoding="utf-8") for f in cand_dir.glob("*.md")}
    capsys.readouterr()
    assert main(["harvest", str(harvest_write_repo), "--write", "2"]) == 0
    out = capsys.readouterr().out.lower()
    second = {f.name: f.read_text(encoding="utf-8") for f in cand_dir.glob("*.md")}
    assert second == first
    assert "exist" in out


def test_harvest_write_rejects_over_cap(harvest_write_repo, capsys):
    """--write is hard-capped: mined candidates are ~13% precision, so dumping
    dozens of drafts buries the reviewer (#136)."""
    assert main(["harvest", str(harvest_write_repo), "--write", "25"]) == 1
    out = capsys.readouterr().out
    assert "20" in out  # the cap is named


def test_harvest_write_requires_scars_dir(tmp_path, capsys):
    """--write needs .scars/candidates/ to exist — point the user at scar init
    instead of scattering files (#136)."""
    work = tmp_path / "bare"
    work.mkdir()
    _git(work.parent, "init", "-q", "-b", "main", str(work))
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    (work / "a.py").write_text("x = 1  # DO NOT remove\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "feat: a")
    assert main(["harvest", str(work), "--write", "1"]) == 1
    assert "scar init" in capsys.readouterr().out


def test_harvest_precision_reads_fall_back_to_old_path_when_new_absent(harvest_repo, capsys):
    # An existing local label set at the pre-#106 location must not be
    # silently orphaned by the path move — reads fall back to it.
    cid = _first_candidate_id(harvest_repo)
    old_path = harvest_repo / "experiments" / "harvest" / "labels.jsonl"
    old_path.parent.mkdir(parents=True)
    old_path.write_text(json.dumps({"id": cid, "label": "keep", "note": "",
                                    "date": "2026-01-01", "repo": harvest_repo.name}) + "\n")
    assert main(["harvest", str(harvest_repo), "--precision"]) == 0
    out = capsys.readouterr().out.lower()
    assert "1 labeled" in out


def test_harvest_precision_prefers_new_path_when_both_exist(harvest_repo, capsys):
    # New writes never touch the old location again — once the new path
    # exists, it is authoritative even if a stale old file lingers.
    cid = _first_candidate_id(harvest_repo)
    old_path = harvest_repo / "experiments" / "harvest" / "labels.jsonl"
    old_path.parent.mkdir(parents=True)
    old_path.write_text(json.dumps({"id": "stale-old-entry", "label": "keep", "note": "",
                                    "date": "2026-01-01", "repo": harvest_repo.name}) + "\n")
    assert main(["harvest", str(harvest_repo), "--label", cid, "discard"]) == 0
    capsys.readouterr()
    assert main(["harvest", str(harvest_repo), "--precision"]) == 0
    out = capsys.readouterr().out.lower()
    assert "1 labeled" in out  # only the new file's one label counted


def test_agent_skill_prints_body_with_three_types(repo, capsys):
    assert main(["agent", "skill"]) == 0
    out = capsys.readouterr().out
    assert "name: scar-authoring" in out
    for kind in ("deadend", "fence", "landmine"):
        assert kind in out


# ---------------------------------------------------------------------------
# Rich output (Issue #78): the five human-facing read commands gain a 3-way
# surface — --json (machine), Rich (tty), plain (non-tty, byte-preserved). The
# plain non-tty assertions live in the tests above; here we cover --json keys
# and that the Rich tty path renders without crashing.
# ---------------------------------------------------------------------------


def _force_tty(monkeypatch):
    """Force the tty branch so the Rich renderer runs (under capsys stdout is
    never a real tty). We assert no crash + exit 0, never exact ANSI."""
    import scar.output as out
    monkeypatch.setattr(out, "is_tty", lambda: True)


def test_status_json_emits_structured_counts(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "candidates" / "x.md").write_text(CANDIDATE)
    assert main(["status", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["counts"]["candidates"] == 1
    assert data["counts"]["active"] == 0
    assert isinstance(data["active"], list)


def test_status_tty_renders_scar_data(repo, capsys, monkeypatch):
    init_scars(repo)
    (repo / ".scars" / "candidates" / "x.md").write_text(CANDIDATE)
    _force_tty(monkeypatch)
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    # a renderer that dropped the candidate would still exit 0 — assert the data.
    assert "x.md" in out           # the pending candidate is named
    assert "1 candidate" in out    # and counted


def test_lint_json_emits_findings_and_summary(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "0001-gone.deadend.md").write_text(ORPHAN_SCAR)
    assert main(["lint", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["files"] >= 1
    assert any(o["scar_id"] == 1 for o in data["orphans"])
    assert "failed" in data


def test_lint_json_broken_scar_exit_one_and_lists_file(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "0001-bad.deadend.md").write_text("# nope\n")
    assert main(["lint", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert any("0001-bad.deadend.md" in f["file"] for f in data["findings"])


def test_lint_tty_renders_orphan_finding(repo, capsys, monkeypatch):
    init_scars(repo)
    (repo / ".scars" / "0001-gone.deadend.md").write_text(ORPHAN_SCAR)
    _force_tty(monkeypatch)
    assert main(["lint"]) == 0
    out = capsys.readouterr().out
    assert "scar #1" in out           # the orphan finding surfaces its id
    assert "src/long_gone/" in out    # ...and the dead anchor path
    assert "orphan" in out.lower()


def test_check_json_lists_anchored_scars(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "candidates" / "tried-x.md").write_text(CANDIDATE)
    main(["promote", "tried-x", "--reviewer", "k"])
    (repo / "src").mkdir()
    capsys.readouterr()
    assert main(["check", "src/thing.py", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["path"] == "src/thing.py"
    assert any(s["title"] == "Tried X, failed" for s in data["scars"])


def test_check_tty_renders_scar_content(repo, capsys, monkeypatch):
    init_scars(repo)
    (repo / ".scars" / "candidates" / "tried-x.md").write_text(CANDIDATE)
    main(["promote", "tried-x", "--reviewer", "k"])
    (repo / "src").mkdir()
    capsys.readouterr()  # flush promote output
    _force_tty(monkeypatch)
    assert main(["check", "src/thing.py"]) == 0
    out = capsys.readouterr().out
    assert "Tried X, failed" in out  # the anchored scar's title
    assert "deadend" in out          # its type
    assert "Why X failed" in out     # its body


def test_check_default_no_exit_code_always_zero(repo, capsys):
    # #106: back-compat — without --exit-code, check never fails CI even when
    # a scar fires. Only --exit-code turns check into a gate.
    init_scars(repo)
    (repo / ".scars" / "candidates" / "tried-x.md").write_text(CANDIDATE)
    main(["promote", "tried-x", "--reviewer", "k"])
    (repo / "src").mkdir()
    assert main(["check", "src/thing.py"]) == 0


def test_check_exit_code_fires_returns_nonzero(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "candidates" / "tried-x.md").write_text(CANDIDATE)
    main(["promote", "tried-x", "--reviewer", "k"])
    (repo / "src").mkdir()
    assert main(["check", "src/thing.py", "--exit-code"]) == 1


def test_check_exit_code_clean_path_returns_zero(repo, capsys):
    init_scars(repo)
    assert main(["check", "docs/x.md", "--exit-code"]) == 0


def test_check_multiple_paths_union(repo, capsys):
    # #106: check accepts several paths in one call and gates on their union.
    init_scars(repo)
    (repo / ".scars" / "candidates" / "tried-x.md").write_text(CANDIDATE)
    main(["promote", "tried-x", "--reviewer", "k"])
    (repo / "src").mkdir()
    capsys.readouterr()
    assert main(["check", "docs/x.md", "src/thing.py", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["paths"] == ["docs/x.md", "src/thing.py"]
    assert any(s["title"] == "Tried X, failed" for s in data["scars"])


def test_check_multiple_paths_exit_code_fires_if_any_hits(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "candidates" / "tried-x.md").write_text(CANDIDATE)
    main(["promote", "tried-x", "--reviewer", "k"])
    (repo / "src").mkdir()
    assert main(["check", "docs/x.md", "src/thing.py", "--exit-code"]) == 1


def test_check_diff_mode_gates_on_union_of_changed_files(repo, capsys):
    # #106: --diff mirrors inject --diff's file-discovery (match._diff_targets)
    # so check can gate a whole PR diff, not just one path at a time.
    init_scars(repo)
    (repo / ".scars" / "candidates" / "tried-x.md").write_text(CANDIDATE)
    main(["promote", "tried-x", "--reviewer", "k"])
    capsys.readouterr()
    diff = """\
diff --git a/src/thing.py b/src/thing.py
--- a/src/thing.py
+++ b/src/thing.py
@@ -0,0 +1 @@
+print("x")
"""
    assert main(["check", "--diff", diff, "--exit-code"]) == 1
    out = capsys.readouterr().out
    assert "Tried X, failed" in out


def test_check_diff_mode_clean_returns_zero(repo, capsys):
    init_scars(repo)
    diff = """\
diff --git a/docs/x.md b/docs/x.md
--- a/docs/x.md
+++ b/docs/x.md
@@ -0,0 +1 @@
+hello
"""
    assert main(["check", "--diff", diff, "--exit-code"]) == 0


# ---------------------------------------------------------------------------
# Task 5: `scar check --diff` surfaces violations (violation field, Task 4's
# find_violations_for_diff feeding the CLI)
# ---------------------------------------------------------------------------

VIOLATION_SCAR = """\
---
id: 42
type: fence
title: No raw sleep calls
severity: critical
confidence: 0.9
created: 2026-06-10
authors: ["claude-code"]
anchors:
  - path: src/
evidence:
  - commit: abc1234
violation: "sleep\\((?:[0-6])\\)"
status: active
---

Do not call sleep with a small value.
"""

DIFF_WITH_VIOLATION = """\
diff --git a/src/thing.py b/src/thing.py
--- a/src/thing.py
+++ b/src/thing.py
@@ -0,0 +1 @@
+time.sleep(2)
"""


def test_check_diff_violation_appears_in_json_exit_zero_without_flag(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "0042-nosleep.fence.md").write_text(VIOLATION_SCAR)
    capsys.readouterr()
    assert main(["check", "--diff", DIFF_WITH_VIOLATION, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["violations"] == [{
        "scar_id": 42,
        "title": "No raw sleep calls",
        "path": "src/thing.py",
        "excerpt": "time.sleep(2)",
    }]


def test_check_diff_violation_exit_code_returns_nonzero(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "0042-nosleep.fence.md").write_text(VIOLATION_SCAR)
    assert main(["check", "--diff", DIFF_WITH_VIOLATION, "--exit-code"]) == 1


def test_check_diff_scars_fire_but_no_violation_gives_empty_list(repo, capsys):
    # #106-adjacent: a scar can fire on a diff via its path anchor without its
    # (absent or non-matching) violation regex ever tripping — violations must
    # report [] rather than being conflated with the scars list.
    init_scars(repo)
    (repo / ".scars" / "candidates" / "tried-x.md").write_text(CANDIDATE)
    main(["promote", "tried-x", "--reviewer", "k"])
    capsys.readouterr()
    diff = """\
diff --git a/src/thing.py b/src/thing.py
--- a/src/thing.py
+++ b/src/thing.py
@@ -0,0 +1 @@
+print("x")
"""
    assert main(["check", "--diff", diff, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["scars"]  # the scar did fire (path anchor)
    assert data["violations"] == []


def test_check_exit_code_trips_on_violation_even_with_zero_top_k(repo, capsys):
    # Violations bypass --top-k truncation — even when scars list is empty,
    # exit-code should trip if violations are found. This pins the
    # `(hits or violations)` OR-branch at line 546 of cli.py.
    init_scars(repo)
    (repo / ".scars" / "0042-nosleep.fence.md").write_text(VIOLATION_SCAR)
    capsys.readouterr()
    assert main(["check", "--diff", DIFF_WITH_VIOLATION, "--exit-code", "--top-k", "0", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["scars"] == []  # --top-k 0 truncates all scars
    assert len(data["violations"]) == 1  # but violation still present
    assert data["violations"][0]["scar_id"] == 42


def test_check_path_mode_json_has_no_violations_key(repo, capsys):
    # path (non-diff) mode is unchanged: no violations key at all.
    init_scars(repo)
    (repo / "src").mkdir()
    capsys.readouterr()
    assert main(["check", "src/thing.py", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert "violations" not in data


def test_why_json_lists_records(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "candidates" / "tried-x.md").write_text(CANDIDATE)
    main(["promote", "tried-x", "--reviewer", "k"])
    (repo / "src").mkdir()
    capsys.readouterr()
    assert main(["why", "src", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert any(r["title"] == "Tried X, failed" for r in data["records"])


def test_why_tty_renders_scar_history(repo, capsys, monkeypatch):
    init_scars(repo)
    (repo / ".scars" / "candidates" / "tried-x.md").write_text(CANDIDATE)
    main(["promote", "tried-x", "--reviewer", "k"])
    (repo / "src").mkdir()
    capsys.readouterr()  # flush promote output
    _force_tty(monkeypatch)
    assert main(["why", "src"]) == 0
    out = capsys.readouterr().out
    assert "Tried X, failed" in out  # the recorded scar's title
    assert "deadend" in out          # its type
    assert "src" in out              # scoped to the queried path


def test_orphan_json_lists_detected(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "0005-both.deadend.md").write_text(MULTI_ANCHOR_ORPHAN)
    assert main(["orphan", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert any(o["scar_id"] == 5 for o in data["orphan_detected"])


def test_orphan_tty_renders_dead_anchor_detail(repo, capsys, monkeypatch):
    init_scars(repo)
    (repo / ".scars" / "0005-both.deadend.md").write_text(MULTI_ANCHOR_ORPHAN)
    _force_tty(monkeypatch)
    assert main(["orphan"]) == 0
    out = capsys.readouterr().out
    # the dead anchor's identifying detail must survive into the report so a
    # renderer that silently dropped the finding is caught.
    assert "orphan-detected" in out
    assert "src/dead_dir/" in out       # the dead path anchor
    assert "1 orphan(s) detected" in out


# ---------------------------------------------------------------------------
# Symbol drift surfaced in lint (#99 Phase 3 finale) — advisory only, never
# changes the lint exit code.
# ---------------------------------------------------------------------------

@symbols_extra
def test_lint_json_includes_symbol_drift(repo, capsys):
    def git(*a):
        subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)

    git("config", "user.email", "t@t.t"); git("config", "user.name", "t")
    src = repo / "store.py"
    src.write_text("class SessionStore:\n    def save(self):\n        x = 1\n        return x\n")
    git("add", "-A"); git("commit", "-q", "-m", "base")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                         capture_output=True, text=True, check=True).stdout.strip()

    scars = repo / ".scars"; scars.mkdir()
    (scars / "s.md").write_text(
        "---\ntype: deadend\ntitle: t\nseverity: medium\nconfidence: 1.0\n"
        "anchors:\n  - path: store.py\n  - symbol: SessionStore.save\n"
        f"evidence:\n  - commit: {sha}\nstatus: active\n---\nbody\n")
    git("add", "-A"); git("commit", "-q", "-m", "add scar")
    src.write_text("class SessionStore:\n    def save(self):\n        return compute(other())\n")
    git("add", "-A"); git("commit", "-q", "-m", "rewrite save")

    capsys.readouterr()  # flush
    rc = main(["lint", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0  # advisory: drift never fails lint
    drift = data["symbol_drift"]
    assert len(drift) == 1
    assert drift[0]["symbol"] == "SessionStore.save"
    assert drift[0]["sha"] == sha


# --- stats (#106: firing observability) ---

def _write_firing_log(state_dir, records):
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "firing-log.jsonl").open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def test_stats_no_firings_yet(repo, capsys, monkeypatch):
    init_scars(repo)
    (repo / ".scars" / "0001-a.deadend.md").write_text(_active_scar(1, "Scar one"))
    monkeypatch.setenv("SCAR_STATE_DIR", str(repo / "state"))
    assert main(["stats", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total_firings"] == 0
    assert data["per_scar"] == []
    assert data["most_fired"] is None
    assert data["last_fired"] is None
    assert data["never_fired"] == [1]


def test_stats_aggregates_counts_and_never_fired(repo, capsys, monkeypatch):
    init_scars(repo)
    (repo / ".scars" / "0001-a.deadend.md").write_text(_active_scar(1, "Scar one"))
    (repo / ".scars" / "0002-b.deadend.md").write_text(_active_scar(2, "Scar two"))
    monkeypatch.setenv("SCAR_STATE_DIR", str(repo / "state"))
    _write_firing_log(repo / "state", [
        {"ts": "2026-06-10T10:00:00", "repo": str(repo), "target": "src/a.py",
         "scar_ids": [1], "count": 1},
        {"ts": "2026-06-11T09:00:00", "repo": str(repo), "target": "src/a.py",
         "scar_ids": [1, 2], "count": 2},
    ])
    assert main(["stats", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total_firings"] == 3
    assert {"id": 1, "count": 2, "violations": 0} in data["per_scar"]
    assert {"id": 2, "count": 1, "violations": 0} in data["per_scar"]
    assert data["most_fired"] == 1
    assert data["last_fired"] == "2026-06-11T09:00:00"
    assert data["never_fired"] == []


def test_stats_plain_output_reports_never_fired_and_disclaimer(repo, capsys, monkeypatch):
    init_scars(repo)
    (repo / ".scars" / "0001-a.deadend.md").write_text(_active_scar(1, "Scar one"))
    monkeypatch.setenv("SCAR_STATE_DIR", str(repo / "state"))
    assert main(["stats"]) == 0
    out = capsys.readouterr().out
    assert "0" in out
    assert "never fired" in out.lower()
    assert "#1" in out
    # scope honesty (#106): must not claim honor-tracking, only firing counts
    assert "honor" in out.lower()


def _stats_log(repo, monkeypatch, entries):
    state = repo / "state"
    state.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    with open(state / "firing-log.jsonl", "a", encoding="utf-8") as fh:
        for scar_ids in entries:
            fh.write(json.dumps({"ts": "2026-07-02T10:00:00", "repo": str(repo),
                                 "target": "x", "scar_ids": scar_ids,
                                 "count": len(scar_ids)}) + "\n")


def test_stats_advisory_on_skewed_distribution(repo, capsys, monkeypatch):
    init_scars(repo)
    (repo / ".scars" / "0001-a.deadend.md").write_text(_active_scar(1, "Scar one"))
    (repo / ".scars" / "0002-b.deadend.md").write_text(_active_scar(2, "Scar two"))
    _stats_log(repo, monkeypatch, [[1]] * 30 + [[2]] * 2)
    assert main(["stats", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["advisories"][0]["id"] == 1
    assert "over-broad" in data["advisories"][0]["note"]


def test_stats_no_advisory_below_thresholds(repo, capsys, monkeypatch):
    init_scars(repo)
    (repo / ".scars" / "0001-a.deadend.md").write_text(_active_scar(1, "Scar one"))
    (repo / ".scars" / "0002-b.deadend.md").write_text(_active_scar(2, "Scar two"))
    _stats_log(repo, monkeypatch, [[1]] * 5 + [[2]] * 5)
    assert main(["stats", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["advisories"] == []


def test_stats_counts_violations_from_log(repo, capsys, monkeypatch):
    """Violation records in the firing log contribute violation_ids counts
    to per_scar entries; a scar can have violations without firing."""
    init_scars(repo)
    (repo / ".scars" / "0001-a.deadend.md").write_text(_active_scar(1, "Scar one"))
    (repo / ".scars" / "0002-b.deadend.md").write_text(_active_scar(2, "Scar two"))
    monkeypatch.setenv("SCAR_STATE_DIR", str(repo / "state"))
    _write_firing_log(repo / "state", [
        {"ts": "2026-06-10T10:00:00", "repo": str(repo), "target": "src/a.py",
         "scar_ids": [1], "count": 1},
        {"ts": "2026-06-11T09:00:00", "repo": str(repo), "target": "src/a.py",
         "scar_ids": [1], "count": 1},
        {"ts": "2026-06-12T08:00:00", "repo": str(repo), "target": "src/b.py",
         "violation_ids": [1]},
    ])
    assert main(["stats", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total_firings"] == 2
    per_scar_1 = [e for e in data["per_scar"] if e["id"] == 1][0]
    assert per_scar_1 == {"id": 1, "count": 2, "violations": 1}


def test_stats_most_fired_excludes_zero_firing_scar(repo, capsys, monkeypatch):
    """A scar that only has a violation record (never actually fired) must
    not be reported as most_fired while simultaneously appearing in
    never_fired — that is contradictory."""
    init_scars(repo)
    (repo / ".scars" / "0001-a.deadend.md").write_text(_active_scar(1, "Scar one"))
    monkeypatch.setenv("SCAR_STATE_DIR", str(repo / "state"))
    _write_firing_log(repo / "state", [
        {"ts": "2026-06-12T08:00:00", "repo": str(repo), "target": "src/b.py",
         "violation_ids": [1]},
    ])
    assert main(["stats", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["most_fired"] is None
    assert 1 in data["never_fired"]
    per_scar_1 = [e for e in data["per_scar"] if e["id"] == 1][0]
    assert per_scar_1["count"] == 0
    assert per_scar_1["violations"] == 1


def test_stats_rejects_string_violation_ids(repo, capsys, monkeypatch):
    """A malformed record with violation_ids as a string (not a list) must be
    skipped entirely, not iterated character-by-character into phantom
    per_scar entries with string ids."""
    init_scars(repo)
    (repo / ".scars" / "0001-a.deadend.md").write_text(_active_scar(1, "Scar one"))
    monkeypatch.setenv("SCAR_STATE_DIR", str(repo / "state"))
    _write_firing_log(repo / "state", [
        {"ts": "2026-06-10T10:00:00", "repo": str(repo), "target": "src/a.py",
         "scar_ids": [1], "count": 1},
        {"ts": "2026-06-11T09:00:00", "repo": str(repo), "target": "src/b.py",
         "violation_ids": "12"},
    ])
    assert main(["stats", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    ids = [e["id"] for e in data["per_scar"]]
    assert "1" not in ids
    assert "2" not in ids
    per_scar_1 = [e for e in data["per_scar"] if e["id"] == 1][0]
    assert per_scar_1["count"] == 1
    assert per_scar_1["violations"] == 0


def test_stats_violations_default_to_zero_no_crash(repo, capsys, monkeypatch):
    """Old-format firing logs with no violation records do not crash;
    violations field defaults to 0."""
    init_scars(repo)
    (repo / ".scars" / "0001-a.deadend.md").write_text(_active_scar(1, "Scar one"))
    monkeypatch.setenv("SCAR_STATE_DIR", str(repo / "state"))
    _write_firing_log(repo / "state", [
        {"ts": "2026-06-10T10:00:00", "repo": str(repo), "target": "src/a.py",
         "scar_ids": [1], "count": 1},
    ])
    assert main(["stats", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    per_scar_1 = [e for e in data["per_scar"] if e["id"] == 1][0]
    assert per_scar_1["violations"] == 0


def test_stats_filters_out_other_repos_records(repo, capsys, monkeypatch):
    """The firing log is machine-global; stats must scope to the current repo
    (#137). Scar ids are per-repo sequential ints, so a foreign repo's #1 is a
    DIFFERENT scar than this repo's #1 — counting it both inflates the count
    and corrupts never-fired/advisory math."""
    init_scars(repo)
    (repo / ".scars" / "0001-a.deadend.md").write_text(_active_scar(1, "Scar one"))
    (repo / ".scars" / "0002-b.deadend.md").write_text(_active_scar(2, "Scar two"))
    monkeypatch.setenv("SCAR_STATE_DIR", str(repo / "state"))
    _write_firing_log(repo / "state", [
        {"ts": "2026-06-10T10:00:00", "repo": str(repo), "target": "src/a.py",
         "scar_ids": [1], "count": 1},
        # Foreign repo: colliding id (#1) and an id this repo doesn't have (#9)
        {"ts": "2026-06-12T10:00:00", "repo": "/somewhere/else", "target": "x",
         "scar_ids": [1, 9], "count": 2},
        {"ts": "2026-06-13T10:00:00", "repo": "/somewhere/else", "target": "x",
         "violation_ids": [1], "count": 1},
    ])
    assert main(["stats", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["repo"] == str(repo)
    assert data["total_firings"] == 1
    assert data["per_scar"] == [{"id": 1, "count": 1, "violations": 0}]
    assert data["last_fired"] == "2026-06-10T10:00:00"
    assert data["never_fired"] == [2]


def test_stats_excludes_records_missing_repo_field(repo, capsys, monkeypatch):
    """A record with no repo field cannot be attributed to this repo — exclude
    it from the scoped view rather than guessing (#137)."""
    init_scars(repo)
    (repo / ".scars" / "0001-a.deadend.md").write_text(_active_scar(1, "Scar one"))
    monkeypatch.setenv("SCAR_STATE_DIR", str(repo / "state"))
    _write_firing_log(repo / "state", [
        {"ts": "2026-06-10T10:00:00", "target": "src/a.py",
         "scar_ids": [1], "count": 1},
    ])
    assert main(["stats", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total_firings"] == 0
    assert data["never_fired"] == [1]


def test_stats_survives_non_dict_log_lines(repo, capsys, monkeypatch):
    """The log is written best-effort from a fail-open hook, so any JSON shape
    can appear (landmine #12): `null`, `[]`, bare numbers. Valid JSON that
    isn't a dict must be skipped, not crash the reader at rec.get()."""
    init_scars(repo)
    (repo / ".scars" / "0001-a.deadend.md").write_text(_active_scar(1, "Scar one"))
    state = repo / "state"
    state.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    (state / "firing-log.jsonl").write_text(
        "null\n[]\n42\n"
        + json.dumps({"ts": "2026-06-10T10:00:00", "repo": str(repo),
                      "target": "x", "scar_ids": [1], "count": 1}) + "\n")
    assert main(["stats", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total_firings"] == 1


def test_stats_all_repos_groups_per_repo_without_merging_ids(repo, capsys, monkeypatch):
    """--all-repos shows the whole machine-global log grouped per repo; ids
    from different repos are never summed into one row (#137)."""
    init_scars(repo)
    (repo / ".scars" / "0001-a.deadend.md").write_text(_active_scar(1, "Scar one"))
    monkeypatch.setenv("SCAR_STATE_DIR", str(repo / "state"))
    _write_firing_log(repo / "state", [
        {"ts": "2026-06-10T10:00:00", "repo": str(repo), "target": "src/a.py",
         "scar_ids": [1], "count": 1},
        {"ts": "2026-06-12T10:00:00", "repo": "/somewhere/else", "target": "x",
         "scar_ids": [1, 9], "count": 2},
        {"ts": "2026-06-13T10:00:00", "repo": "/somewhere/else", "target": "x",
         "violation_ids": [1], "count": 1},
    ])
    assert main(["stats", "--all-repos", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["all_repos"] is True
    groups = {g["repo"]: g for g in data["repos"]}
    assert groups[str(repo)]["total_firings"] == 1
    assert groups[str(repo)]["per_scar"] == [{"id": 1, "count": 1, "violations": 0}]
    other = groups["/somewhere/else"]
    assert other["total_firings"] == 2
    assert {"id": 1, "count": 1, "violations": 1} in other["per_scar"]
    assert {"id": 9, "count": 1, "violations": 0} in other["per_scar"]


# ---------------------------------------------------------------------------
# scar reanchor (#111) — orphan recovery v1: propose new anchors for
# orphaned/partial-rot scars. Propose-only by default, read-only. --apply
# requires --id, rewrites only single-high-confidence+tracked anchors,
# never flips status.
# ---------------------------------------------------------------------------

def _reanchor_scar(*, id: int, path_anchors=(), pattern_anchors=(), symbol_anchors=()) -> str:
    anchor_lines = ""
    for p in path_anchors:
        anchor_lines += f"  - path: {p}\n"
    for pat in pattern_anchors:
        anchor_lines += f'  - pattern: "{pat}"\n'
    for s in symbol_anchors:
        anchor_lines += f"  - symbol: {s}\n"
    return (
        f"---\n"
        f"id: {id}\n"
        f"type: deadend\n"
        f"title: reanchor test scar {id}\n"
        f"severity: medium\n"
        f"confidence: 0.8\n"
        f"created: 2026-06-10\n"
        f'authors: ["claude-code"]\n'
        f"anchors:\n"
        f"{anchor_lines}"
        f"evidence:\n"
        f"  - commit: abc1234\n"
        f"status: active\n"
        f"---\n\n"
        f"Body text.\n"
    )


def _init_bare_git(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")


def _pad(tag: str, n: int = 10) -> str:
    """Padding lines that make a same-commit move fall BELOW git's own -M
    rename-similarity threshold (so build_rename_map/#109 does not silently
    claim the anchor first) while staying well ABOVE reanchor's own overlap
    threshold (which only counts what survives from the OLD file, not what
    was added to the new one)."""
    return "".join(f"def pad_{tag}_{i}():\n    return 'padding {tag} line number {i} here'\n"
                   for i in range(n))


def test_reanchor_proposes_high_confidence_for_same_commit_move(tmp_path, monkeypatch, capsys):
    _init_bare_git(tmp_path)
    init_scars(tmp_path)
    content = ("def hello():\n    return 'world from old module'\n\n"
               "def helper():\n    return 42\n")
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "old_module.py").write_text(content)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add old_module")
    (tmp_path / "src" / "old_module.py").unlink()
    # Padded so git's OWN -M rename heuristic doesn't already claim this
    # (that's #109's territory) — reanchor's overlap ratio only counts what
    # survives from old_module.py, so padding doesn't dilute it.
    (tmp_path / "src" / "new_module.py").write_text(content + _pad("a"))
    f = tmp_path / ".scars" / "0020-moved.deadend.md"
    f.write_text(_reanchor_scar(id=20, path_anchors=["src/old_module.py"]))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "move old_module to new_module + add scar")
    monkeypatch.chdir(tmp_path)

    before = f.read_text()
    assert main(["reanchor"]) == 0
    out = capsys.readouterr().out
    assert "#20" in out
    assert "src/old_module.py" in out
    assert "src/new_module.py" in out
    assert "high" in out.lower()
    assert f.read_text() == before  # read-only by default


def test_reanchor_finds_later_commit_move_via_pickaxe(tmp_path, monkeypatch, capsys):
    _init_bare_git(tmp_path)
    init_scars(tmp_path)
    content = ("def hello():\n    return 'a very distinctive marker string here'\n\n"
               "def helper():\n    return 99\n")
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "old_module.py").write_text(content)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add old_module")
    (tmp_path / "src" / "old_module.py").unlink()
    f = tmp_path / ".scars" / "0021-moved.deadend.md"
    f.write_text(_reanchor_scar(id=21, path_anchors=["src/old_module.py"]))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "delete old_module + add scar")
    (tmp_path / "README.md").write_text("unrelated intervening change\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "unrelated change")
    (tmp_path / "src" / "new_module.py").write_text(content)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add new_module elsewhere")
    monkeypatch.chdir(tmp_path)

    assert main(["reanchor"]) == 0
    out = capsys.readouterr().out
    assert "#21" in out
    assert "src/new_module.py" in out


def test_reanchor_truly_deleted_no_proposal(tmp_path, monkeypatch, capsys):
    _init_bare_git(tmp_path)
    init_scars(tmp_path)
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "gone.py").write_text(
        "def gone():\n    return 'nothing like this exists elsewhere'\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add gone")
    (tmp_path / "src" / "gone.py").unlink()
    (tmp_path / ".scars" / "0022-gone.deadend.md").write_text(
        _reanchor_scar(id=22, path_anchors=["src/gone.py"]))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "delete gone + add scar")
    monkeypatch.chdir(tmp_path)

    assert main(["reanchor"]) == 0
    out = capsys.readouterr().out
    assert "no reanchor proposals" in out.lower()


def test_reanchor_ambiguous_reports_both_and_apply_refuses(tmp_path, monkeypatch, capsys):
    _init_bare_git(tmp_path)
    init_scars(tmp_path)
    content = ("def shared():\n    return 'duplicated content across two files'\n\n"
               "def more():\n    return 7\n")
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "shared.py").write_text(content)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add shared")
    (tmp_path / "src" / "shared.py").unlink()
    # Distinct padding per copy so git's -M rename pairing (which greedily
    # matches ONE target and would otherwise silently resolve this via #109)
    # doesn't fire; both copies still overlap fully with the dead content.
    (tmp_path / "src" / "copy_one.py").write_text(content + _pad("one"))
    (tmp_path / "src" / "copy_two.py").write_text(content + _pad("two"))
    f = tmp_path / ".scars" / "0023-ambiguous.deadend.md"
    f.write_text(_reanchor_scar(id=23, path_anchors=["src/shared.py"]))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "delete shared, duplicate + add scar")
    monkeypatch.chdir(tmp_path)

    assert main(["reanchor"]) == 0
    out = capsys.readouterr().out
    assert "#23" in out
    assert "src/copy_one.py" in out
    assert "src/copy_two.py" in out

    before = f.read_text()
    assert main(["reanchor", "--apply", "--id", "23"]) == 0
    out2 = capsys.readouterr().out
    assert "ambiguous" in out2.lower()
    assert f.read_text() == before  # apply refuses; file untouched


def test_reanchor_apply_rewrites_exactly_one_line(tmp_path, monkeypatch, capsys):
    _init_bare_git(tmp_path)
    init_scars(tmp_path)
    content = ("def hello():\n    return 'world from old module'\n\n"
               "def helper():\n    return 42\n")
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "old_module.py").write_text(content)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add old_module")
    (tmp_path / "src" / "old_module.py").unlink()
    (tmp_path / "src" / "new_module.py").write_text(content + _pad("b"))
    f = tmp_path / ".scars" / "0024-moved.deadend.md"
    f.write_text(_reanchor_scar(id=24, path_anchors=["src/old_module.py"]))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "move old_module to new_module + add scar")
    monkeypatch.chdir(tmp_path)

    before = f.read_text()
    assert main(["reanchor", "--apply", "--id", "24"]) == 0
    out = capsys.readouterr().out
    assert "fixed" in out.lower()

    after = f.read_text()
    before_lines = before.split("\n")
    after_lines = after.split("\n")
    assert len(before_lines) == len(after_lines)
    diffs = [(b, a) for b, a in zip(before_lines, after_lines) if b != a]
    assert diffs == [("  - path: src/old_module.py", "  - path: src/new_module.py")]
    assert "status: active" in after  # no status flip


def test_reanchor_apply_multi_anchor_partial(tmp_path, monkeypatch, capsys):
    _init_bare_git(tmp_path)
    init_scars(tmp_path)
    content = ("def hello():\n    return 'world from old module'\n\n"
               "def helper():\n    return 42\n")
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "old_module.py").write_text(content)
    (tmp_path / "src" / "gone.py").write_text(
        "def gone():\n    return 'nothing like this exists elsewhere'\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add old_module + gone")
    (tmp_path / "src" / "old_module.py").unlink()
    (tmp_path / "src" / "new_module.py").write_text(content + _pad("c"))
    (tmp_path / "src" / "gone.py").unlink()
    f = tmp_path / ".scars" / "0025-multi.deadend.md"
    f.write_text(_reanchor_scar(id=25, path_anchors=["src/old_module.py", "src/gone.py"]))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "move one, delete other + add scar")
    monkeypatch.chdir(tmp_path)

    before = f.read_text()
    assert main(["reanchor", "--apply", "--id", "25"]) == 0
    out = capsys.readouterr().out
    assert "fixed" in out.lower()
    assert "not fixed" in out.lower()

    after = f.read_text()
    before_lines = before.split("\n")
    after_lines = after.split("\n")
    assert len(before_lines) == len(after_lines)
    diffs = [(b, a) for b, a in zip(before_lines, after_lines) if b != a]
    assert diffs == [("  - path: src/old_module.py", "  - path: src/new_module.py")]
    assert "  - path: src/gone.py" in after_lines  # untouched, no candidate found


def test_reanchor_partial_rot_finding_feeds_reanchor(tmp_path, monkeypatch, capsys):
    _init_bare_git(tmp_path)
    init_scars(tmp_path)
    content = ("def hello():\n    return 'world from old module'\n\n"
               "def helper():\n    return 42\n")
    (tmp_path / "src" / "live").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "live" / "mod.py").write_text("x = 1\n")
    (tmp_path / "src" / "old_module.py").write_text(content)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add live/ + old_module")
    (tmp_path / "src" / "old_module.py").unlink()
    (tmp_path / "src" / "new_module.py").write_text(content + _pad("d"))
    f = tmp_path / ".scars" / "0026-partial.landmine.md"
    f.write_text(_reanchor_scar(id=26, path_anchors=["src/live/", "src/old_module.py"]))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "move old_module, keep live/ + add scar")
    monkeypatch.chdir(tmp_path)

    assert main(["orphan"]) == 0
    out = capsys.readouterr().out
    assert "no orphan-detected scars" in out  # not an orphan — still firing on live/
    assert "1 partial-rot" in out

    assert main(["reanchor"]) == 0
    out2 = capsys.readouterr().out
    assert "#26" in out2
    assert "src/new_module.py" in out2


@symbols_extra
def test_reanchor_symbol_renamed_same_file(tmp_path, monkeypatch, capsys):
    _init_bare_git(tmp_path)
    init_scars(tmp_path)
    body = ("def old_helper(items):\n"
           "    total = 0\n"
           "    for item in items:\n"
           "        if item > 0:\n"
           "            total += item\n"
           "    return total\n")
    (tmp_path / "src" / "pkg").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "pkg" / "mod.py").write_text(body)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add old_helper")
    sha = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                         check=True, capture_output=True, text=True).stdout.strip()

    new_body = body.replace("old_helper", "new_helper")
    (tmp_path / "src" / "pkg" / "mod.py").write_text(new_body)
    f = tmp_path / ".scars" / "0027-symbol.deadend.md"
    scar_text = (
        "---\nid: 27\ntype: deadend\ntitle: symbol renamed in place\n"
        "severity: medium\nconfidence: 0.8\ncreated: 2026-06-10\n"
        'authors: ["claude-code"]\nanchors:\n'
        "  - path: src/pkg/mod.py\n  - symbol: old_helper\n"
        f"evidence:\n  - commit: {sha}\nstatus: active\n---\n\nBody.\n"
    )
    f.write_text(scar_text)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "rename old_helper to new_helper + add scar")
    monkeypatch.chdir(tmp_path)

    assert main(["reanchor"]) == 0
    out = capsys.readouterr().out
    assert "#27" in out
    assert "old_helper" in out
    assert "new_helper" in out
    assert "symbol" in out.lower()


@symbols_extra
def test_reanchor_symbol_relocated_to_new_file(tmp_path, monkeypatch, capsys):
    _init_bare_git(tmp_path)
    init_scars(tmp_path)
    body = ("def old_helper(items):\n"
           "    total = 0\n"
           "    for item in items:\n"
           "        if item > 0:\n"
           "            total += item\n"
           "    return total\n")
    (tmp_path / "src" / "pkg").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "pkg" / "mod.py").write_text(body)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add old_helper")
    sha = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                         check=True, capture_output=True, text=True).stdout.strip()

    (tmp_path / "src" / "pkg" / "mod.py").write_text("def unrelated():\n    pass\n")
    (tmp_path / "src" / "lib").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "lib" / "other.py").write_text(body.replace("old_helper", "new_helper"))
    f = tmp_path / ".scars" / "0028-symbol.deadend.md"
    scar_text = (
        "---\nid: 28\ntype: deadend\ntitle: symbol relocated\n"
        "severity: medium\nconfidence: 0.8\ncreated: 2026-06-10\n"
        'authors: ["claude-code"]\nanchors:\n'
        "  - path: src/pkg/mod.py\n  - symbol: old_helper\n"
        f"evidence:\n  - commit: {sha}\nstatus: active\n---\n\nBody.\n"
    )
    f.write_text(scar_text)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "relocate old_helper + add scar")
    monkeypatch.chdir(tmp_path)

    assert main(["reanchor"]) == 0
    out = capsys.readouterr().out
    assert "#28" in out
    assert "src/lib/other.py" in out
    assert "new_helper" in out


def test_reanchor_pattern_anchor_gets_last_matched_diagnostic(tmp_path, monkeypatch, capsys):
    _init_bare_git(tmp_path)
    init_scars(tmp_path)
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "mod.py").write_text("class OldClassName:\n    pass\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add OldClassName")
    (tmp_path / "src" / "mod.py").write_text("class Renamed:\n    pass\n")
    f = tmp_path / ".scars" / "0029-pattern.deadend.md"
    f.write_text(_reanchor_scar(id=29, pattern_anchors=["OldClassName"]))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "rename class + add scar")
    monkeypatch.chdir(tmp_path)

    before = f.read_text()
    assert main(["reanchor"]) == 0
    out = capsys.readouterr().out
    assert "#29" in out
    assert "last matched at" in out.lower()
    assert f.read_text() == before  # diagnostic only, never applyable


def test_reanchor_pattern_anchor_redos_prone_skips_diagnostic(tmp_path, monkeypatch, capsys):
    _init_bare_git(tmp_path)
    init_scars(tmp_path)
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "mod.py").write_text("x = 1\n")
    _git(tmp_path, "add", "-A")
    # A distinctive nested-quantifier literal that cannot coincidentally
    # match the boilerplate .scars/README.md / template.md content
    # init_scars() writes (unlike a generic "(a+)+b", which DOES match any
    # bare "ab" substring in that prose and would keep the anchor "alive").
    (tmp_path / ".scars" / "0030-redos.deadend.md").write_text(
        _reanchor_scar(id=30, pattern_anchors=["(zqzqz+)+zqEND"]))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add redos-prone pattern scar")
    monkeypatch.chdir(tmp_path)

    assert main(["reanchor"]) == 0
    out = capsys.readouterr().out
    assert "#30" in out
    assert "skipped" in out.lower()
    assert "redos" in out.lower() or "pathological" in out.lower()


def test_reanchor_degrades_cleanly_without_git(tmp_path, monkeypatch, capsys):
    init_scars(tmp_path)
    (tmp_path / ".scars" / "0031-x.deadend.md").write_text(
        _reanchor_scar(id=31, path_anchors=["src/x.py"]))
    monkeypatch.chdir(tmp_path)
    assert main(["reanchor"]) == 1
    out = capsys.readouterr().out
    assert "git unavailable" in out.lower()


def test_reanchor_degrades_cleanly_without_symbols_extra(tmp_path, monkeypatch, capsys):
    import scar.symbols as symbols_mod

    monkeypatch.setattr(symbols_mod, "symbols_available", lambda: False)
    _init_bare_git(tmp_path)
    init_scars(tmp_path)
    content = ("def hello():\n    return 'world from old module'\n\n"
               "def helper():\n    return 42\n")
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "old_module.py").write_text(content)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add old_module")
    (tmp_path / "src" / "old_module.py").unlink()
    (tmp_path / "src" / "new_module.py").write_text(content + _pad("e"))
    f = tmp_path / ".scars" / "0032-moved.deadend.md"
    f.write_text(_reanchor_scar(id=32, path_anchors=["src/old_module.py"],
                                symbol_anchors=["whatever"]))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "move old_module + add scar with symbol anchor")
    monkeypatch.chdir(tmp_path)

    assert main(["reanchor"]) == 0  # no crash without the extra
    out = capsys.readouterr().out
    assert "#32" in out  # path proposal unaffected
    assert "src/new_module.py" in out


def test_reanchor_json_output_matches_plain_facts(tmp_path, monkeypatch, capsys):
    _init_bare_git(tmp_path)
    init_scars(tmp_path)
    content = ("def hello():\n    return 'world from old module'\n\n"
               "def helper():\n    return 42\n")
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "old_module.py").write_text(content)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add old_module")
    (tmp_path / "src" / "old_module.py").unlink()
    (tmp_path / "src" / "new_module.py").write_text(content + _pad("f"))
    f = tmp_path / ".scars" / "0033-moved.deadend.md"
    f.write_text(_reanchor_scar(id=33, path_anchors=["src/old_module.py"]))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "move old_module + add scar")
    monkeypatch.chdir(tmp_path)

    assert main(["reanchor", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["proposals"]) == 1
    p = data["proposals"][0]
    assert p["scar_id"] == 33
    assert p["anchor_kind"] == "path"
    assert p["dead_anchor"] == "src/old_module.py"
    assert p["proposed_anchor"] == "src/new_module.py"
    assert p["confidence"] == "high"


# ---------------------------------------------------------------------------
# scar gc (#115) — acts on machine state (state dir: markers, firing log),
# reports read-only on repo state (.scars/: candidates, fp-log.txt).
# ---------------------------------------------------------------------------

def _touch_marker(state_dir, name, *, age_days):
    import os
    import time

    state_dir.mkdir(parents=True, exist_ok=True)
    p = state_dir / name
    p.write_text("")
    mtime = time.time() - age_days * 86400
    os.utime(p, (mtime, mtime))
    return p


def test_gc_requires_scars_dir(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["gc"]) == 1
    assert "scar init" in capsys.readouterr().out


def test_gc_json_shape_empty_state(repo, capsys, monkeypatch):
    init_scars(repo)
    monkeypatch.setenv("SCAR_STATE_DIR", str(repo / "state"))
    assert main(["gc", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "removed_markers": 0,
        "dropped_firings": 0,
        "dry_run": False,
        "candidates": [],
        "fp_log": {"present": False, "size": 0, "lines": 0},
    }


def test_gc_removes_old_markers_and_reports_count(repo, capsys, monkeypatch):
    init_scars(repo)
    state = repo / "state"
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    old = _touch_marker(state, "drafted-old", age_days=10)
    fresh = _touch_marker(state, "drafted-fresh", age_days=1)

    assert main(["gc", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["removed_markers"] == 1
    assert not old.exists()
    assert fresh.exists()


def test_gc_respects_days_flag(repo, capsys, monkeypatch):
    init_scars(repo)
    state = repo / "state"
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    marker = _touch_marker(state, "drafted-3d", age_days=3)

    assert main(["gc", "--days", "1", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["removed_markers"] == 1
    assert not marker.exists()


def test_gc_dry_run_marks_true_and_removes_nothing(repo, capsys, monkeypatch):
    init_scars(repo)
    state = repo / "state"
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    old = _touch_marker(state, "drafted-old", age_days=10)
    _write_firing_log(state, [{"ts": "t", "repo": "r", "target": "x",
                               "scar_ids": [], "count": 0} for _ in range(20)])

    assert main(["gc", "--max-firings", "5", "--dry-run", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["dry_run"] is True
    assert data["removed_markers"] == 1
    assert data["dropped_firings"] == 15
    assert old.exists()  # dry-run: nothing actually deleted
    lines = (state / "firing-log.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 20  # dry-run: log untouched


def test_gc_truncates_firing_log_respects_max_firings_flag(repo, capsys, monkeypatch):
    init_scars(repo)
    state = repo / "state"
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    _write_firing_log(state, [{"ts": "t", "repo": "r", "target": "x",
                               "scar_ids": [], "count": 0} for _ in range(30)])

    assert main(["gc", "--max-firings", "10", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["dropped_firings"] == 20
    lines = (state / "firing-log.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 10


def test_gc_json_reports_candidate_ages_and_fp_log(repo, capsys, monkeypatch):
    init_scars(repo)
    monkeypatch.setenv("SCAR_STATE_DIR", str(repo / "state"))
    cand = repo / ".scars" / "candidates"
    (cand / "a.md").write_text(CANDIDATE)
    fp_log = cand / "fp-log.txt"
    fp_log.write_text("2026-06-10 false trigger\n")

    assert main(["gc", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["candidates"] == [{"name": "a.md", "age_days": 0.0}]
    assert data["fp_log"] == {"present": True, "size": fp_log.stat().st_size, "lines": 1}


def test_gc_plain_output_reports_removals_nudge_and_fp_log_note(repo, capsys, monkeypatch):
    init_scars(repo)
    state = repo / "state"
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    _touch_marker(state, "drafted-old", age_days=10)
    cand = repo / ".scars" / "candidates"
    (cand / "a.md").write_text(CANDIDATE)
    (cand / "fp-log.txt").write_text("2026-06-10 false trigger\n")

    assert main(["gc"]) == 0
    out = capsys.readouterr().out
    assert "1" in out  # removed marker count
    assert "a.md" in out
    assert "scar promote" in out
    assert "drafter-precision" in out
    assert "not auto-cleaned" in out


def test_gc_dry_run_plain_output_marks_would_not_did(repo, capsys, monkeypatch):
    init_scars(repo)
    state = repo / "state"
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    _touch_marker(state, "drafted-old", age_days=10)

    assert main(["gc", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would" in out.lower()


def test_gc_scars_dir_never_touched(repo, capsys, monkeypatch):
    """Structural guarantee at the CLI boundary, mirroring test_gc.py's
    hash-before/after: running `scar gc` end to end must not change any
    byte in .scars/."""
    import hashlib

    init_scars(repo)
    state = repo / "state"
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    _touch_marker(state, "drafted-old", age_days=10)
    cand = repo / ".scars" / "candidates"
    (cand / "a.md").write_text(CANDIDATE)
    (cand / "fp-log.txt").write_text("2026-06-10 false trigger\n")

    def _hash_dir(path):
        out = {}
        for f in sorted(path.rglob("*")):
            if f.is_file():
                out[str(f.relative_to(path))] = hashlib.sha256(f.read_bytes()).hexdigest()
        return out

    before = _hash_dir(repo / ".scars")
    assert main(["gc"]) == 0
    after = _hash_dir(repo / ".scars")
    assert before == after


# ---------------------------------------------------------------------------
# #154: rich tty branches for promote / harvest / agent doctor / init.
# Plain non-tty byte-identity is locked by the tests above; these assert the
# rich renderer runs and carries rich-only framing (panel titles) + content.
# ---------------------------------------------------------------------------


def test_init_tty_renders_rich_panel(repo, capsys, monkeypatch):
    _force_tty(monkeypatch)
    assert main(["init"]) == 0
    out = capsys.readouterr().out
    assert "scar init" in out          # panel title — absent from plain output
    assert "candidates" in out


def test_promote_tty_renders_rich_result(repo, capsys, monkeypatch):
    init_scars(repo)
    (repo / ".scars" / "candidates" / "x.md").write_text(CANDIDATE)
    _force_tty(monkeypatch)
    assert main(["promote", "x.md"]) == 0
    out = capsys.readouterr().out
    assert "scar promote" in out       # rich-only framing
    assert "promoted" in out


def test_harvest_tty_renders_rich_header(repo, capsys, monkeypatch):
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=T",
                    "commit", "-qm", "init"], cwd=repo, check=True)
    _force_tty(monkeypatch)
    assert main(["harvest"]) == 0
    out = capsys.readouterr().out
    assert "scar harvest" in out       # rich-only framing
    assert "curation required" in out


def test_agent_doctor_tty_renders_rich(repo, capsys, monkeypatch):
    _force_tty(monkeypatch)
    assert main(["agent", "doctor"]) == 0
    out = capsys.readouterr().out
    assert "scar agent doctor" in out  # rich-only framing
    assert "AGENTS.md" in out
