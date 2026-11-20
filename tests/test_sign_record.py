"""sign_record: the one owner of the sign sequence (#418).

Called directly, with no candidate and no file being created, against the strict
fake signer: it must go head, sign, check, and hand back an act bound to the
record's own hash.
"""
from __future__ import annotations

import subprocess

import pytest
from presence_fakes import FakeBinary

from scar import presence, record
from scar.model import parse_scar_text
from scar.signedpromote import SignedPromoteError, sign_record
from scar.store import ScarStore, init_scars

ORG = "uEiARDVf_JDy-0rOjSM1lEH6YMSEdD2RQiuviL-AsSVLq4A"
HEAD = {"id": "uEiBheadheadheadheadheadheadheadheadheadheadhead", "kind": "chain"}
RECEIPT = "uEiReceipt0000000000000000000000000000000000"

ACTIVE = """\
---
id: 1
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
status: active
---

Why X failed.
"""


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "--allow-empty", "-m", "root"], check=True)
    init_scars(tmp_path)
    return tmp_path


@pytest.fixture
def fakes(tmp_path_factory, monkeypatch):
    d = tmp_path_factory.mktemp("bin")
    signer = FakeBinary(d, "handsal-sign")
    checker = FakeBinary(d, "handsal-check")
    monkeypatch.setattr(presence, "SIGNER", str(signer.path))
    monkeypatch.setattr(presence, "CHECKER", str(checker.path))
    checker.reply("head", ({"head": HEAD, "reason": "ok"}, 0))
    checker.reply("check-act", ({"class": "apple-se-bio", "reason": "ok",
                                 "receipt_id": RECEIPT, "valid": True}, 0))
    signer.reply("sign", {"out": '{"receipt_id": "%s"}' % RECEIPT, "code": 0,
                          "act_from_decision": True})
    return signer, checker


def test_sign_record_goes_head_sign_check_and_binds_the_act_to_the_record(
        repo, fakes, monkeypatch):
    order = []
    for name in ("head", "sign", "ingest"):
        real = getattr(presence, name)

        def spy(*args, _real=real, _name=name):
            order.append(_name)
            return _real(*args)

        monkeypatch.setattr(presence, name, spy)
    scar = parse_scar_text(ACTIVE)

    jws, receipt = sign_record(ScarStore(root=repo, scars_dir=repo / ".scars"), scar, ORG, "tried-x.md")

    assert order == ["head", "sign", "ingest"]
    assert presence.act_inputs_hash(jws) == record.record_hash(scar)
    assert receipt == RECEIPT


def test_sign_record_asks_for_no_touch_when_the_head_is_refused(repo, fakes):
    signer, checker = fakes
    checker.reply("head", ({"head": None, "reason": "no_head"}, 1))
    with pytest.raises(SignedPromoteError) as exc:
        sign_record(ScarStore(root=repo, scars_dir=repo / ".scars"), parse_scar_text(ACTIVE), ORG, "tried-x.md")
    assert not exc.value.touch_spent
    assert signer.calls("sign") == []
