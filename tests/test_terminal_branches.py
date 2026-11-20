"""Every terminal branch drives a real command with hostile values (#414).

The rich (terminal) renderers had no tests, which is how a value like
``app/[id]/page.tsx`` or ``x[/]y`` got eaten as markup without anyone seeing
it. Each test here forces the terminal branch through the ``output.is_tty``
seam, runs the real command, and checks the text a person would read.

The console is wide, so a long value is never split by wrapping, and ANSI is
stripped before asserting.
"""
import io
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from scar import output
from scar.cli import main
from scar.store import init_scars

from test_cli import (_git_repo_with_renamed_anchor, _init_bare_git, _pad, _reanchor_scar,
                      _write_firing_log, symbols_extra)
from test_verify import fake  # noqa: F401  (fixture: a stand-in verifier)
from test_verify import write_scar as write_signed_scar
from test_write_gate_cli import _moved_module_repo, extra_renamed_scar

HOSTILE_TITLE = "x[/]y see [bold] here"
BRACKET_PATH = "app/[id]/page.tsx"
ENDS_IN_BACKSLASH = "ends with a backslash \\"


def _scar(*, id, title="plain title", paths=("src/gone/",), status="active", extra="",
          type="deadend", evidence="abc1234"):
    anchors = "".join(f"  - path: {p}\n" for p in paths)
    return (
        "---\n"
        f"id: {id}\n"
        f"type: {type}\n"
        f"title: {title}\n"
        "severity: medium\n"
        "confidence: 0.8\n"
        "created: 2026-06-10\n"
        'authors: ["claude-code"]\n'
        "anchors:\n"
        f"{anchors}"
        "evidence:\n"
        f"  - commit: {evidence}\n"
        f"{extra}"
        f"status: {status}\n"
        "---\n\n"
        "Body text.\n"
    )


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    monkeypatch.chdir(tmp_path)
    init_scars(tmp_path)
    return tmp_path


@pytest.fixture
def term(monkeypatch):
    """Force the terminal branch on a wide console and return a reader for
    what a person would see, with colour codes stripped."""
    from rich.console import Console
    buf = io.StringIO()
    monkeypatch.setattr(output, "is_tty", lambda: True)
    monkeypatch.setattr(output, "console", Console(
        file=buf, force_terminal=True, color_system="standard", width=400))

    def read():
        return re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())
    return read


def _commit(repo, message="snapshot"):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _state_dir():
    return Path(os.environ["SCAR_STATE_DIR"])


# --- status ----------------------------------------------------------------------


def test_status_terminal_shows_every_section_with_hostile_values(repo, term):
    (repo / "src" / "live").mkdir(parents=True)
    (repo / "src" / "live" / "mod.py").write_text("x = 1\n")
    scars = repo / ".scars"
    # review_after in the past and a firing threshold already crossed
    (scars / "0001-live.deadend.md").write_text(_scar(
        id=1, title=HOSTILE_TITLE, paths=("src/live/",),
        extra="review_after: 2020-01-01\nexpires:\n  review_after_firings: 1\n"))
    (scars / "0002-chal.deadend.md").write_text(_scar(
        id=2, title="challenged [red]one", paths=("src/live/",), status="challenged"))
    (scars / "0003-orph.deadend.md").write_text(_scar(
        id=3, title="orphaned [dim] title", paths=("src/live/",), status="orphaned"))
    (scars / "0004-rot.deadend.md").write_text(_scar(
        id=4, paths=("src/live/", BRACKET_PATH)))
    (scars / "0005-dead.deadend.md").write_text(_scar(id=5, paths=("app/[gone]/x.tsx",)))
    (scars / "0009-[broken].deadend.md").write_text("---\nid: 9\n")
    _commit(repo)
    _write_firing_log(_state_dir(), [
        {"ts": "2099-01-01T00:00:00", "repo": str(repo), "target": "src/live/mod.py",
         "scar_ids": [1], "count": 1}])

    assert main(["status"]) == 0
    out = term()
    assert "  challenged deadend #2 challenged [red]one" in out
    assert re.search(r"REVIEW DUE deadend #1 review_after 2020-01-01", out)
    assert re.search(r"REVIEW DUE deadend #1 fired 1 times since its last revision", out)
    assert "orphaned deadend #3 orphaned [dim] title" in out
    assert "orphan-detected [#5]" in out and "app/[gone]/x.tsx" in out
    assert "partial-rot [#4]" in out and BRACKET_PATH in out
    assert "WARNING: 1 unparseable (can NEVER fire): 0009-[broken].deadend.md" in out


