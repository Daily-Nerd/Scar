"""presence.py owns the fixed paths and the words (consumer, action, method).
Slice 3 of the presence proof (#392)."""
import pytest

from scar import presence


def test_the_fixed_paths_are_absolute_administrator_paths():
    assert presence.SIGNER == "/Library/Application Support/handsal/bin/handsal-sign"
    assert presence.CHECKER == "/Library/Application Support/handsal/bin/handsal-check"


def test_the_consumer_id_is_pinned_because_renaming_breaks_every_stored_act():
    # Signed domain separation: a rename makes every act already stored
    # consumer_mismatch on recheck-act. This value must never change.
    assert presence.CONSUMER == "scar"


def test_the_words_of_a_promotion_decision():
    assert presence.ACTION == "promote"
    assert presence.METHOD == "local:handsal.decision"


def test_the_validity_window_is_a_decimal_string_constant():
    # SG-04: expires_in_s is a decimal string, never a JSON number, and Scar
    # keeps no copy of the org's policy.
    assert presence.EXPIRES_IN_S == "3600"
    assert isinstance(presence.EXPIRES_IN_S, str)



def _jws(payload_bytes):
    import base64
    return "h." + base64.urlsafe_b64encode(payload_bytes).decode().rstrip("=") + ".s"


def test_act_inputs_hash_reads_the_payload_without_verifying_anything():
    assert presence.act_inputs_hash(_jws(b'{"inputs_hash": "uEiabc", "x": 1}')) == "uEiabc"


@pytest.mark.parametrize("bad", [
    "", "a.b", "a.b.c.d", "h.!!.s", _jws(b"nope"), _jws(b"[]"), _jws(b"{}"),
    _jws(b'{"inputs_hash": 5}'), _jws(b'{"inputs_hash": ""}'), _jws(b"\xff\xfe"),
])
def test_act_inputs_hash_raises_on_anything_malformed(bad):
    with pytest.raises(presence.MalformedAct):
        presence.act_inputs_hash(bad)
