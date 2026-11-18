"""scar promote with a presence signature (#392): a batch is a guided queue,
and the preflight refuses before any touch. Fakes only."""
import json
import os
import subprocess

import pytest
from presence_fakes import FakeBinary

from scar import presence
from scar.cli import main
from scar.model import parse_scar_text
from scar.store import ScarStore, init_scars

ORG = "uEiARDVf_JDy-0rOjSM1lEH6YMSEdD2RQiuviL-AsSVLq4A"

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


def head(n):
    return {"head": {"id": f"uEiHead{n:040d}", "kind": "chain"}, "reason": "ok"}


def accepted(n):
    return {"class": "apple-se-bio", "reason": "ok", "receipt_id": f"uEiRcpt{n:040d}", "valid": True}


def signed(n):
    return {"jws": f"jws.{n}.sig", "receipt_id": f"uEiRcpt{n:040d}"}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "--allow-empty", "-m", "root"], check=True)
    init_scars(tmp_path)
    (tmp_path / ".scars" / "config.json").write_text(json.dumps({"org": ORG, "signed_from": 1}))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def fakes(tmp_path_factory, monkeypatch):
    d = tmp_path_factory.mktemp("bin")
    signer = FakeBinary(d, "handsal-sign")
    checker = FakeBinary(d, "handsal-check")
    monkeypatch.setattr(presence, "SIGNER", str(signer.path))
    monkeypatch.setattr(presence, "CHECKER", str(checker.path))
    # Every head call answers a new head, so a reused head would show.
    checker.reply("head", *[(head(i), 0) for i in range(1, 12)])
    checker.reply("enrolled", ({"enrolled": True, "reason": "ok"}, 0))
    checker.reply("check-act", *[(accepted(i), 0) for i in range(1, 12)])
    signer.reply("pubkey", ({"kid": "KID43", "class": "apple-se-bio",
                             "agree": {"x": "a", "y": "b"}, "sign": {"x": "c", "y": "d"}}, 0))
    signer.reply("render", ({"first_line": "l", "text": "t"}, 0))
    signer.reply("sign", *[(signed(i), 0) for i in range(1, 12)])
    return signer, checker


def make(repo, count):
    names = []
    for i in range(1, count + 1):
        name = f"cand-{i}.md"
        (repo / ".scars" / "candidates" / name).write_text(CANDIDATE.format(n=i))
        names.append(name)
    return names


def snapshot(repo):
    return {p: p.read_bytes() for p in (repo / ".scars").rglob("*") if p.is_file()}


def run(*names):
    return main(["promote", *names, "--reviewer", "kibukx"])


def test_a_batch_is_signed_one_scar_at_a_time_with_a_fresh_head_each(repo, fakes, capsys):
    signer, checker = fakes
    names = make(repo, 3)
    assert run(*names) == 0
    for i in (1, 2, 3):
        scar = parse_scar_text(next((repo / ".scars").glob(f"000{i}-*.md")).read_text())
        assert scar.id == i
        assert scar.promotion_act == f"jws.{i}.sig"
        assert scar.promotion_receipt == f"uEiRcpt{i:040d}"
    heads_used = [c["stdin_json"]["draft"]["head"]["id"] for c in signer.calls("sign")]
    # head call 1 is the preflight's; each scar then takes its own.
    assert heads_used == [head(n)["head"]["id"] for n in (2, 3, 4)]
    assert len(checker.calls("head")) == 4
    subjects = [c["stdin_json"]["draft"]["body"]["subject"] for c in signer.calls("sign")]
    assert [s.split("-")[0] for s in subjects] == ["1", "2", "3"]
    assert "promoted 3 of 3" in capsys.readouterr().out


def test_receipts_accumulate_into_seen_within_the_batch(repo, fakes):
    signer, checker = fakes
    run(*make(repo, 3))
    seen = [c["stdin_json"]["seen"] for c in checker.calls("check-act")]
    assert seen[0] == []
    assert seen[1] == [f"uEiRcpt{1:040d}"]
    assert seen[2] == sorted([f"uEiRcpt{1:040d}", f"uEiRcpt{2:040d}"])


def test_a_cancel_at_the_second_scar_keeps_the_first_and_leaves_the_rest(repo, fakes, capsys):
    signer, checker = fakes
    signer.reply("sign", (signed(1), 0), ({"error": "user_cancel"}, 1))
    names = make(repo, 3)
    originals = {n: (repo / ".scars" / "candidates" / n).read_bytes() for n in names}
    assert run(*names) == 1
    out = capsys.readouterr().out
    assert "promoted 1 of 3" in out
    assert (repo / ".scars" / "0001-cand-1.deadend.md").exists()
    assert not (repo / ".scars" / "candidates" / "cand-1.md").exists()
    for n in names[1:]:
        assert (repo / ".scars" / "candidates" / n).read_bytes() == originals[n]
    assert not list((repo / ".scars").glob("0002-*"))
    assert len(signer.calls("sign")) == 2          # the third was never asked for
    assert ScarStore.discover(repo).next_id() == 2


def test_a_checker_refusal_at_the_third_scar_is_reported_with_the_count(repo, fakes, capsys):
    signer, checker = fakes
    checker.reply("check-act", (accepted(1), 0), (accepted(2), 0),
                  ({"class": None, "reason": "expired", "receipt_id": "x", "valid": False}, 0))
    names = make(repo, 3)
    before3 = (repo / ".scars" / "candidates" / names[2]).read_bytes()
    assert run(*names) == 1
    out = capsys.readouterr().out
    assert "promoted 2 of 3" in out
    assert "touch was spent" in out and "expired" in out
    assert (repo / ".scars" / "candidates" / names[2]).read_bytes() == before3
    assert ScarStore.discover(repo).next_id() == 3