def test_status_and_why_terminal_keep_a_title_that_ends_in_a_backslash(repo, term):
    (repo / "src").mkdir()
    (repo / ".scars" / "0001-a.deadend.md").write_text(
        _scar(id=1, title=ENDS_IN_BACKSLASH, paths=("src/",), status="challenged"))
    assert main(["status"]) == 0
    assert f"challenged deadend #1 {ENDS_IN_BACKSLASH}" in term()
    assert main(["why", "src/a.py"]) == 0
    assert f"#1 \u2014 {ENDS_IN_BACKSLASH}" in term()


# --- lint ------------------------------------------------------------------------


def test_lint_terminal_shows_partial_rot_reverse_hint_and_firing_review(repo, term):
    (repo / "src" / "live").mkdir(parents=True)
    (repo / "src" / "live" / "mod.py").write_text("x = 1\n")
    scars = repo / ".scars"
    (scars / "0001-live.deadend.md").write_text(_scar(
        id=1, title=HOSTILE_TITLE, paths=("src/live/",),
        extra="expires:\n  review_after_firings: 1\n"))
    (scars / "0004-rot.deadend.md").write_text(_scar(
        id=4, paths=("src/live/", BRACKET_PATH)))
    (scars / "0003-orph.deadend.md").write_text(_scar(
        id=3, paths=("src/live/",), status="orphaned"))
    _commit(repo)
    _write_firing_log(_state_dir(), [
        {"ts": "2099-01-01T00:00:00", "repo": str(repo), "target": "src/live/mod.py",
         "scar_ids": [1], "count": 1}])

    assert main(["lint"]) == 0
    out = term()
    assert "HINT partial-rot: scar #4" in out and BRACKET_PATH in out
    assert "HINT: scar #3 marked orphaned but anchors live again" in out
    assert "WARNING firing-review: scar #1" in out
    assert "fired 1 times since its last revision" in out
    assert "lint: " in out and "firing-review" in out


def test_lint_terminal_notes_a_shallow_clone(tmp_path, monkeypatch, term):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q")
    _git(origin, "config", "user.email", "t@t.t")
    _git(origin, "config", "user.name", "t")
    (origin / "a.txt").write_text("a\n")
    _commit(origin, "one")
    (origin / "b.txt").write_text("b\n")
    _commit(origin, "two")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--depth", "1", "-q", origin.as_uri(), str(clone)],
                   check=True, capture_output=True)
    monkeypatch.chdir(clone)
    init_scars(clone)
    assert main(["lint"]) == 0
    assert "note: shallow clone \u2014 evidence-reachability check skipped" in term()


def test_lint_terminal_reports_each_promotion_state_literally(repo, term, fake):  # noqa: F811
    fake('{"class":"c","not_proven":["expiry","freshness","order_of_arrival","replay",'
         '"revocation_after_head"],"reason":"odd[/]code [bold] x","receipt_id":"",'
         '"rechecked":false}')
    (repo / ".scars" / "config.json").write_text(
        json.dumps({"org": "org-1", "signed_from": 1}))
    _git(repo, "commit", "-q", "--allow-empty", "-m", "root")
    write_signed_scar(repo)
    assert main(["lint", "--verify-promotion"]) == 0
    out = term()
    assert "promotion: scar #1: not proven: odd[/]code [bold] x" in out


