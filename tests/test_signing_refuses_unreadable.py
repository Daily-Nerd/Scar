"""Signing refuses before any touch while a scar file cannot be read (#439).

The checker is given the receipts of existing scars as `seen`, so one act is
never accepted for a second scar. A file the store cannot read has no receipt
the store can list, so the list would be short exactly where it matters. The
store reports such files, and signing stops: no head, no touch, no write.
"""
import json
import os
import subprocess

import pytest
from presence_fakes import FakeBinary

from scar import presence, signedpromote
from scar.cli import main
from scar.store import ScarStore, UnreadableScars, init_scars

ORG = "uEiARDVf_JDy-0rOjSM1lEH6YMSEdD2RQiuviL-AsSVLq4A"
HEAD = {"head": {"id": "uEiBheadheadheadheadheadheadheadheadheadheadhead", "kind": "chain"},
        "reason": "ok"}
RECEIPT = "uEiReceipt0000000000000000000000000000000000"

CANDIDATE = """\
---
type: deadend
title: Tried {n}, failed
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

Why {n} failed.
"""

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

NO_FRONTMATTER = b"promotion_receipt: R-hidden\njust notes\n"
NOT_UTF8 = b"\xff\xfe---\ntitle: nope\n---\n"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "--allow-empty", "-m", "root"], check=True)
    init_scars(tmp_path)
    (tmp_path / ".scars" / "config.json").write_text(
        json.dumps({"org": ORG, "signed_from": 1}))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def fakes(tmp_path_factory, monkeypatch):
    d = tmp_path_factory.mktemp("bin")
    signer = FakeBinary(d, "handsal-sign")
    checker = FakeBinary(d, "handsal-check")
    monkeypatch.setattr(presence, "SIGNER", str(signer.path))
    monkeypatch.setattr(presence, "CHECKER", str(checker.path))
    checker.reply("head", (HEAD, 0))
    checker.reply("enrolled", ({"enrolled": True, "reason": "ok"}, 0))
    checker.reply("check-act", ({"class": "apple-se-bio", "reason": "ok",
                                 "receipt_id": RECEIPT, "valid": True}, 0))
    signer.reply("pubkey", ({"kid": "KID43", "class": "apple-se-bio",
                             "agree": {"x": "a", "y": "b"}, "sign": {"x": "c", "y": "d"}}, 0))
    signer.reply("render", ({"first_line": "l", "text": "t"}, 0))
    signer.reply("sign", {"out": json.dumps({"receipt_id": RECEIPT}), "code": 0,
                          "act_from_decision": True})
    return signer, checker


def _unreadable(repo, kind, name="0009-broken.fence.md"):
    path = repo / ".scars" / name
    if kind == "no-frontmatter":
        path.write_bytes(NO_FRONTMATTER)
    elif kind == "not-utf8":
        path.write_bytes(NOT_UTF8)
    else:
        path.write_text("---\ntitle: x\n---\n", encoding="utf-8")
        path.chmod(0)
        if os.access(path, os.R_OK):
            path.chmod(0o644)
            pytest.skip("this process can read a mode 000 file (running as root)")
    return path


KINDS = ["no-frontmatter", "not-utf8", "oserror"]


def _snapshot(repo):
    # Readable only: the oserror case leaves a mode 000 file in the tree.
    out = {}
    for p in (repo / ".scars").rglob("*"):
        if p.is_file() and os.access(p, os.R_OK):
            out[p] = p.read_bytes()
    return out


@pytest.fixture(autouse=True)
def _restore_modes(repo):
    yield
    for p in (repo / ".scars").glob("*.md"):
        p.chmod(0o644)


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_seen_receipts_refuses_and_names_the_unreadable_file(repo, kind):
    (repo / ".scars" / "0001-good.fence.md").write_text(
        ACTIVE.replace("deadend", "fence"), encoding="utf-8")
    bad = _unreadable(repo, kind)
    with pytest.raises(UnreadableScars) as exc:
        ScarStore.discover(repo).seen_receipts()
    assert [f.name for f, _why in exc.value.files] == [bad.name]
    assert bad.name in str(exc.value)
    assert "scar lint" in str(exc.value)


