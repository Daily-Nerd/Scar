"""Review queue (#354): lint --json carries review_due, and
`lint --review-queue` renders the CI-usable review signals as markdown."""
import json
import re
import subprocess

from scar.cli import _md_code, main
from scar.store import init_scars

SCAR = """\
---
id: {id}
type: landmine
title: {title}
severity: medium
confidence: 0.8
created: 2026-06-10
authors: ["claude-code"]
anchors:
{anchors}
evidence:
  - commit: abc1234
{extra}status: active
---

{body}
"""


def _write(root, name, *, id, title, anchors, extra="", body="Plain body."):
    text = SCAR.format(id=id, title=title, anchors="\n".join(
        f"  - path: {a}" for a in anchors), extra=extra, body=body)
    (root / ".scars" / name).write_text(text)


def _repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    init_scars(tmp_path)
    (tmp_path / "src" / "live").mkdir(parents=True)
    (tmp_path / "src" / "live" / "mod.py").write_text("x = 1\n")
    monkeypatch.chdir(tmp_path)


def _commit(root):
    """Commit, then point every scar's evidence at that real commit so the
    fixtures are not accidentally flagged as unreachable-evidence."""
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                         capture_output=True, text=True).stdout.strip()
    for f in (root / ".scars").glob("0*.md"):
        f.write_text(f.read_text().replace("abc1234", sha))
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "evidence"], cwd=root, check=True)


