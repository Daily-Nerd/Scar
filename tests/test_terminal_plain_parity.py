"""A terminal shows every line the plain output shows (#429).

`output.render(data=, tty=, plain=)` only uses `data` for --json, so a terminal
renderer and a plain renderer written apart can drift, and the person at the
terminal is the one who loses lines. For reanchor, lint, promote and status
both renderers now consume ONE line builder. These tests render the same data
both ways and require every plain line to be present in the terminal output
once styling is gone. Plain text itself is pinned line for line, so the
refactor cannot move a byte of what a pipe receives.
"""
import io
import json
import os
import re
import subprocess

from pathlib import Path

import pytest
from rich.console import Console

from scar import cli, output
from scar.cli import main
from scar.store import init_scars

EM = "\u2014"


@pytest.fixture(params=[80, 120, 1000])
def term(monkeypatch, request):
    """Force the terminal branch. The widths are a real terminal's (80, 120)
    and one wide enough to never wrap: a value cut or lost at 80 columns is
    the same bug as a line the terminal never printed."""
    buf = io.StringIO()
    monkeypatch.setattr(output, "is_tty", lambda: True)
    monkeypatch.setattr(output, "console", Console(
        file=buf, force_terminal=True, color_system="standard", width=request.param))
    return lambda: re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())


def _plain_lines(capsys, fn, *args):
    capsys.readouterr()
    fn(*args)
    return capsys.readouterr().out.splitlines()


def _squash(text):
    """Text with whitespace and box-drawing characters removed: what is left
    is the words, however the terminal wrapped them or boxed them in."""
    return re.sub(r"[\s\u2500-\u257f]+", "", text)


def _column_streams(text):
    """The text of each table column, top to bottom, wrapped cells joined.
    Table rows are the lines that carry a column separator but do not start a
    panel border."""
    streams = []
    for line in text.splitlines():
        if "\u2502" not in line or line.startswith(("\u2502", "\u256d", "\u2570")):
            continue
        for i, part in enumerate(line.split("\u2502")):
            if i == len(streams):
                streams.append("")
            streams[i] += _squash(part)
    return streams


def _assert_parity(report, terminal_text):
    """Every datum the plain output shows, the terminal shows. Plain lines
    must be in the terminal output; every field of every table row must be in
    its column. Expected values come from the builder's own output."""
    assert report.before or report.rows or report.after, "the data produced nothing"
    flat = _squash(terminal_text)
    missing = [ln for ln in (*report.before, *report.after) if _squash(ln) not in flat]
    assert not missing, f"the terminal dropped lines: {missing}"
    streams = _column_streams(terminal_text)
    for row in report.rows:
        cells = row.cells()
        assert len(streams) >= len(cells), "the terminal drew no table for these rows"
        for i, cell in enumerate(cells):
            assert _squash(cell) in streams[i], f"column {i} lost {cell!r}"


def _plain_of(report):
    return [*report.before, *(r.line() for r in report.rows), *report.after]


# --- reanchor, list mode ----------------------------------------------------


REANCHOR_DATA = {
    "proposals": [
        {"scar_id": 4, "anchor_kind": "path", "dead_anchor": "src/old.py",
         "proposed_anchor": "src/new.py", "confidence": "high", "signal": 0.91,
         "evidence": "renamed in 3 commits"},
        {"scar_id": 5, "anchor_kind": "symbol", "dead_anchor": "a.py::f",
         "proposed_anchor": "b.py::f", "confidence": "low", "signal": 0.4,
         "evidence": "same name elsewhere"},
    ],
    "pattern_diagnostics": [
        {"scar_id": 6, "dead_anchor": "foo\\(", "diagnostic": "last matched at abc1234"}],
}


def test_reanchor_list_plain_is_unchanged(capsys):
    assert _plain_lines(capsys, cli._reanchor_plain, REANCHOR_DATA) == [
        f"reanchor [#4] path src/old.py -> src/new.py (high, signal 0.91) {EM} renamed in 3 commits",
        f"reanchor [#5] symbol a.py::f -> b.py::f (low, signal 0.4) {EM} same name elsewhere",
        f"pattern [#6] foo\\(: last matched at abc1234 (diagnostic only {EM} never auto-applied)",
        "2 proposal(s), 1 pattern diagnostic(s) "
        f"{EM} review, then `scar reanchor --apply --id N` to persist",
    ]


def test_reanchor_list_terminal_shows_every_plain_line(term):
    cli._reanchor_rich(REANCHOR_DATA)
    _assert_parity(cli._reanchor_report(REANCHOR_DATA), term())


