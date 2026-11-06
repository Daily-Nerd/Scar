"""Review queue (#354): lint --json carries review_due, and
`lint --review-queue` renders the CI-usable review signals as markdown."""
import json
import subprocess

from scar.cli import main
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
    due = next(r for r in rows if "#1 " in r)
    rot = next(r for r in rows if "#2 " in r)
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
    rows = [ln for ln in out.splitlines() if "#3 " in ln]
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
    assert "#4" in out
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
    assert "- [ ] #1 " in out


def test_review_queue_does_not_change_exit_codes(tmp_path, monkeypatch, capsys):
    _due_and_rot(tmp_path, monkeypatch)
    rc, _ = _queue(capsys, "--fail-partial-rot")
    assert rc == 1
    assert main(["lint", "--fail-partial-rot"]) == 1


def test_review_queue_has_no_em_dash(tmp_path, monkeypatch, capsys):
    _due_and_rot(tmp_path, monkeypatch)
    _, out = _queue(capsys)
    assert "\u2014" not in out