def _due_and_rot(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    _write(tmp_path, "0001-due.landmine.md", id=1, title="Due scar",
           anchors=["src/live/"], extra="review_after: 2020-01-01\n")
    _write(tmp_path, "0002-rot.landmine.md", id=2, title="Rotted scar",
           anchors=["src/live/", "src/gone/"], body="SECRET-BODY-TEXT")
    _commit(tmp_path)


def test_lint_json_review_due_matches_status(tmp_path, monkeypatch, capsys):
    _due_and_rot(tmp_path, monkeypatch)
    main(["lint", "--json"])
    lint_due = json.loads(capsys.readouterr().out)["review_due"]
    main(["status", "--json"])
    status_due = json.loads(capsys.readouterr().out)["review_due"]
    assert lint_due == status_due
    assert [d["id"] for d in lint_due] == [1]
    assert lint_due[0]["review_after"] == "2020-01-01"


def test_lint_json_review_due_empty_list_when_none(tmp_path, monkeypatch, capsys):
    _repo(tmp_path, monkeypatch)
    main(["lint", "--json"])
    assert json.loads(capsys.readouterr().out)["review_due"] == []


# --- lint --review-queue ---

def _queue(capsys, *extra):
    rc = main(["lint", "--review-queue", *extra])
    return rc, capsys.readouterr().out


def test_review_queue_empty_store_prints_nothing(tmp_path, monkeypatch, capsys):
    _repo(tmp_path, monkeypatch)
    rc, out = _queue(capsys)
    assert rc == 0
    assert out == ""


def test_review_queue_one_row_per_flagged_scar(tmp_path, monkeypatch, capsys):
    _due_and_rot(tmp_path, monkeypatch)
    rc, out = _queue(capsys)
    assert rc == 0
    rows = [ln for ln in out.splitlines() if ln.startswith("- [ ] ")]
    assert len(rows) == 2
    due = next(r for r in rows if "scar 1 " in r)
    rot = next(r for r in rows if "scar 2 " in r)
    assert "Due scar" in due and "src/live/" in due
    assert "review_after 2020-01-01" in due
    assert "Suggested:" in due and "scar archive 1" in due
    assert "Rotted scar" in rot and "partial rot" in rot
    assert "Suggested: `scar reanchor`" in rot


def test_review_queue_merges_states_into_one_row(tmp_path, monkeypatch, capsys):
    _repo(tmp_path, monkeypatch)
    _write(tmp_path, "0003-both.landmine.md", id=3, title="Both",
           anchors=["src/live/", "src/gone/"], extra="review_after: 2020-01-01\n")
    _commit(tmp_path)
    _, out = _queue(capsys)
    rows = [ln for ln in out.splitlines() if "scar 3 " in ln]
    assert len(rows) == 1
    assert "partial rot" in rows[0] and "review_after 2020-01-01" in rows[0]


def test_review_queue_never_prints_body_or_violation(tmp_path, monkeypatch, capsys):
    _repo(tmp_path, monkeypatch)
    _write(tmp_path, "0004-v.landmine.md", id=4, title="Violating",
           anchors=["src/live/", "src/gone/"],
           extra="violation: 'LEAKY_REGEX_\\d+'\n",
           body="SECRET-BODY-TEXT")
    _commit(tmp_path)
    _, out = _queue(capsys)
    assert "scar 4 " in out
    assert "SECRET-BODY-TEXT" not in out
    assert "LEAKY_REGEX" not in out
    assert "evidence" not in out.lower()


def test_review_queue_excludes_firing_log_signals(tmp_path, monkeypatch, capsys):
    _repo(tmp_path, monkeypatch)
    _write(tmp_path, "0005-n.landmine.md", id=5, title="Healthy",
           anchors=["src/live/"], extra="review_after_firings: 1\n")
    _commit(tmp_path)
    _, out = _queue(capsys)
    assert out == ""


def test_review_queue_notes_firing_log_exclusion_when_non_empty(tmp_path, monkeypatch, capsys):
    _due_and_rot(tmp_path, monkeypatch)
    _, out = _queue(capsys)
    last = out.rstrip("\n").splitlines()[-1]
    assert "firing" in last.lower()


def test_review_queue_plain_even_on_tty(tmp_path, monkeypatch, capsys):
    from scar import output
    _due_and_rot(tmp_path, monkeypatch)
    monkeypatch.setattr(output, "is_tty", lambda: True)
    _, out = _queue(capsys)
    assert "\x1b[" not in out
    assert "- [ ] scar 1 " in out


def test_review_queue_does_not_change_exit_codes(tmp_path, monkeypatch, capsys):
    _due_and_rot(tmp_path, monkeypatch)
    rc, _ = _queue(capsys, "--fail-partial-rot")
    assert rc == 1
    assert main(["lint", "--fail-partial-rot"]) == 1


def test_review_queue_has_no_em_dash(tmp_path, monkeypatch, capsys):
    _due_and_rot(tmp_path, monkeypatch)
    _, out = _queue(capsys)
    assert "\u2014" not in out


def _broken(root, name="0009-broken.landmine.md", marker="BROKEN-BODY-TEXT"):
    # No frontmatter terminator and no required fields: lint_text errors.
    (root / ".scars" / name).write_text(f"---\nid: 9\ntitle: {marker}\n\n{marker}\n")


def test_review_queue_lists_broken_file_and_still_exits_1(tmp_path, monkeypatch, capsys):
    """#359: a file with lint errors must be in the queue."""
    _due_and_rot(tmp_path, monkeypatch)
    _broken(tmp_path)
    rc, out = _queue(capsys)
    assert rc == 1
    assert "- [ ] `.scars/0009-broken.landmine.md`: file has lint errors." in out
    assert "scar lint" in out


def test_review_queue_only_broken_file_is_non_empty(tmp_path, monkeypatch, capsys):
    _repo(tmp_path, monkeypatch)
    _broken(tmp_path)
    rc, out = _queue(capsys)
    assert rc == 1
    assert out.strip() != ""
    assert len([r for r in out.splitlines() if r.startswith("- [ ]")]) == 1


def test_review_queue_broken_row_never_leaks_body_or_message(tmp_path, monkeypatch, capsys):
    _repo(tmp_path, monkeypatch)
    _broken(tmp_path)
    data_rc = main(["lint", "--json"])
    msgs = [f["message"] for f in json.loads(capsys.readouterr().out)["findings"]]
    assert data_rc == 1 and msgs
    _, out = _queue(capsys)
    assert "BROKEN-BODY-TEXT" not in out
    for m in msgs:
        assert m not in out


def test_review_queue_broken_rows_are_sorted_by_path(tmp_path, monkeypatch, capsys):
    _repo(tmp_path, monkeypatch)
    _broken(tmp_path, "0012-b.landmine.md")
    _broken(tmp_path, "0010-a.landmine.md")
    _, out = _queue(capsys)
    rows = [r for r in out.splitlines() if r.startswith("- [ ]")]
    assert "0010-a" in rows[0] and "0012-b" in rows[1]


def test_review_queue_truncates_to_issue_body_limit(tmp_path, monkeypatch, capsys):
    """GitHub rejects issue bodies over 65536 characters (#359)."""
    _repo(tmp_path, monkeypatch)
    for i in range(1200):
        _broken(tmp_path, f"{3000 + i}-{'x' * 40}.landmine.md")
    rc, out = _queue(capsys)
    assert rc == 1
    assert len(out) <= 65536
    assert "more row(s) omitted" in out
    assert out.count("- [ ]") < 1200


def test_review_queue_lists_duplicate_id_scars_that_parse(tmp_path, monkeypatch, capsys):
    """Duplicate ids parse fine (and can fire) but are lint errors: both get rows."""
    _repo(tmp_path, monkeypatch)
    _write(tmp_path, "0001-a.landmine.md", id=1, title="A", anchors=["src/live/"])
    _write(tmp_path, "0002-b.landmine.md", id=1, title="B", anchors=["src/live/"])
    _commit(tmp_path)
    rc, out = _queue(capsys)
    assert rc == 1
    assert "`.scars/0001-a.landmine.md`: file has lint errors." in out
    assert "`.scars/0002-b.landmine.md`: file has lint errors." in out
    assert "duplicate id" not in out


# --- inert rendering (#362) ---

# CommonMark code span: N backticks, content with no run of exactly N, N backticks.
_SPAN = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")


def _outside_spans(text):
    return _SPAN.sub("", text)


def test_md_code_plain_and_empty():
    assert _md_code("abc") == "`abc`"
    assert _md_code("") == "`(empty)`"
    assert _md_code(" \n\t ") == "`(empty)`"


def test_md_code_collapses_whitespace_and_newlines():
    assert _md_code("a\n\n  b\r\nc") == "`a b c`"


def test_md_code_fences_longer_than_longest_backtick_run():
    assert _md_code("a`b") == "``a`b``"
    assert _md_code("a``b`c") == "```a``b`c```"


def test_md_code_pads_when_edge_is_backtick():
    assert _md_code("`a") == "`` `a ``"
    assert _md_code("a`") == "`` a` ``"
    assert _md_code("`") == "`` ` ``"


def test_md_code_round_trips_through_span_regex():
    for v in ["x", "a`b", "``", "`a``b`", "@octocat", "a  b\nc"]:
        out = _md_code(v)
        assert _outside_spans(out) == "", out



def _hostile_title():
    # A scar title is one line, so the newline case lives in the _md_code tests.
    return "Ping @octocat see #7 [x](https://e.com) https://e.com <b>h</b> tick`here"


def test_review_queue_title_is_inert(tmp_path, monkeypatch, capsys):
    _repo(tmp_path, monkeypatch)
    _write(tmp_path, "0005-h.landmine.md", id=5, title=_hostile_title(),
           anchors=["src/live/", "src/gone/"])
    _commit(tmp_path)
    _, out = _queue(capsys)
    rows = [r for r in out.splitlines() if r.startswith("- [ ] scar 5 ")]
    assert len(rows) == 1
    row = rows[0]
    assert "``Ping @octocat see #7 [x](https://e.com) https://e.com <b>h</b> tick`here``" in row
    rest = _outside_spans(row[len("- [ ] "):])
    assert not re.search(r"@|#\d|http|<|\[", rest), rest


def test_review_queue_no_hash_digit_outside_code_in_any_row(tmp_path, monkeypatch, capsys):
    _due_and_rot(tmp_path, monkeypatch)
    _, out = _queue(capsys)
    for line in out.splitlines():
        assert not re.search(r"#\d", _outside_spans(line)), line
    assert "scar 1 " in out and "scar 2 " in out


def test_review_queue_backtick_path_cannot_close_span(tmp_path, monkeypatch, capsys):
    _repo(tmp_path, monkeypatch)
    _write(tmp_path, "0006-p.landmine.md", id=6, title="Backtick path",
           anchors=["src/gone`@octocat`x/", "src/live/"])
    _commit(tmp_path)
    _, out = _queue(capsys)
    row = next(r for r in out.splitlines() if r.startswith("- [ ] scar 6 "))
    assert "``src/gone`@octocat`x/``" in row
    assert not re.search(r"@|#\d", _outside_spans(row[len("- [ ] "):]))


def test_review_queue_suggested_command_path_is_one_span(tmp_path, monkeypatch, capsys):
    _repo(tmp_path, monkeypatch)
    _write(tmp_path, "0007-w.landmine.md", id=7, title="Why path",
           anchors=["src/live/`@octocat"], extra="review_after: 2020-01-01\n")
    (tmp_path / "src" / "live" / "`@octocat").write_text("x\n")
    _commit(tmp_path)
    _, out = _queue(capsys)
    row = next(r for r in out.splitlines() if r.startswith("- [ ] scar 7 "))
    assert "``src/live/`@octocat``" in row
    assert not re.search(r"@|#\d", _outside_spans(row[len("- [ ] "):]))


def test_review_queue_failed_file_path_is_inert(tmp_path, monkeypatch, capsys):
    _repo(tmp_path, monkeypatch)
    _broken(tmp_path, "0009-a`@octocat.landmine.md")
    _, out = _queue(capsys)
    row = next(r for r in out.splitlines() if r.startswith("- [ ]"))
    rest = _outside_spans(row[len("- [ ] "):])
    assert not re.search(r"@|#\d", rest), rest
