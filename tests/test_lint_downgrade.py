"""The downgrade guard (#392): `scar lint` is structural, runs in CI with no
verifier, and refuses the way signing could be switched off by editing files.

The attack is not forging an act (the checker catches that); it is deleting the
org from the config so promote stops asking for one, or dropping an act from a
scar. Both are structural facts a lint can see.
"""
import base64
import json
import subprocess

import pytest

from scar import config, renames
from scar.cli import main
from scar.model import parse_scar_text
from scar.record import record_hash
from scar.store import ScarStore, init_scars

ORG = "uEiARDVf_JDy-0rOjSM1lEH6YMSEdD2RQiuviL-AsSVLq4A"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    init_scars(tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def make_act(inputs_hash):
    """A compact JWS shape with a real payload and a junk signature. Scar only
    decodes the payload here; whether the signature is genuine is the checker's job."""
    payload = base64.urlsafe_b64encode(
        json.dumps({"inputs_hash": inputs_hash}).encode()).rstrip(b"=").decode()
    return f"h.{payload}.s"


def scar(repo, n, act=True, status="active", name=None, act_value=None):
    lines = [f"id: {n}", "type: fence", f"title: Scar {n}", "severity: low", "confidence: 0.5",
             "created: 2026-11-01", 'authors: ["claude-code"]', "promoted_by: kibukx"]
    tail = ["anchors:", "  - path: src/x.py", f"status: {status}"]
    p = repo / ".scars" / (name or f"{n:04d}-s{n}.fence.md")

    def write(extra):
        p.write_text("---\n" + "\n".join(lines + extra + tail) + "\n---\n\nBody.\n")

    write([])
    if act:
        # The act signs the record, so it carries the hash of this very file.
        act_text = act_value if act_value is not None else make_act(
            record_hash(parse_scar_text(p.read_text())))
        write([f"promotion_act: {act_text}", f"promotion_receipt: uEiR{n}"])
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


# ---- a stale act (#409): the act no longer matches the record it sits on ----


def signed_repo(repo):
    p = scar(repo, 1)
    cfg(repo, org=ORG, signed_from=1)
    return p


def test_an_act_that_matches_its_record_is_clean_and_says_nothing(repo, capsys):
    signed_repo(repo)
    rc, out = lint(capsys)
    assert rc == 0
    assert "signature" not in out and "matches" not in out


def test_an_anchor_rewrite_makes_the_act_stale(repo, capsys):
    p = signed_repo(repo)
    assert renames.apply_anchor_rewrite(p, "path", {"src/x.py": "src/y.py"})
    rc, out = lint(capsys)
    assert rc == 1
    assert "0001-s1.fence.md" in out and "scar 1" in out and "new signature" in out
    assert "promoted_from" in out


@pytest.mark.parametrize("old,new", [("Body.", "Body, edited."),
                                     ("title: Scar 1", "title: Scar one"),
                                     ("severity: low", "severity: high")])
def test_editing_a_signed_field_makes_the_act_stale(repo, capsys, old, new):
    p = signed_repo(repo)
    p.write_text(p.read_text().replace(old, new))
    rc, out = lint(capsys)
    assert rc == 1
    assert "scar 1" in out and "new signature" in out


def test_transition_and_evidence_notes_do_not_stale_the_act(repo, capsys):
    p = signed_repo(repo)
    store = ScarStore.discover(repo)
    store.append_evidence_note(1, "note: seen again")
    store.transition(1, "challenged", "looked odd", "2026-11-02")
    assert "challenged" in p.read_text()
    rc, out = lint(capsys)
    assert rc == 0, out


@pytest.mark.parametrize("act_value", [
    "only.two",                      # not three segments
    "a.b.c.d",                       # too many segments
    "h.!!not-base64!!.s",            # payload is not base64
    "h." + base64.urlsafe_b64encode(b"not json").decode().rstrip("=") + ".s",
    "h." + base64.urlsafe_b64encode(b"[1]").decode().rstrip("=") + ".s",   # not an object
    "h." + base64.urlsafe_b64encode(b"{}").decode().rstrip("=") + ".s",    # no inputs_hash
    "h." + base64.urlsafe_b64encode(b'{"inputs_hash": 5}').decode().rstrip("=") + ".s",
])
def test_an_act_that_cannot_be_read_is_an_error(repo, capsys, act_value):
    scar(repo, 1, act_value=act_value)
    cfg(repo, org=ORG, signed_from=1)
    rc, out = lint(capsys)
    assert rc == 1
    assert "scar 1" in out and "cannot be read" in out


def test_a_scar_with_no_act_gets_no_stale_finding(repo, capsys):
    scar(repo, 1, act=False)
    cfg(repo, org=ORG, signed_from=2)
    rc, out = lint(capsys)
    assert rc == 0 and "new signature" not in out


def test_signing_off_reports_one_error_for_a_stale_act_not_two(repo, capsys):
    p = scar(repo, 1)
    p.write_text(p.read_text().replace("Body.", "Edited."))
    rc, out = lint(capsys)  # no org: the act has no org to answer to
    assert rc == 1
    assert "no org" in out and "new signature" not in out