def test_reanchor_list_empty_parity(capsys, term):
    empty = {"proposals": [], "pattern_diagnostics": []}
    assert _plain_lines(capsys, cli._reanchor_plain, empty) == ["no reanchor proposals"]
    cli._reanchor_rich(empty)
    _assert_parity(cli._reanchor_report(empty), term())


# --- reanchor --apply -------------------------------------------------------


APPLY_DATA = {
    "fixed": [{"anchor_kind": "path", "dead_anchor": "a/", "proposed_anchor": "b/",
               "signed": False}],
    "skipped": [{"anchor_kind": "symbol", "dead_anchor": "x.py::g",
                 "reason": "no candidate found"}],
    "refused": [{"scar_id": 7, "reason": "would break the signed record",
                 "touch_spent": False}],
}


def test_reanchor_apply_plain_is_unchanged(capsys):
    assert _plain_lines(capsys, cli._reanchor_apply_plain, APPLY_DATA, 7) == [
        "fixed [#7] path a/ -> b/",
        "not fixed [#7] symbol x.py::g: no candidate found",
        "refused [#7] would break the signed record",
        f"1 anchor(s) fixed, 1 left unfixed {EM} status unchanged "
        "(anchors-live-again hint will surface on next `scar lint`)",
    ]


def test_reanchor_apply_terminal_shows_every_plain_line(term):
    cli._reanchor_apply_rich(APPLY_DATA, 7)
    _assert_parity(cli._reanchor_apply_report(APPLY_DATA, 7), term())


SIGNED_APPLY_DATA = {
    "fixed": [{"anchor_kind": "path", "dead_anchor": "a/", "proposed_anchor": "b/",
               "signed": True}],
    "skipped": [],
    "refused": [],
}


def test_reanchor_apply_signed_line_in_plain_and_terminal(capsys, term):
    assert _plain_lines(capsys, cli._reanchor_apply_plain, SIGNED_APPLY_DATA, 7)[0] == (
        "fixed (signed) [#7] path a/ -> b/")
    cli._reanchor_apply_rich(SIGNED_APPLY_DATA, 7)
    _assert_parity(cli._reanchor_apply_report(SIGNED_APPLY_DATA, 7), term())


# --- orphan --fix-renames ---------------------------------------------------


FIX_DATA = {
    "fixed": [{"scar_id": 9, "renamed": {"src/a.py": "src/b.py"}, "signed": False},
              {"scar_id": 10, "renamed": {"src/c.py": "src/d.py"}, "signed": True}],
    "skipped": [{"scar_id": 5}],
    "refused": [{"scar_id": 11, "reason": "cancelled at x.md: nothing was written",
                 "touch_spent": False},
                {"scar_id": 12, "reason": "the touch was spent, but the checker said no",
                 "touch_spent": True}],
}


def test_orphan_fix_plain_is_pinned(capsys):
    assert _plain_lines(capsys, cli._orphan_fix_plain, FIX_DATA) == [
        "fixed [#9] renamed anchor src/a.py -> src/b.py",
        "fixed (signed) [#10] renamed anchor src/c.py -> src/d.py",
        f"not fixed [#5] no unambiguous, currently-tracked rename target {EM} anchor "
        "left as-is",
        "refused [#11] cancelled at x.md: nothing was written",
        "refused [#12] the touch was spent, but the checker said no",
        "2 scar(s) fixed, 1 left unfixed, 2 refused",
    ]


def test_orphan_fix_terminal_shows_every_plain_line(term):
    cli._orphan_fix_rich(FIX_DATA)
    _assert_parity(cli._orphan_fix_report(FIX_DATA), term())


def test_orphan_fix_nothing_to_fix_parity(capsys, term):
    empty = {"fixed": [], "skipped": [], "refused": []}
    assert _plain_lines(capsys, cli._orphan_fix_plain, empty) == [
        "no orphan-detected scars with a dead path anchor to fix",
        "0 scar(s) fixed, 0 left unfixed"]
    cli._orphan_fix_rich(empty)
    _assert_parity(cli._orphan_fix_report(empty), term())


# --- lint -------------------------------------------------------------------