def test_lint_terminal_reports_a_verified_promotion(repo, term, fake):  # noqa: F811
    fake()
    (repo / ".scars" / "config.json").write_text(
        json.dumps({"org": "org-1", "signed_from": 1}))
    _git(repo, "commit", "-q", "--allow-empty", "-m", "root")
    write_signed_scar(repo)
    assert main(["lint", "--verify-promotion"]) == 0
    assert "promotion: scar #1: verified" in term()


@symbols_extra
def test_lint_terminal_shows_symbol_drift(repo, term):
    src = repo / "store.py"
    src.write_text("class SessionStore:\n    def save(self):\n        x = 1\n        return x\n")
    _commit(repo, "base")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                         text=True, check=True).stdout.strip()
    (repo / ".scars" / "s.md").write_text(
        "---\ntype: deadend\ntitle: t\nseverity: medium\nconfidence: 1.0\n"
        "anchors:\n  - path: store.py\n  - symbol: SessionStore.save\n"
        f"evidence:\n  - commit: {sha}\nstatus: active\n---\nbody\n")
    _commit(repo, "add scar")
    src.write_text("class SessionStore:\n    def save(self):\n        return compute(other())\n")
    _commit(repo, "rewrite save")
    assert main(["lint"]) == 0
    out = term()
    assert "HINT symbol-drift: scar #" in out
    assert "SessionStore.save" in out and f"similar since {sha[:7]}" in out


# --- check -----------------------------------------------------------------------


def test_check_terminal_with_no_scar_names_the_path_literally(repo, term):
    assert main(["check", "app/[id]/none.tsx"]) == 0
    assert "no scars anchored to app/[id]/none.tsx" in term()


def test_check_terminal_prints_a_violation_line_for_a_diff(repo, term):
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n")
    (repo / ".scars" / "0001-v.deadend.md").write_text(_scar(
        id=1, title=HOSTILE_TITLE, paths=("src/",),
        extra="violation: 'forbidden_call\\('\n"))
    diff = ("diff --git a/src/[a].py b/src/[a].py\n--- a/src/[a].py\n+++ b/src/[a].py\n"
            "@@ -1 +1,2 @@\n x = 1\n+forbidden_call(1)\n")
    assert main(["check", "--diff", diff]) == 0
    out = term()
    assert f"VIOLATION scar #1 on src/[a].py: {HOSTILE_TITLE}" in out


# --- why -------------------------------------------------------------------------


def test_why_terminal_with_no_record_names_the_path_literally(repo, term):
    assert main(["why", "app/[id]/none.tsx"]) == 0
    assert "no recorded pain for app/[id]/none.tsx" in term()


# --- stats -----------------------------------------------------------------------


def _fires(repo_key, n, scar_id=1):
    return [{"ts": f"2026-06-10T10:00:{i:02d}", "repo": repo_key, "target": "src/a.py",
             "scar_ids": [scar_id], "count": 1} for i in range(n)]


def test_stats_terminal_shows_an_over_broad_advisory(repo, term):
    (repo / ".scars" / "0001-a.deadend.md").write_text(_scar(id=1, title=HOSTILE_TITLE))
    _write_firing_log(_state_dir(), _fires(str(repo), 25))
    assert main(["stats"]) == 0
    out = term()
    assert "advisory: scar #1 accounts for 100% of firings" in out
    assert "likely over-broad" in out


def test_stats_all_repos_terminal_groups_repos_with_bracketed_names(repo, term, monkeypatch):
    state = repo / "state[0]"
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    rows = _fires("/work/app[1]", 3) + [
        {"ts": "2026-06-13T10:00:00", "repo": "/work/app[1]", "target": "x",
         "violation_ids": [1], "count": 1}] + _fires("/work/[other]", 2, scar_id=9)
    _write_firing_log(state, rows)
    assert main(["stats", "--all-repos"]) == 0
    out = term()
    assert "2 repo(s) recorded in" in out and "state[0]" in out
    # the table title wraps inside a narrow table, so check the names alone
    assert "/work/app[1]" in out and "3 firing(s)" in out
    assert "/work/[other]" in out and "2 firing(s)" in out
    assert "3 (violations: x1)" in out
    assert "note: ids are per-repo" in out


