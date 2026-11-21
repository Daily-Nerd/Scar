"""The checker and signer adapters, against fakes only (#392). The real
binaries are never run: a test that could touch a real key is a bug."""
import pytest
from presence_fakes import FakeBinary

from scar import presence

ORG = "uEiARDVf_JDy-0rOjSM1lEH6YMSEdD2RQiuviL-AsSVLq4A"
HEAD = {"id": "uEiBheadheadheadheadheadheadheadheadheadheadhead", "kind": "chain"}


@pytest.fixture
def checker(tmp_path, monkeypatch):
    fake = FakeBinary(tmp_path, "handsal-check")
    monkeypatch.setattr(presence, "CHECKER", str(fake.path))
    return fake


def test_head_sends_only_the_org_and_returns_the_head_unchanged(checker):
    checker.reply("head", ({"head": HEAD, "reason": "ok"}, 0))
    assert presence.head(ORG) == HEAD
    (call,) = checker.calls("head")
    assert call["stdin_json"] == {"org": ORG}
    assert call["argv"] == ["head"]


@pytest.mark.parametrize("reason", ["stale_view", "fork", "pin_missing", "epoch_superseded"])
def test_a_head_refusal_carries_the_checker_reason_verbatim(checker, reason):
    checker.reply("head", ({"head": None, "reason": reason}, 0))
    with pytest.raises(presence.Refused) as exc:
        presence.head(ORG)
    assert exc.value.reason == reason


@pytest.mark.parametrize("reply,code", [
    ("not json", 0),
    ('{"error":"malformed_input"}', 0),
    ('{"head":null}', 0),
    ('{"head":{"id":"x","kind":"chain"},"reason":"stale_view"}', 0),
    ('{"head":{"id":"x"},"reason":"ok"}', 0),
    ('{"head":{"id":"x","kind":"chain"},"reason":"ok"}', 3),
])
def test_anything_but_a_clean_ok_head_is_never_a_head(checker, reply, code):
    checker.reply("head", (reply, code))
    with pytest.raises(presence.PresenceError):
        presence.head(ORG)


def test_a_missing_checker_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(presence, "CHECKER", str(tmp_path / "nope"))
    with pytest.raises(presence.Unavailable):
        presence.head(ORG)


def test_a_slow_checker_is_unavailable_not_a_hang(checker, monkeypatch):
    checker.reply("head", {"out": "{}", "code": 0, "sleep": 3})
    monkeypatch.setattr(presence, "CHECKER_TIMEOUT_S", 0.5)
    with pytest.raises(presence.Unavailable):
        presence.head(ORG)


# ---- a plausible neighbouring key is not the key (#439) ----------------------

@pytest.mark.parametrize("reply", [
    '{"chain_head":{"id":"x","kind":"chain"},"reason":"ok"}',
    '{"head":{"id":"x","kind":"chain"},"status":"ok"}',
    '{"head":{"id":"x","kind":"chain"},"message":"ok"}',
])
def test_head_reads_head_and_reason_and_no_neighbour(checker, reply):
    checker.reply("head", (reply, 0))
    with pytest.raises(presence.Unavailable):
        presence.head(ORG)
