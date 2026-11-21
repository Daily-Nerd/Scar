"""One definition of the act line (#439).

promotion_act and promotion_receipt are written by a machine, always at column
0. The parser and signedpromote.with_act decide which line is one through the
same definition in model.py, so what lint and the write gate judge is exactly
what the edit replaces, and a line of someone's data is never deleted.
"""
import pytest

from scar.model import ACT_LINE_RE, parse_scar_text
from scar.signedpromote import with_act

NOTES_BLOCK = (
    "---\ntype: fence\ntitle: T\nnotes: |\n  promotion_act: keepme\n  other\nid: 1\n"
    "anchors:\n  - path: x\n---\nbody\n")


def _fm(*lines, eol="\n", body="body"):
    return "---" + eol + eol.join(lines) + eol + "---" + eol + body + eol


BASE = ("type: fence", "title: T", "id: 1")
ANCHORS = ("anchors:", "  - path: x")

ROWS = {
    "column-0 act": _fm(*BASE, "promotion_act: A.B.C", "promotion_receipt: R1", *ANCHORS),
    "indented act only": _fm(*BASE, "  promotion_act: DECOY", *ANCHORS),
    "indented decoy before the real act": _fm(
        *BASE, "  promotion_act: DECOY", "promotion_act: REAL",
        "promotion_receipt: R1", *ANCHORS),
    "duplicate column-0 acts": _fm(
        *BASE, "promotion_act: FIRST", "promotion_receipt: R1",
        "promotion_act: SECOND", *ANCHORS),
    "crlf file": _fm(*BASE, "promotion_act: A.B.C", "promotion_receipt: R1",
                     *ANCHORS, eol="\r\n"),
    "act only in the body": _fm(*BASE, *ANCHORS, body="promotion_act: BODY"),
    "indented receipt": _fm(*BASE, "\tpromotion_receipt: DECOY", *ANCHORS),
}


def _is_act_line(line):
    return line.startswith(("promotion_act:", "promotion_receipt:"))


def _frontmatter_lines(text):
    return text.split("---", 2)[1].replace("\r", "").split("\n")


def test_with_act_leaves_an_indented_act_like_line_alone():
    out = with_act(NOTES_BLOCK, "A.B.C", "RCPT")
    assert out == NOTES_BLOCK.replace(
        "anchors:", "promotion_act: A.B.C\npromotion_receipt: RCPT\nanchors:")
    assert "notes: |\n  promotion_act: keepme\n  other\n" in out
    parsed = parse_scar_text(out)
    assert (parsed.promotion_act, parsed.promotion_receipt) == ("A.B.C", "RCPT")


@pytest.mark.parametrize("name", ROWS)
def test_parser_and_with_act_agree_on_which_line_is_the_act(name):
    text = ROWS[name]
    out = with_act(text, "NEWACT", "NEWRCPT")
    before, after = _frontmatter_lines(text), _frontmatter_lines(out)
    replaced = [line for line in before if _is_act_line(line)]
    # Exactly the column-0 act and receipt lines went, and every other line stayed
    # in its place and order.
    assert [line for line in before if not _is_act_line(line)] == [
        line for line in after if not _is_act_line(line)]
    assert [line for line in after if _is_act_line(line)] == [
        "promotion_act: NEWACT", "promotion_receipt: NEWRCPT"]
    # What the parser read before the edit came from a line the edit replaced.
    read = parse_scar_text(text)
    for value, key in ((read.promotion_act, "promotion_act"),
                       (read.promotion_receipt, "promotion_receipt")):
        if value:
            assert f"{key}: {value}" in replaced
    # And the edit gave the parser exactly what it was given.
    again = parse_scar_text(out)
    assert (again.promotion_act, again.promotion_receipt) == ("NEWACT", "NEWRCPT")


@pytest.mark.parametrize("name", ROWS)
def test_with_act_is_idempotent_and_round_trips(name):
    once = with_act(ROWS[name], "A.B.C", "RCPT")
    assert with_act(once, "A.B.C", "RCPT") == once
    parsed = parse_scar_text(once)
    assert (parsed.promotion_act, parsed.promotion_receipt) == ("A.B.C", "RCPT")


def test_indented_decoy_act_is_not_read_as_the_act():
    parsed = parse_scar_text(ROWS["indented decoy before the real act"])
    assert (parsed.promotion_act, parsed.promotion_receipt) == ("REAL", "R1")


@pytest.mark.parametrize("name", ["indented act only", "indented receipt",
                                  "act only in the body"])
def test_an_act_that_is_not_at_column_zero_is_no_act(name):
    parsed = parse_scar_text(ROWS[name])
    assert (parsed.promotion_act, parsed.promotion_receipt) == ("", "")


def test_the_first_column_zero_act_is_the_one_read():
    parsed = parse_scar_text(ROWS["duplicate column-0 acts"])
    assert parsed.promotion_act == "FIRST"


def test_act_values_keep_the_quote_and_comment_handling_every_scalar_has():
    text = _fm(*BASE, 'promotion_act: "A.B.C"', "promotion_receipt: R1  # note", *ANCHORS)
    parsed = parse_scar_text(text)
    assert (parsed.promotion_act, parsed.promotion_receipt) == ("A.B.C", "R1")


def test_an_empty_act_value_does_not_borrow_the_next_line():
    text = _fm(*BASE, "promotion_act:", "promotion_receipt: R1", *ANCHORS)
    parsed = parse_scar_text(text)
    assert (parsed.promotion_act, parsed.promotion_receipt) == ("", "R1")


def test_the_definition_is_one_column_0_pattern_for_the_two_keys():
    assert ACT_LINE_RE.match("promotion_act: x") and ACT_LINE_RE.match("promotion_receipt: x")
    assert not ACT_LINE_RE.match("  promotion_act: x")
    assert not ACT_LINE_RE.match("promotion_acts: x")
    assert not ACT_LINE_RE.match("promotion_by: x")