LINT_DATA = {
    "files": 4,
    "findings": [
        {"file": ".scars/0001-a.deadend.md", "level": "error", "message": "bad [thing]"},
        {"file": ".scars/candidates/c.md", "level": "warning", "message": "meh"}],
    "orphans": [{"scar_id": 2, "reason": "all anchors dead: src/gone/"}],
    "partial_rot": [{"scar_id": 3, "reason": "dead anchor src/x/ among live ones",
                     "dead_symbol_anchors": []}],
    "symbol_drift": [{"scar_id": 4, "symbol": "f", "sha": "abcdef123456",
                      "similarity": 0.52}],
    "revivals": [{"scar_id": 5, "predicate": "old_name\\("}],
    "reverse_hints": [{"id": 6}],
    "symbols_unchecked": [7, 8],
    "firing_review": [{"scar_id": 9, "count": 5, "threshold": 5, "since": None,
                       "undated": True, "reason": "fired 5 times since its last revision"}],
    "shallow_clone": True,
    "unreachable_evidence": [{"scar_id": 10, "sha": "deadbee",
                              "reason": "unknown commit"}],
    "failed": 1,
    "failed_files": [".scars/0001-a.deadend.md"],
    "review_due": [],
    "promotion": [{"scar_id": 1, "file": "x", "state": "verified"},
                  {"scar_id": 2, "file": "y", "state": "unverified"}],
}


def test_lint_plain_is_unchanged(capsys):
    assert _plain_lines(capsys, cli._lint_plain, LINT_DATA) == [
        ".scars/0001-a.deadend.md: error: bad [thing]",
        ".scars/candidates/c.md: warning: meh",
        f"WARNING orphan-detected: scar #2 {EM} all anchors dead: src/gone/",
        f"HINT partial-rot: scar #3 {EM} dead anchor src/x/ among live ones "
        f"{EM} re-anchor to restore full coverage",
        "HINT revives-if: scar #5 is archived but its revives_if predicate "
        f"/old_name\\(/ matches the tree again {EM} review for re-promotion",
        f"HINT symbol-drift: scar #4 {EM} f ~52% similar since abcdef1 "
        f"{EM} re-verify the scar still describes this symbol",
        "HINT: scar #6 is marked orphaned but its anchors live again "
        f"{EM} consider re-activating (scar challenge/archive note)",
        "HINT symbols-unchecked: orphan detection could not check the symbol "
        "anchors of scar(s) #7, #8 without the [symbols] extra, so it counted "
        "them as live",
        f"WARNING firing-review: scar #9 {EM} fired 5 times since its last revision",
        f"note: shallow clone {EM} evidence-reachability check skipped "
        "(actions/checkout defaults to depth 1; use fetch-depth: 0)",
        f"WARNING evidence-unreachable: scar #10 {EM} commit deadbee "
        "unknown commit, not reachable from HEAD",
        "promotion: scar #1: verified",
        "promotion: scar #2: unverified",
        "lint: 4 file(s), 1 with errors, 1 orphan(s), 1 partial-rot, "
        "1 unreachable-evidence, 1 firing-review",
    ]


def test_lint_terminal_shows_every_plain_line(term):
    cli._lint_rich(LINT_DATA)
    report = cli._lint_report(LINT_DATA)
    assert len(report.rows) == len(LINT_DATA["findings"])
    _assert_parity(report, term())


def test_lint_terminal_parity_on_a_clean_run(capsys, term):
    clean = {**LINT_DATA, "findings": [], "orphans": [], "partial_rot": [],
             "symbol_drift": [], "revivals": [], "reverse_hints": [],
             "symbols_unchecked": [], "firing_review": [], "shallow_clone": False,
             "unreachable_evidence": [], "failed": 0, "promotion": []}
    plain = _plain_lines(capsys, cli._lint_plain, clean)
    assert plain == ["lint: 4 file(s), 0 with errors, 0 orphan(s), 0 partial-rot, "
                     "0 unreachable-evidence, 0 firing-review"]
    cli._lint_rich(clean)
    _assert_parity(cli._lint_report(clean), term())


# --- promote ----------------------------------------------------------------


PROMOTE_DATA = {
    "promoted": ".scars/0003-x.deadend.md", "reviewer": "Ada", "reviewer_from_git": True,
    "born_orphan": True, "reviewer_case_warning": True, "signed": True,
    "reviewer_given": "ADA", "git_user_name": "Ada",
}


def test_promote_plain_is_unchanged(capsys):
    assert _plain_lines(capsys, cli._promote_plain, PROMOTE_DATA) == [
        "promoted -> .scars/0003-x.deadend.md",
        "  signed: a person's presence was proven",
        "  reviewer: Ada (from git config)",
        "  warning: --reviewer 'ADA' differs from git user.name 'Ada' only by case "
        f"{EM} pick one spelling or author identity drifts (#182)",
        "  advisory: this scar's anchors resolve to nothing in the current tree "
        f"(born orphan-detected) {EM} confirm the anchors are right",
    ]


