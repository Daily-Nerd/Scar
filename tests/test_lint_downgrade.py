"""The downgrade guard (#392): `scar lint` is structural, runs in CI with no
verifier, and refuses the way signing could be switched off by editing files.

The attack is not forging an act (the checker catches that); it is deleting the
org from the config so promote stops asking for one, or dropping an act from a
scar. Both are structural facts a lint can see.
"""
import json
import subprocess

import pytest

from scar import config
from scar.cli import main
from scar.store import ScarStore, init_scars

ORG = "uEiARDVf_JDy-0rOjSM1lEH6YMSEdD2RQiuviL-AsSVLq4A"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    init_scars(tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def scar(repo, n, act=True, status="active", name=None):
    lines = [f"id: {n}", "type: fence", f"title: Scar {n}", "severity: low", "confidence: 0.5",
             "created: 2026-11-01", 'authors: ["claude-code"]', "promoted_by: kibukx"]
    if act:
        lines += [f"promotion_act: a.b.{n}", f"promotion_receipt: uEiR{n}"]
    lines += ["anchors:", "  - path: src/x.py", f"status: {status}"]
    p = repo / ".scars" / (name or f"{n:04d}-s{n}.fence.md")
    p.write_text("---\n" + "\n".join(lines) + "\n---\n\nBody.\n")
    return p


def cfg(repo, **kw):
    (repo / ".scars" / "config.json").write_text(json.dumps(kw))


def lint(capsys):
    rc = main(["lint"])
    return rc, capsys.readouterr().out


def test_without_an_org_and_without_acts_nothing_changes(repo, capsys):
    scar(repo, 1, act=False)
    rc, _ = lint(capsys)
    assert rc == 0


def test_an_act_with_no_config_is_an_error(repo, capsys):
    scar(repo, 1)
    rc, out = lint(capsys)
    assert rc == 1
    assert "promotion_act" in out and "no org" in out


def test_deleting_the_org_while_acts_remain_is_an_error(repo, capsys):
    scar(repo, 1)
    cfg(repo)  # the org key is gone
    rc, out = lint(capsys)
    assert rc == 1
    assert "promotion_act" in out and "no org" in out


def test_org_and_every_scar_signed_is_clean(repo, capsys):
    scar(repo, 1)
    scar(repo, 2)
    cfg(repo, org=ORG, signed_from=1)
    rc, _ = lint(capsys)
    assert rc == 0


def test_a_scar_promoted_under_the_org_without_an_act_is_an_error(repo, capsys):
    scar(repo, 1)
    scar(repo, 2, act=False)
    cfg(repo, org=ORG, signed_from=1)
    rc, out = lint(capsys)
    assert rc == 1
    assert "0002-s2.fence.md" in out and "promotion_act" in out


@pytest.mark.parametrize("status", ["archived", "orphaned", "challenged"])
def test_the_guard_covers_every_status_a_promoted_scar_can_have(repo, capsys, status):
    scar(repo, 1, act=False, status=status)
    cfg(repo, org=ORG, signed_from=1)
    rc, out = lint(capsys)
    assert rc == 1 and "promotion_act" in out


def test_scars_before_signed_from_need_no_act(repo, capsys):
    scar(repo, 1, act=False)
    scar(repo, 2, act=False)
    scar(repo, 3)
    cfg(repo, org=ORG, signed_from=3)
    rc, _ = lint(capsys)
    assert rc == 0


def test_an_org_with_no_signed_from_is_an_error_that_names_the_fix(repo, capsys):
    scar(repo, 1, act=False)
    cfg(repo, org=ORG)
    rc, out = lint(capsys)
    assert rc == 1
    assert "signed_from" in out and "next id is 2" in out


def test_signed_from_beyond_the_next_id_would_let_unsigned_scars_through(repo, capsys):
    scar(repo, 1, act=False)
    cfg(repo, org=ORG, signed_from=50)
    rc, out = lint(capsys)
    assert rc == 1
    assert "signed_from" in out and "50" in out


def test_signed_from_at_the_next_id_is_the_normal_state_of_a_new_org(repo, capsys):
    scar(repo, 1, act=False)
    cfg(repo, org=ORG, signed_from=2)
    rc, _ = lint(capsys)
    assert rc == 0


def test_candidates_are_outside_the_guard(repo, capsys):
    cfg(repo, org=ORG, signed_from=1)
    (repo / ".scars" / "candidates" / "c.md").write_text(
        "---\ntype: fence\ntitle: C\nseverity: low\nconfidence: 0.5\ncreated: 2026-11-01\n"
        'authors: ["claude-code"]\nanchors:\n  - path: src/x.py\nstatus: candidate\n---\n\nBody.\n')
    rc, _ = lint(capsys)
    assert rc == 0


@pytest.mark.parametrize("content", ["{bad", "[]", '{"org": ""}', '{"org": 5}',
                                     '{"org": "o", "signed_from": 0}',
                                     '{"org": "o", "signed_from": "3"}',
                                     '{"org": "o", "signed_from": true}',
                                     '{"org": "o", "signed_from": 1.5}'])
def test_a_config_that_cannot_be_read_is_an_error_not_no_org(repo, capsys, content):
    scar(repo, 1, act=False)
    (repo / ".scars" / "config.json").write_text(content)
    rc, out = lint(capsys)
    assert rc == 1
    assert "config.json" in out


def test_the_guard_needs_no_verifier(repo, capsys, monkeypatch, tmp_path):
    from scar import presence
    monkeypatch.setattr(presence, "CHECKER", str(tmp_path / "absent"))
    monkeypatch.setattr(presence, "SIGNER", str(tmp_path / "absent"))
    scar(repo, 1)
    cfg(repo, org=ORG, signed_from=1)
    rc, _ = lint(capsys)
    assert rc == 0


def test_config_reads_signed_from():
    assert config.Config(org="o", signed_from=3).signed_from == 3


def test_promote_refuses_an_org_without_signed_from_before_any_touch(repo, capsys, tmp_path, monkeypatch):
    from presence_fakes import FakeBinary

    from scar import presence
    signer = FakeBinary(tmp_path, "handsal-sign")
    checker = FakeBinary(tmp_path, "handsal-check")
    monkeypatch.setattr(presence, "SIGNER", str(signer.path))
    monkeypatch.setattr(presence, "CHECKER", str(checker.path))
    cfg(repo, org=ORG)
    cand = repo / ".scars" / "candidates" / "c.md"
    cand.write_text(
        "---\ntype: fence\ntitle: C\nseverity: low\nconfidence: 0.5\ncreated: 2026-11-01\n"
        'authors: ["claude-code"]\nanchors:\n  - path: src/x.py\nstatus: candidate\n---\n\nBody.\n')
    before = cand.read_bytes()
    assert main(["promote", "c.md", "--reviewer", "k"]) == 1
    out = capsys.readouterr().out
    assert "signed_from" in out
    assert signer.calls() == [] and checker.calls() == []
    assert cand.read_bytes() == before
    assert ScarStore.discover(repo).next_id() == 1