@pytest.mark.parametrize("kind", KINDS)
def test_unreadable_lists_every_file_the_store_cannot_read(repo, kind):
    bad = _unreadable(repo, kind)
    store = ScarStore.discover(repo)
    assert [f.name for f, _why in store.unreadable()] == [bad.name]


def test_seen_receipts_is_unchanged_when_every_file_reads(repo):
    (repo / ".scars" / "0001-a.fence.md").write_text(
        ACTIVE.replace("deadend", "fence").replace(
            "status: active", "promotion_receipt: uEiA\nstatus: active"), encoding="utf-8")
    store = ScarStore.discover(repo)
    assert store.unreadable() == []
    assert store.seen_receipts() == ["uEiA"]


# ---------------------------------------------------------------------------
# Signing: nothing is asked of the person, nothing is written
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_machine_checks_refuse_before_any_call_to_the_checker(repo, fakes, kind):
    signer, checker = fakes
    bad = _unreadable(repo, kind)
    with pytest.raises(signedpromote.SignedPromoteError) as exc:
        signedpromote.machine_checks(ScarStore.discover(repo), ORG, 1)
    assert bad.name in str(exc.value)
    assert signer.calls() == [] and checker.calls() == []


@pytest.mark.parametrize("kind", KINDS)
def test_an_unreadable_scar_blocks_a_promotion_batch_before_the_first_prompt(
        repo, fakes, capsys, kind):
    signer, checker = fakes
    for i in (1, 2):
        (repo / ".scars" / "candidates" / f"cand-{i}.md").write_text(
            CANDIDATE.format(n=i), encoding="utf-8")
    bad = _unreadable(repo, kind)
    before = _snapshot(repo)
    assert main(["promote", "cand-1.md", "cand-2.md", "--reviewer", "kibukx"]) == 1
    out = capsys.readouterr().out
    assert bad.name in out and "no touch was requested" in out
    assert signer.calls("sign") == [] and checker.calls("check-act") == []
    assert _snapshot(repo) == before


@pytest.mark.parametrize("kind", KINDS)
def test_an_unreadable_scar_blocks_resign_before_the_touch(repo, fakes, capsys, kind):
    signer, checker = fakes
    (repo / ".scars" / "0001-tried-x.deadend.md").write_text(ACTIVE, encoding="utf-8")
    bad = _unreadable(repo, kind)
    before = _snapshot(repo)
    assert main(["resign", "--id", "1"]) == 1
    out = capsys.readouterr().out
    assert bad.name in out and "no touch was requested" in out
    assert signer.calls("sign") == [] and checker.calls("check-act") == []
    assert _snapshot(repo) == before


@pytest.mark.parametrize("kind", KINDS)
def test_sign_record_keeps_the_refusal_as_a_backstop_before_the_touch(repo, fakes, kind):
    signer, checker = fakes
    (repo / ".scars" / "candidates" / "cand-1.md").write_text(
        CANDIDATE.format(n=1), encoding="utf-8")
    bad = _unreadable(repo, kind)
    store = ScarStore.discover(repo)
    prepared = store.prepare_promotion(store.candidates()[0], "kibukx")
    with pytest.raises(signedpromote.SignedPromoteError) as exc:
        signedpromote.sign_record(store, prepared.scar, ORG, "cand-1.md")
    assert bad.name in str(exc.value)
    assert not exc.value.touch_spent
    assert signer.calls("sign") == []


def test_a_file_that_becomes_unreadable_during_the_touch_is_a_spent_touch(
        repo, fakes, monkeypatch):
    signer, checker = fakes
    (repo / ".scars" / "candidates" / "cand-1.md").write_text(
        CANDIDATE.format(n=1), encoding="utf-8")
    store = ScarStore.discover(repo)
    prepared = store.prepare_promotion(store.candidates()[0], "kibukx")
    real_sign = presence.sign

    def sign_then_break(decision):
        result = real_sign(decision)
        (repo / ".scars" / "0009-late.fence.md").write_bytes(NOT_UTF8)
        return result

    monkeypatch.setattr(presence, "sign", sign_then_break)
    with pytest.raises(signedpromote.SignedPromoteError) as exc:
        signedpromote.sign_record(store, prepared.scar, ORG, "cand-1.md")
    assert exc.value.touch_spent
    assert "0009-late.fence.md" in str(exc.value)
    assert checker.calls("check-act") == []
