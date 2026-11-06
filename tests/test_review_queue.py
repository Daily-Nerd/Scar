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
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


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