def test_promote_terminal_shows_every_plain_line(term):
    cli._promote_rich(PROMOTE_DATA)
    _assert_parity(cli._promote_report(PROMOTE_DATA), term())


def test_promote_unsigned_parity(capsys, term):
    data = {**PROMOTE_DATA, "signed": False, "reviewer_from_git": False,
            "born_orphan": False, "reviewer_case_warning": False}
    plain = _plain_lines(capsys, cli._promote_plain, data)
    assert plain == ["promoted -> .scars/0003-x.deadend.md",
                     "  not signed: no org in .scars/config.json"]
    cli._promote_rich(data)
    _assert_parity(cli._promote_report(data), term())


# --- status -----------------------------------------------------------------


STATUS_DATA = {
    "scars_dir": "/r/.scars",
    "active": [{"type": "deadend", "id": 1, "severity": "high", "title": "One [x]"}],
    "challenged": [{"type": "fence", "id": 2, "title": "Two"}],
    "candidates": ["c1.md"],
    "review_due": [{"type": "deadend", "id": 1, "review_after": "2020-01-01"}],
    "firing_review": [{"type": "deadend", "id": 1, "count": 5, "threshold": 5,
                       "since": None, "undated": True,
                       "reason": "fired 5 times since its last revision"}],
    "orphan_detected": [{"scar_id": 5, "reason": "all anchors dead"}],
    "orphaned": [{"type": "landmine", "id": 3, "title": "Three"}],
    "partial_rot": [{"scar_id": 4, "reason": "one anchor dead"}],
    "broken": ["bad.md"],
    "counts": {"active": 1, "candidates": 1, "orphan_detected": 1, "orphaned": 1,
               "partial_rot": 1, "broken": 1, "firing_review": 1},
}


def test_status_plain_is_unchanged(capsys):
    assert _plain_lines(capsys, cli._status_plain, STATUS_DATA) == [
        "/r/.scars: 1 active, 1 candidate(s) pending review",
        "  [deadend #1 | high] One [x]",
        "  [challenged fence #2] Two",
        "  candidate: c1.md",
        f"  REVIEW DUE [deadend #1] review_after 2020-01-01 {EM} "
        "re-verify, then update the date or archive",
        "  REVIEW DUE [deadend #1] fired 5 times since its last revision",
        "  1 orphan-detected (firing, anchors gone), 1 orphaned (persisted), "
        "1 partial-rot (firing, ≥1 anchor dead)",
        "    orphan-detected [#5] all anchors dead",
        "    orphaned [landmine #3] Three",
        "    partial-rot [#4] one anchor dead",
        "  WARNING: 1 unparseable (can NEVER fire): bad.md",
    ]


def test_status_terminal_shows_every_plain_line(term):
    cli._status_rich(STATUS_DATA)
    report = cli._status_report(STATUS_DATA)
    assert len(report.rows) == len(STATUS_DATA["active"])
    _assert_parity(report, term())


def test_a_plain_line_is_its_rows_cells_and_lines_in_order(capsys):
    """The plain renderer prints nothing the report did not return."""
    for report_fn, plain_fn, args in (
            (cli._lint_report, cli._lint_plain, (LINT_DATA,)),
            (cli._status_report, cli._status_plain, (STATUS_DATA,)),
            (cli._promote_report, cli._promote_plain, (PROMOTE_DATA,)),
            (cli._reanchor_report, cli._reanchor_plain, (REANCHOR_DATA,)),
            (cli._reanchor_apply_report, cli._reanchor_apply_plain, (APPLY_DATA, 7))):
        assert _plain_lines(capsys, plain_fn, *args) == _plain_of(report_fn(*args))


HOSTILE = [("x[/]y see [bold] here", "a trailing backslash \\"),
           ("[red]hot[/red] [#5]", "app/[id]/page.tsx")]


def test_table_cells_are_data_never_markup(term):
    findings = [{"file": f"[{i}].md", "level": "warning", "message": m}
                for i, (m, _) in enumerate(HOSTILE)]
    active = [{"type": "deadend", "id": i, "severity": "low", "title": t}
              for i, (_, t) in enumerate(HOSTILE)]
    cli._lint_rich({**LINT_DATA, "findings": findings})
    cli._status_rich({**STATUS_DATA, "active": active})
    out = term()
    streams = _column_streams(out)
    for text in ("x[/]y see [bold] here", "[red]hot[/red] [#5]", "[0].md", "[1].md",
                 "a trailing backslash \\", "app/[id]/page.tsx"):
        assert any(_squash(text) in st for st in streams), text


