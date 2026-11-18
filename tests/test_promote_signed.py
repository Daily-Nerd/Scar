"""scar promote with a presence signature (#392): one scar at a time.

Everything runs against fake signer and checker programs at tmp paths. The
real binaries are never run.
"""
import base64
import json
import subprocess

import pytest
from presence_fakes import FakeBinary

from scar import presence
from scar.cli import main
from scar.model import parse_scar_text
from scar.record import promoted_record, promotion_subject
from scar.store import ScarStore, init_scars

ORG = "uEiARDVf_JDy-0rOjSM1lEH6YMSEdD2RQiuviL-AsSVLq4A"
HEAD = {"id": "uEiBheadheadheadheadheadheadheadheadheadheadhead", "kind": "chain"}
JWS = "aaa.bbb.ccc"
RECEIPT = "uEiReceipt0000000000000000000000000000000000"

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


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit",
        "-q", "--allow-empty", "-m", "root")
    init_scars(tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def fakes(tmp_path_factory, monkeypatch):
    d = tmp_path_factory.mktemp("bin")
    signer = FakeBinary(d, "handsal-sign")
    checker = FakeBinary(d, "handsal-check")
    monkeypatch.setattr(presence, "SIGNER", str(signer.path))
    monkeypatch.setattr(presence, "CHECKER", str(checker.path))
    checker.reply("head", ({"head": HEAD, "reason": "ok"}, 0))
    checker.reply("enrolled", ({"enrolled": True, "reason": "ok"}, 0))
    checker.reply("check-act", ({"class": "apple-se-bio", "reason": "ok",
                                 "receipt_id": RECEIPT, "valid": True}, 0))
    signer.reply("pubkey", ({"kid": "KID43", "class": "apple-se-bio",
                             "agree": {"x": "a", "y": "b"}, "sign": {"x": "c", "y": "d"}}, 0))
    signer.reply("render", ({"first_line": "l", "text": "t"}, 0))
    signer.reply("sign", ({"jws": JWS, "receipt_id": RECEIPT}, 0))
    return signer, checker


def set_org(repo, org=ORG):
    (repo / ".scars" / "config.json").write_text(json.dumps({"org": org}))


def candidate(repo, name="tried-x.md", text=CANDIDATE):
    p = repo / ".scars" / "candidates" / name
    p.write_text(text)
    return p


def test_a_signed_promotion_writes_the_act_and_the_receipt(repo, fakes, capsys):
    set_org(repo)
    cand = candidate(repo)
    assert main(["promote", "tried-x", "--reviewer", "kibukx"]) == 0
    active = repo / ".scars" / "0001-tried-x.deadend.md"
    scar = parse_scar_text(active.read_text())
    assert scar.promotion_act == JWS
    assert scar.promotion_receipt == RECEIPT
    assert scar.status == "active" and scar.id == 1
    assert not cand.exists()
    out = capsys.readouterr().out
    assert "promoted -> .scars/0001-tried-x.deadend.md" in out
    assert "signed" in out


def test_the_calls_go_head_then_sign_then_check_with_the_same_bytes(repo, fakes):
    signer, checker = fakes
    set_org(repo)
    candidate(repo)
    assert main(["promote", "tried-x", "--reviewer", "kibukx"]) == 0
    scar = parse_scar_text((repo / ".scars" / "0001-tried-x.deadend.md").read_text())
    record = promoted_record(scar)
    subject = promotion_subject(repo, 1)

    (head_call,) = checker.calls("head")
    assert head_call["stdin_json"] == {"org": ORG}

    (sign_call,) = signer.calls("sign")
    draft = sign_call["stdin_json"]["draft"]
    assert draft["head"] == HEAD
    assert draft["body"] == {"action": "promote", "consumer": "scar", "subject": subject}
    assert draft["expires_in_s"] == "3600"
    assert draft["org"] == ORG
    assert sign_call["stdin_json"]["attachments"] == {
        "decision_b64": base64.b64encode(record).decode("ascii")}

    (check_call,) = checker.calls("check-act")
    sent = check_call["stdin_json"]
    assert sent["jws"] == JWS
    assert sent["subject"] == subject
    assert base64.b64decode(sent["decision_b64"]) == record
    assert sent["seen"] == []


def test_receipts_of_existing_scars_are_passed_as_seen(repo, fakes):
    signer, checker = fakes
    set_org(repo)
    (repo / ".scars" / "0001-old.fence.md").write_text(
        "---\nid: 1\ntype: fence\ntitle: Old\nseverity: low\nconfidence: 0.5\n"
        "promoted_by: k\npromotion_act: o.l.d\npromotion_receipt: uEiOld\n"
        "anchors:\n  - path: src/x.py\nstatus: archived\n---\n\nBody.\n")
    candidate(repo)
    assert main(["promote", "tried-x", "--reviewer", "kibukx"]) == 0
    assert checker.calls("check-act")[0]["stdin_json"]["seen"] == ["uEiOld"]
    assert (repo / ".scars" / "0002-tried-x.deadend.md").exists()


def snapshot(repo):
    return {p: p.read_bytes() for p in (repo / ".scars").rglob("*") if p.is_file()}


def test_a_checker_refusal_after_the_touch_writes_nothing_and_says_so(repo, fakes, capsys):
    signer, checker = fakes
    checker.reply("check-act", ({"class": None, "reason": "validity_too_long",
                                 "receipt_id": "uEiX", "valid": False}, 0))
    set_org(repo)
    candidate(repo)
    before = snapshot(repo)
    assert main(["promote", "tried-x", "--reviewer", "kibukx"]) == 1
    out = capsys.readouterr().out
    assert "touch was spent" in out
    assert "validity_too_long" in out
    assert snapshot(repo) == before                 # candidate byte-identical, no active file
    assert ScarStore.discover(repo).next_id() == 1  # the id was not used up
    assert len(signer.calls("sign")) == 1


def test_a_duplicate_receipt_is_a_refusal_with_the_reason_verbatim(repo, fakes, capsys):
    signer, checker = fakes
    checker.reply("check-act", ({"class": None, "reason": "duplicate_receipt",
                                 "receipt_id": RECEIPT, "valid": False}, 0))
    set_org(repo)
    candidate(repo)
    assert main(["promote", "tried-x", "--reviewer", "k"]) == 1
    assert "duplicate_receipt" in capsys.readouterr().out
    assert not (repo / ".scars" / "0001-tried-x.deadend.md").exists()


def test_an_unusable_checker_answer_after_the_touch_also_writes_nothing(repo, fakes, capsys):
    signer, checker = fakes
    checker.reply("check-act", ("not json", 0))
    set_org(repo)
    candidate(repo)
    before = snapshot(repo)
    assert main(["promote", "tried-x", "--reviewer", "k"]) == 1
    assert "touch was spent" in capsys.readouterr().out
    assert snapshot(repo) == before


@pytest.mark.parametrize("reason", ["stale_view", "fork", "pin_missing", "epoch_superseded"])
def test_a_refused_head_stops_before_any_touch(repo, fakes, capsys, reason):
    signer, checker = fakes
    checker.reply("head", ({"head": None, "reason": reason}, 0))
    set_org(repo)
    candidate(repo)
    before = snapshot(repo)
    assert main(["promote", "tried-x", "--reviewer", "k"]) == 1
    out = capsys.readouterr().out
    assert reason in out
    assert "touch was spent" not in out
    assert signer.calls("sign") == []
    assert snapshot(repo) == before


def test_a_cancelled_touch_leaves_the_candidate_alone(repo, fakes, capsys):
    signer, checker = fakes
    signer.reply("sign", ({"error": "user_cancel"}, 1))
    set_org(repo)
    candidate(repo)
    before = snapshot(repo)
    assert main(["promote", "tried-x", "--reviewer", "k"]) == 1
    out = capsys.readouterr().out
    assert "cancel" in out.lower()
    assert checker.calls("check-act") == []
    assert snapshot(repo) == before


@pytest.mark.parametrize("code,reason", [(2, "render_refused"), (3, "key_unavailable"),
                                         (4, "no_display"), (5, "internal")])
def test_a_signer_refusal_writes_nothing_and_shows_its_code(repo, fakes, capsys, code, reason):
    signer, checker = fakes
    signer.reply("sign", ({"error": reason}, code))
    set_org(repo)
    candidate(repo)
    before = snapshot(repo)
    assert main(["promote", "tried-x", "--reviewer", "k"]) == 1
    out = capsys.readouterr().out
    assert reason in out
    assert checker.calls("check-act") == []
    assert snapshot(repo) == before


def test_missing_binaries_fail_closed_when_an_org_is_set(repo, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(presence, "SIGNER", str(tmp_path / "nope-sign"))
    monkeypatch.setattr(presence, "CHECKER", str(tmp_path / "nope-check"))
    set_org(repo)
    candidate(repo)
    before = snapshot(repo)
    assert main(["promote", "tried-x", "--reviewer", "k"]) == 1
    assert snapshot(repo) == before


def test_without_an_org_promote_is_as_before_and_is_reported_unsigned(repo, monkeypatch, tmp_path, capsys):
    # Neither program is even looked at.
    monkeypatch.setattr(presence, "SIGNER", str(tmp_path / "nope-sign"))
    monkeypatch.setattr(presence, "CHECKER", str(tmp_path / "nope-check"))
    candidate(repo)
    assert main(["promote", "tried-x", "--reviewer", "k"]) == 0
    scar = parse_scar_text((repo / ".scars" / "0001-tried-x.deadend.md").read_text())
    assert scar.promotion_act == "" and scar.promotion_receipt == ""
    assert "not signed" in capsys.readouterr().out


@pytest.mark.parametrize("content", ["{bad", "[]", '{"org": ""}', '{"org": 5}', '{"org": null}'])
def test_a_config_that_exists_but_is_unreadable_is_never_read_as_no_org(repo, fakes, capsys, content):
    signer, checker = fakes
    (repo / ".scars" / "config.json").write_text(content)
    candidate(repo)
    before = snapshot(repo)
    assert main(["promote", "tried-x", "--reviewer", "k"]) == 1
    assert "config.json" in capsys.readouterr().out
    assert snapshot(repo) == before
    assert signer.calls("sign") == []


def test_no_root_commit_refuses_before_any_touch(tmp_path, monkeypatch, fakes, capsys):
    signer, checker = fakes
    git(tmp_path, "init", "-q")  # no commit, so no subject can be named
    init_scars(tmp_path)
    monkeypatch.chdir(tmp_path)
    set_org(tmp_path)
    candidate(tmp_path)
    before = snapshot(tmp_path)
    assert main(["promote", "tried-x", "--reviewer", "k"]) == 1
    assert "root commit" in capsys.readouterr().out
    assert signer.calls("sign") == []
    assert snapshot(tmp_path) == before


def test_a_candidate_that_carries_an_act_does_not_keep_it_without_a_signature(repo, monkeypatch, tmp_path, capsys):
    candidate(repo, text=CANDIDATE.replace(
        "status: candidate", "promotion_act: forged.a.b\npromotion_receipt: uEiForged\nstatus: candidate"))
    assert main(["promote", "tried-x", "--reviewer", "k"]) == 0
    scar = parse_scar_text((repo / ".scars" / "0001-tried-x.deadend.md").read_text())
    assert scar.promotion_act == "" and scar.promotion_receipt == ""