def test_stats_all_repos_terminal_flags_a_disconnected_instrument(repo, term, monkeypatch):
    from scar.cli import INSTRUMENT_WARNING
    state = repo / "state"
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    # a violation with no precheck row at all: the injecting half is dead
    _write_firing_log(state, [
        {"ts": "2026-06-13T10:00:00", "repo": "/work/app[1]", "target": "x",
         "violation_ids": [1], "count": 1}])
    assert main(["stats", "--all-repos"]) == 0
    out = term()
    assert "/work/app[1]" in out
    assert f"  {INSTRUMENT_WARNING}" in out


# --- gc --------------------------------------------------------------------------


def test_gc_terminal_names_candidates_and_the_state_dir_literally(repo, term, monkeypatch):
    state = repo / "state[x]"
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    (repo / ".scars" / "candidates" / "[draft]-one.md").write_text("---\n---\n")
    assert main(["gc", "--dry-run"]) == 0
    out = term()
    assert "[draft]-one.md" in out
    assert "review with `scar promote <path>` or delete rejected files" in out
    # once in the summary panel, once in the fp-log line below it
    assert out.count("state[x]") >= 2
    assert "scar gc (dry-run)" in out


# --- orphan ----------------------------------------------------------------------


def test_orphan_terminal_says_so_when_nothing_is_orphaned(repo, term):
    assert main(["orphan"]) == 0
    assert "no orphan-detected scars" in term()


def test_orphan_terminal_lists_partial_rot_with_a_bracketed_anchor(repo, term):
    (repo / "src" / "live").mkdir(parents=True)
    (repo / "src" / "live" / "mod.py").write_text("x = 1\n")
    (repo / ".scars" / "0004-rot.deadend.md").write_text(_scar(
        id=4, paths=("src/live/", BRACKET_PATH)))
    _commit(repo)
    assert main(["orphan"]) == 0
    out = term()
    assert "partial-rot [#4]" in out and BRACKET_PATH in out
    assert "1 partial-rot \u2014 advisory; re-anchor the dead anchor(s)." in out


def test_orphan_fix_renames_terminal_reports_fixed_and_refused_literally(
        tmp_path, monkeypatch, term):
    _git_repo_with_renamed_anchor(tmp_path)
    s10 = extra_renamed_scar(tmp_path, 10, signed=True)
    before = s10.read_bytes()
    monkeypatch.chdir(tmp_path)
    assert main(["orphan", "--fix-renames"]) == 1
    out = term()
    assert re.search(r"fixed \[#9\] renamed anchor \S+ -> src/new_home.py", out)
    refused = re.search(r"refused \[#10\] (.+)", out)
    assert refused and "signed" in refused.group(1)
    assert "1 scar(s) fixed, 0 left unfixed, 1 refused" in out
    assert s10.read_bytes() == before


def test_orphan_fix_renames_terminal_when_there_is_nothing_to_fix(repo, term):
    assert main(["orphan", "--fix-renames"]) == 0
    out = term()
    assert "no orphan-detected scars with a dead path anchor to fix" in out
    assert "0 scar(s) fixed, 0 left unfixed" in out


def test_orphan_fix_renames_terminal_lists_an_unfixable_scar(repo, term):
    (repo / ".scars" / "0005-dead.deadend.md").write_text(_scar(id=5, paths=("app/[gone]/x.tsx",)))
    assert main(["orphan", "--fix-renames"]) == 0
    out = term()
    assert "not fixed [#5] no unambiguous, currently-tracked rename target" in out
    assert "0 scar(s) fixed, 1 left unfixed" in out