def test_the_rest_can_be_promoted_afterwards_and_ids_continue(repo, fakes):
    signer, checker = fakes
    signer.reply("sign", (signed(1), 0), ({"error": "user_cancel"}, 1), (signed(3), 0))
    names = make(repo, 2)
    assert run(*names) == 1
    assert run(names[1]) == 0
    assert (repo / ".scars" / "0002-cand-2.deadend.md").exists()


# ---- preflight: nothing is touched before the first prompt -------------------

def test_a_missing_signer_is_refused_before_anything(repo, fakes, monkeypatch, tmp_path, capsys):
    signer, checker = fakes
    monkeypatch.setattr(presence, "SIGNER", str(tmp_path / "nope-sign"))
    names = make(repo, 2)
    before = snapshot(repo)
    assert run(*names) == 1
    out = capsys.readouterr().out
    assert "nope-sign" in out
    assert snapshot(repo) == before
    assert checker.calls("head") == []


def test_a_missing_checker_is_refused_before_anything(repo, fakes, monkeypatch, tmp_path, capsys):
    signer, checker = fakes
    monkeypatch.setattr(presence, "CHECKER", str(tmp_path / "nope-check"))
    names = make(repo, 2)
    before = snapshot(repo)
    assert run(*names) == 1
    assert "nope-check" in capsys.readouterr().out
    assert snapshot(repo) == before
    assert signer.calls("sign") == []


@pytest.mark.parametrize("reason", ["stale_view", "fork", "epoch_superseded", "pin_missing"])
def test_a_refused_head_is_refused_before_the_first_prompt(repo, fakes, capsys, reason):
    signer, checker = fakes
    checker.reply("head", ({"head": None, "reason": reason}, 0))
    names = make(repo, 2)
    before = snapshot(repo)
    assert run(*names) == 1
    assert reason in capsys.readouterr().out
    assert signer.calls("sign") == []
    assert snapshot(repo) == before


@pytest.mark.parametrize("reason", ["local_key_not_active", "local_key_unknown",
                                    "local_class_not_accepted", "stale_view"])
def test_a_key_that_is_not_enrolled_is_refused_before_the_prompt(repo, fakes, capsys, reason):
    # A removed member's key still signs; the refusal must come at the terminal,
    # not after a touch.
    signer, checker = fakes
    checker.reply("enrolled", ({"enrolled": False, "reason": reason}, 0))
    names = make(repo, 2)
    before = snapshot(repo)
    assert run(*names) == 1
    assert reason in capsys.readouterr().out
    assert signer.calls("sign") == [] and signer.calls("render") == []
    assert snapshot(repo) == before


def test_the_enrolled_question_names_this_machines_own_key(repo, fakes):
    signer, checker = fakes
    run(*make(repo, 1))
    assert checker.calls("enrolled")[0]["stdin_json"] == {"device_kid": "KID43", "org": ORG}


def test_no_local_key_is_refused_before_the_prompt(repo, fakes, capsys):
    signer, checker = fakes
    signer.reply("pubkey", ({"error": "key_unavailable"}, 3))
    names = make(repo, 1)
    before = snapshot(repo)
    assert run(*names) == 1
    assert "key_unavailable" in capsys.readouterr().out
    assert signer.calls("sign") == []
    assert snapshot(repo) == before


def test_a_record_the_signer_cannot_show_stops_the_whole_batch_before_any_touch(repo, fakes, capsys):
    signer, checker = fakes
    signer.reply("render", ({"first_line": "l", "text": "t"}, 0),
                 ({"error": "render_refused"}, 2))
    names = make(repo, 3)
    before = snapshot(repo)
    assert run(*names) == 1
    out = capsys.readouterr().out
    assert "render_refused" in out and names[1] in out
    assert signer.calls("sign") == []
    assert snapshot(repo) == before


def test_preflight_renders_each_scar_with_the_id_it_will_get(repo, fakes):
    signer, checker = fakes
    run(*make(repo, 3))
    subjects = [c["stdin_json"]["draft"]["body"]["subject"].split("-")[0]
                for c in signer.calls("render")]
    assert subjects == ["1", "2", "3"]


def test_no_root_commit_is_refused_in_preflight(tmp_path, monkeypatch, fakes, capsys):
    signer, checker = fakes
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    init_scars(tmp_path)
    (tmp_path / ".scars" / "config.json").write_text(json.dumps({"org": ORG, "signed_from": 1}))
    monkeypatch.chdir(tmp_path)
    names = make(tmp_path, 2)
    before = snapshot(tmp_path)
    assert run(*names) == 1
    assert "root commit" in capsys.readouterr().out
    assert snapshot(tmp_path) == before
    assert signer.calls() == [] and checker.calls() == []


def test_what_promote_writes_passes_the_downgrade_guard(repo, fakes, capsys):
    assert run(*make(repo, 2)) == 0
    capsys.readouterr()
    assert main(["lint"]) == 0


def test_the_fixed_paths_are_never_taken_from_the_environment(repo, fakes, monkeypatch, tmp_path):
    # No variable, flag or PATH entry can move the signer or the checker.
    signer, checker = fakes
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    for name in ("handsal-sign", "handsal-check"):
        (decoy / name).write_text("#!/bin/sh\nexit 0\n")
        (decoy / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{decoy}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("SCAR_SIGNER", str(decoy / "handsal-sign"))
    monkeypatch.setenv("SCAR_CHECKER", str(decoy / "handsal-check"))
    assert run(*make(repo, 1)) == 0
    assert len(signer.calls("sign")) == 1