def test_a_long_cell_is_wrapped_never_cut(term):
    long_title = "w" * 150 + " " + "tail-marker-" + "z" * 60
    cli._status_rich({**STATUS_DATA, "active": [
        {"type": "deadend", "id": 1, "severity": "high", "title": long_title}]})
    assert _squash(long_title) in "".join(_column_streams(term()))
    assert "\u2026" not in term()


# --- --json holds on every exit path of reanchor and orphan -----------------


@pytest.fixture
def bare(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    init_scars(tmp_path)
    return tmp_path


def _orphan_detected_scar(repo):
    """Scar 1 fires and has every anchor dead, so orphan detection reports it."""
    (repo / ".scars" / "0001-a.deadend.md").write_text(
        "---\nid: 1\ntype: deadend\ntitle: gone\nseverity: medium\nconfidence: 0.8\n"
        "created: 2026-06-10\nauthors: [k]\nanchors:\n  - path: src/gone/\n"
        "evidence:\n  - commit: abc1234\nstatus: active\n---\n\nBody.\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t.t",
                    "commit", "-q", "-m", "x"], check=True)
    log = Path(os.environ["SCAR_STATE_DIR"]) / "firing-log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"ts": "2099-01-01T00:00:00", "repo": str(repo),
                               "target": "src/x.py", "scar_ids": [1], "count": 1}) + "\n")


def _json_error(capsys, argv):
    capsys.readouterr()
    rc = main(argv)
    out = capsys.readouterr().out
    payload = json.loads(out)
    return rc, payload


@pytest.mark.parametrize("argv", [
    ["reanchor", "--json"], ["reanchor", "--apply", "--id", "1", "--json"],
    ["orphan", "--json"], ["orphan", "--apply", "--id", "1", "--json"],
    ["orphan", "--fix-renames", "--json"]])
def test_json_when_there_is_no_store(bare, capsys, argv):
    rc, payload = _json_error(capsys, argv)
    assert rc == 1 and "no .scars/" in payload["error"]


@pytest.mark.parametrize("cmd", ["reanchor", "orphan"])
def test_json_when_git_is_unavailable(repo, capsys, monkeypatch, cmd):
    monkeypatch.setattr(cli, "_repo_context", lambda store: None)
    rc, payload = _json_error(capsys, [cmd, "--json"])
    assert rc == 1 and "git unavailable" in payload["error"]


@pytest.mark.parametrize("cmd", ["reanchor", "orphan"])
def test_json_apply_without_an_id(repo, capsys, cmd):
    rc, payload = _json_error(capsys, [cmd, "--apply", "--json"])
    assert rc == 1 and "--apply requires --id" in payload["error"]


def test_json_reanchor_apply_with_nothing_to_do(repo, capsys):
    rc, payload = _json_error(capsys, ["reanchor", "--apply", "--id", "99", "--json"])
    assert rc == 1 and "has no reanchor proposals" in payload["error"]


def test_json_orphan_apply_for_a_scar_that_is_not_orphan_detected(repo, capsys):
    rc, payload = _json_error(capsys, ["orphan", "--apply", "--id", "99", "--json"])
    assert rc == 1 and "not orphan-detected" in payload["error"]


def test_json_orphan_apply_when_the_transition_is_refused(repo, capsys, monkeypatch):
    from scar.store import ScarStore
    _orphan_detected_scar(repo)

    def refuse(self, *a, **k):
        raise ValueError("refused for the test")
    monkeypatch.setattr(ScarStore, "transition", refuse)
    rc, payload = _json_error(capsys, ["orphan", "--apply", "--id", "1", "--json"])
    assert rc == 1 and payload["error"] == "refused for the test"


def test_orphan_apply_json_names_the_scar(repo, capsys):
    _orphan_detected_scar(repo)
    rc, payload = _json_error(capsys, ["orphan", "--apply", "--id", "1", "--reason", "gone", "--json"])
    assert rc == 0
    assert payload["scar_id"] == 1 and payload["orphaned"] is True
    assert payload["path"].endswith("0001-a.deadend.md")
    assert "gone" in payload["reason"]


def test_orphan_apply_plain_line_is_unchanged(repo, capsys):
    _orphan_detected_scar(repo)
    capsys.readouterr()
    assert main(["orphan", "--apply", "--id", "1"]) == 0
    assert capsys.readouterr().out == (
        "orphaned -> .scars/0001-a.deadend.md (never fires again; history kept, "
        "anchors-live-again hint will surface if the code returns)\n")


def test_json_early_exits_stay_plain_without_the_flag(bare, capsys):
    assert main(["reanchor"]) == 1
    assert capsys.readouterr().out == (
        "no .scars/ directory found (walked up to repo root). Run: scar init\n")