# --- reanchor --------------------------------------------------------------------


def test_reanchor_terminal_says_so_when_there_is_nothing_to_propose(repo, term):
    assert main(["reanchor"]) == 0
    assert "no reanchor proposals" in term()


def test_reanchor_terminal_lists_a_proposal(tmp_path, monkeypatch, term):
    f = _moved_module_repo(tmp_path, 24, signed=False)
    monkeypatch.chdir(tmp_path)
    assert main(["reanchor"]) == 0
    out = term()
    assert re.search(r"reanchor \[#24\] path src/old_module.py -> src/new_module.py "
                     r"\(\w+\)", out)
    assert re.search(r"\d+ proposal\(s\), \d+ pattern diagnostic\(s\)", out)
    assert f.exists()


def test_reanchor_apply_terminal_reports_fixed(tmp_path, monkeypatch, term):
    _moved_module_repo(tmp_path, 24, signed=False)
    monkeypatch.chdir(tmp_path)
    assert main(["reanchor", "--apply", "--id", "24"]) == 0
    out = term()
    assert "fixed [#24] path src/old_module.py -> src/new_module.py" in out
    assert "1 anchor(s) fixed, 0 left unfixed" in out


def test_reanchor_apply_terminal_refuses_a_signed_scar_and_names_the_reason(
        tmp_path, monkeypatch, term):
    f = _moved_module_repo(tmp_path, 24, signed=True)
    monkeypatch.chdir(tmp_path)
    before = f.read_bytes()
    assert main(["reanchor", "--apply", "--id", "24"]) == 1
    out = term()
    refused = re.search(r"refused \[#24\] (.+)", out)
    assert refused and "signed" in refused.group(1)
    assert "0 anchor(s) fixed" in out
    assert f.read_bytes() == before


def test_reanchor_terminal_lists_a_pattern_diagnostic_with_a_bracketed_regex(
        tmp_path, monkeypatch, term):
    _init_bare_git(tmp_path)
    init_scars(tmp_path)
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "mod.py").write_text("class OldClassName:\n    pass\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add OldClassName")
    (tmp_path / "src" / "mod.py").write_text("class Renamed:\n    pass\n")
    (tmp_path / ".scars" / "0029-pattern.deadend.md").write_text(
        _reanchor_scar(id=29, pattern_anchors=["Old[C]lassName"]))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "rename class + add scar")
    monkeypatch.chdir(tmp_path)
    assert main(["reanchor"]) == 0
    out = term()
    assert re.search(r"pattern \[#29\] Old\[C\]lassName: last matched at", out)
    assert "0 proposal(s), 1 pattern diagnostic(s)" in out


def test_reanchor_apply_terminal_lists_an_ambiguous_anchor_as_not_fixed(
        tmp_path, monkeypatch, term):
    _init_bare_git(tmp_path)
    init_scars(tmp_path)
    content = ("def shared():\n    return 'duplicated content across two files'\n\n"
               "def more():\n    return 7\n")
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "shared.py").write_text(content)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add shared")
    (tmp_path / "src" / "shared.py").unlink()
    (tmp_path / "src" / "copy_one.py").write_text(content + _pad("one"))
    (tmp_path / "src" / "copy_two.py").write_text(content + _pad("two"))
    f = tmp_path / ".scars" / "0023-ambiguous.deadend.md"
    f.write_text(_reanchor_scar(id=23, path_anchors=["src/shared.py"]))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "delete shared, duplicate + add scar")
    monkeypatch.chdir(tmp_path)
    before = f.read_text()
    assert main(["reanchor", "--apply", "--id", "23"]) == 0
    out = term()
    assert "not fixed [#23] path src/shared.py: ambiguous" in out
    assert "0 anchor(s) fixed, 1 left unfixed" in out
    assert f.read_text() == before


# --- sweep -----------------------------------------------------------------------


def test_sweep_terminal_prints_a_hit_with_a_bracketed_path_and_excerpt(repo, term):
    (repo / "app" / "[id]").mkdir(parents=True)
    (repo / "app" / "[id]" / "page.tsx").write_text("const a = forbidden_call(arr[0]) // [bold]\n")
    (repo / ".scars" / "0001-v.deadend.md").write_text(_scar(
        id=1, title=HOSTILE_TITLE, paths=("app/",),
        extra="violation: 'forbidden_call\\('\n"))
    _commit(repo)
    assert main(["sweep"]) == 0
    out = term()
    assert f"app/[id]/page.tsx:1: scar #1 (deadend): {HOSTILE_TITLE}" in out
    assert "const a = forbidden_call(arr[0]) // [bold]" in out
    assert "1 hits" in out


# --- draft-check -----------------------------------------------------------------


def test_draft_check_terminal_prints_the_contract_panel(repo, term, monkeypatch):
    from scar import draftcheck

    class _Result:
        triggered = True

        def signal_counts(self):
            return {}

    monkeypatch.setattr(draftcheck, "analyze", lambda state_dir, r: _Result())
    monkeypatch.setattr(draftcheck, "contract_text",
                        lambda store, result: "Record [bold]this[/] abandoned try at app/[id]/page.tsx")
    assert main(["draft-check"]) == 0
    out = term()
    assert "scar draft-check" in out
    assert "Record [bold]this[/] abandoned try at app/[id]/page.tsx" in out


# --- init / promote / harvest ----------------------------------------------------


def test_init_terminal_prints_the_next_steps_when_history_exists(tmp_path, monkeypatch, term):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("a\n")
    _commit(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    out = term()
    assert "next steps (this repo has minable history):" in out
    assert "scar harvest --write 5" in out and "scar hook install --git" in out


def test_promote_terminal_warns_when_the_reviewer_differs_only_by_case(repo, term):
    _git(repo, "config", "user.name", "kibukx")
    (repo / ".scars" / "candidates" / "tried-x.md").write_text(
        _scar(id=1, title=HOSTILE_TITLE, paths=("src/",)).replace("id: 1\n", "")
        .replace("status: active\n", "status: candidate\n"))
    assert main(["promote", "tried-x", "--reviewer", "Kibukx"]) == 0
    out = term()
    assert "warning: --reviewer differs from git user.name only by case" in out


def _harvest_history(repo):
    comp = repo / "apps" / "[slug]"
    comp.mkdir(parents=True)
    (comp / "deploy.yaml").write_text("replicas: 1\n")
    _commit(repo, "feat: add slug app")
    (comp / "deploy.yaml").write_text("replicas: 3\n")
    _git(repo, "commit", "-qam", "feat: scale up")
    (comp / "deploy.yaml").write_text("replicas: 1\n")
    _git(repo, "commit", "-qam", "fix: revert replicas [#9], instance broke")
    (comp / "deploy.yaml").unlink()
    comp.rmdir()
    _commit(repo, "chore: remove slug app")


def test_harvest_terminal_keeps_the_literal_brackets_of_every_candidate(repo, term):
    _harvest_history(repo)
    assert main(["harvest", str(repo)]) == 0
    out = term()
    assert "raw candidate(s)" in out and "curation required" in out
    assert "apps/[slug]" in out
    assert re.search(r"- \[\w+ score \d+\.\d\]", out)
    assert "revert replicas [#9]" in out


def test_harvest_top_terminal_keeps_the_literal_brackets_of_every_candidate(repo, term):
    _harvest_history(repo)
    assert main(["harvest", str(repo), "--top-k", "5"]) == 0
    out = term()
    assert re.search(r"top \d+ of \d+ raw, cross-section by raw score", out)
    assert re.search(r"- \[\w+ score \d+\.\d\]", out)
    assert "apps/[slug]" in out or "revert replicas [#9]" in out
