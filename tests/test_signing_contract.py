"""The signed-promotion contract (SPEC section 10).

The code is the source of truth. SPEC section 10 states the rules that decide
whether a signed promotion still verifies later, and this file fails the build
when the spec and the code drift apart. Checks are derived from the code where
they can be: constants are imported, the worked example is rebuilt, lint states
come from the real lint function, the command list comes from the real parser.

Two things are pinned here and not read from the spec:

- METHOD below. The spec calls it a fixed string in presence.py and does not
  print it.
- The wrap width, which is private in record.py. It is measured through the
  public render_fit instead of imported.

The gate rule and the size boundaries are tested in tests/test_write_gate.py and
tests/test_render_fit.py. Here only the constants and the outcomes the spec
states are checked against the code.
"""
from __future__ import annotations

import ast
import base64
import json
import re
import subprocess
from pathlib import Path

import pytest

from scar import config, presence, record, verify
from scar.cli import build_parser
from scar.lint import downgrade_findings
from scar.model import Scar, parse_scar_text
from scar.record import (MAX_RECORD_BYTES, MAX_RECORD_LINES, promoted_record,
                         promotion_subject, record_hash, render_fit)
from scar.signedpromote import SignedPromoteError, check_signable_status
from scar.store import LIFECYCLE_TRANSITIONS

ROOT = Path(__file__).resolve().parent.parent
SPEC = (ROOT / "SPEC.md").read_text(encoding="utf-8")
AGENTS_DOC = (ROOT / "website" / "docs" / "agents.md").read_text(encoding="utf-8")

# The same literal is in src/scar/presence.py. The spec does not print it.
METHOD = "local:handsal.decision"

EM_DASH = "\u2014"


# --- reading the spec --------------------------------------------------------

def _top() -> str:
    """The whole of section 10, up to the next top-level section or the end."""
    head = "## 10. Signed promotion"
    assert head in SPEC, "SPEC.md has no section 10"
    return SPEC.split(head, 1)[1].split("\n## ", 1)[0]


def _section(title: str) -> str:
    """The body of the `### 10.N <title>` subsection."""
    m = re.search(rf"^### 10\.\d+ {re.escape(title)}\n(.*?)(?=^### |\Z)", _top(),
                  re.MULTILINE | re.DOTALL)
    assert m, f"SPEC section 10 has no subsection titled {title!r}"
    return m.group(1)


def _raw_table(section: str) -> list[list[str]]:
    """Data rows of the first markdown table in `section`, cells stripped."""
    rows = []
    for line in section.splitlines():
        if not line.startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            continue
        rows.append(cells)
    return rows[1:]  # drop the header


def _table(section: str) -> list[list[str]]:
    """Like _raw_table, with the backticks removed from a cell that is one
    code span."""
    return [[c[1:-1] if re.fullmatch(r"`[^`]*`", c) else c for c in row]
            for row in _raw_table(section)]


def _ticks(cell: str) -> list[str]:
    return re.findall(r"`([^`]+)`", cell)


# --- 1. constants ------------------------------------------------------------

PATH_ROW = "fixed absolute path in presence.py"
METHOD_ROW = "fixed string in presence.py"


def _public_constants(module) -> dict[str, object]:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names = [t.id for node in tree.body if isinstance(node, ast.Assign)
             for t in node.targets
             if isinstance(t, ast.Name) and t.id.isupper() and not t.id.startswith("_")]
    return {n: getattr(module, n) for n in names}


def _wrap_width_in_code() -> int:
    """The width at which a long ASCII line wraps, measured through render_fit:
    the smallest body length whose record needs one more block line."""
    def lines(n: int) -> int:
        return render_fit(Scar(id=1, title="t", body="a" * n)).lines
    base = lines(1)
    n = 1
    while lines(n) == base:
        n += 1
    return n - 1


def test_constants_table_matches_code():
    rows = {r[0]: r[1] for r in _table(_section("The act's words"))}
    expected: dict[str, str] = {
        "MAX_RECORD_BYTES": str(MAX_RECORD_BYTES),
        "MAX_RECORD_LINES": str(MAX_RECORD_LINES),
        "WRAP_WIDTH": str(_wrap_width_in_code()),
        "CONSUMER": presence.CONSUMER,
        "ACTION": presence.ACTION,
        "EXPIRES_IN_S": presence.EXPIRES_IN_S,
        "CHECKER_TIMEOUT_S": str(presence.CHECKER_TIMEOUT_S),
        "SIGNER_TIMEOUT_S": str(presence.SIGNER_TIMEOUT_S),
        "CONFIG_NAME": config.CONFIG_NAME,
        "SIGNER": PATH_ROW,
        "CHECKER": PATH_ROW,
        "METHOD": METHOD_ROW,
    }
    assert rows == expected


def test_every_public_constant_has_a_row():
    rows = {r[0] for r in _table(_section("The act's words"))}
    public = set(_public_constants(record)) | set(_public_constants(presence))
    assert public <= rows, f"constants with no row in the spec: {sorted(public - rows)}"


def test_method_is_pinned_here_and_never_printed_in_the_spec():
    assert presence.METHOD == METHOD
    assert METHOD not in SPEC
    assert METHOD not in AGENTS_DOC
    for path in (presence.SIGNER, presence.CHECKER):
        assert path not in _top(), "the spec must not print the signer's install path"


# --- 2. worked example -------------------------------------------------------

EXAMPLE_FILE = (
    "---\r\n"
    "id: 7\r\n"
    "type: fence\r\n"
    "severity: high\r\n"
    'title: "Retry loop is deliberate"\r\n'
    "confidence: 0.9\r\n"
    'authors: ["claude-code"]\r\n'
    "promoted_by: reviewer\r\n"
    "promoted_from: candidates/retry-loop.md\r\n"
    "anchors:\r\n"
    '  - command: "git push --force"\r\n'
    "  - symbol: retry_with_backoff\r\n"
    '  - pattern: "retry\\("\r\n'
    "  - path: src/net.py\r\n"
    "  - path: src/util.py\r\n"
    "evidence:\r\n"
    "  - note: left out of the record\r\n"
    "status: active\r\n"
    "promotion_act: left.out.of-the-record\r\n"
    "---\r\n"
    "\r\n"
    "The backoff is slow on purpose.\r\n"
    "Do not remove it.\r\n"
    "\r\n"
)


def test_worked_example_is_the_record_byte_for_byte():
    section = _section("What is signed")
    block = re.search(r"```text\n(.*?)```", section, re.DOTALL)
    assert block, "the section needs a fenced `text` block with the record"
    scar = parse_scar_text(EXAMPLE_FILE)
    assert block.group(1).encode("utf-8") == promoted_record(scar)
    printed = re.search(r"^Hash of the block above: `(u[A-Za-z0-9_-]+)`$", section, re.MULTILINE)
    assert printed, "the section needs the hash line under the block"
    assert printed.group(1) == record_hash(scar)


def test_record_ignores_everything_the_spec_says_it_ignores():
    """Status, evidence, authors, promoted_by, confidence, the act and the
    receipt are not in the record (the example file carries all of them)."""
    base = parse_scar_text(EXAMPLE_FILE)
    other = parse_scar_text(EXAMPLE_FILE)
    other.status, other.evidence, other.authors = "archived", ["note: x"], ["someone"]
    other.promoted_by, other.confidence = "another", 0.1
    other.promotion_act, other.promotion_receipt = "a.b.c", "uEiOther"
    assert promoted_record(base) == promoted_record(other)


# --- 3. subject --------------------------------------------------------------

def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


def test_subject_matches_the_stated_pattern(tmp_path):
    pattern = re.search(r"^Pattern: `([^`]+)`$", _section("The subject"), re.MULTILINE)
    assert pattern, "the subject section needs a `Pattern:` line"
    _git(tmp_path, "init", "-q")
    assert promotion_subject(tmp_path, 12) is None  # no commits, no root
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "first")
    subject = promotion_subject(tmp_path, 12)
    assert subject is not None and re.fullmatch(pattern.group(1), subject)
    root = subprocess.run(["git", "-C", str(tmp_path), "rev-list", "--max-parents=0", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    assert subject == f"12-{root[:16]}"


# --- 4. command table --------------------------------------------------------

def _commands_rows() -> list[list[str]]:
    return _raw_table(_section("What each command does"))


def _subparsers() -> dict:
    parser = build_parser()
    for action in parser._actions:
        if hasattr(action, "choices") and isinstance(action.choices, dict) and "promote" in action.choices:
            return action.choices
    raise AssertionError("no subcommands found")


def test_every_pinned_by_file_exists():
    for row in _commands_rows():
        files = _ticks(row[3])
        assert files, f"row {row[0]!r} names no test file"
        for name in files:
            assert name.startswith("tests/") and (ROOT / name).is_file(), name


def test_every_command_in_the_table_is_registered():
    subs = _subparsers()
    seen = 0
    for row in _commands_rows():
        for token in _ticks(row[0]):
            words = token.split()
            assert words[0] == "scar", token
            assert words[1] in subs, f"{token!r} is not a registered subcommand"
            flags = {o for a in subs[words[1]]._actions for o in a.option_strings}
            for flag in (w for w in words[2:] if w.startswith("-")):
                assert flag in flags, f"{token!r}: {flag} is not an option of {words[1]}"
            seen += 1
    assert seen >= 6


def _enclosing_callers(targets: set[str]) -> set[str]:
    """Names of the functions in src/scar that call any of `targets`."""
    found: set[str] = set()
    for path in (ROOT / "src" / "scar").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(fn):
                if isinstance(call, ast.Call):
                    f = call.func
                    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
                    if name in targets:
                        found.add(fn.name)
    return found


def test_every_caller_of_the_sign_sequence_is_in_the_table():
    """A command that signs and is missing from the table is the drift. The
    callers are listed from the code: whoever calls the one sequence
    (sign_record), or either entry point built on it, or the in-place helper."""
    first = _enclosing_callers({"sign_record"})
    callers = first | _enclosing_callers(first | {"_sign_in_place"})
    named = {t.split(".")[-1] for row in _commands_rows() for t in _ticks(row[2])}
    assert callers <= named, f"signing code the command table does not name: {sorted(callers - named)}"


def test_every_code_name_in_the_table_exists():
    source = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "src" / "scar").glob("*.py"))
    for row in _commands_rows():
        for token in _ticks(row[2]):
            name = token.split(".")[-1]
            assert re.search(rf"\b(def|class) {re.escape(name)}\b", source), token


# --- 5. status table ---------------------------------------------------------

def test_status_table_matches_check_signable_status():
    rows = {r[0]: r[1] for r in _table(_section("Which scars are signed"))}
    assert set(rows) == set(LIFECYCLE_TRANSITIONS)
    for status, answer in rows.items():
        assert answer in ("yes", "no"), (status, answer)
        try:
            check_signable_status(Scar(id=1, status=status))
            accepted = True
        except SignedPromoteError:
            accepted = False
        assert accepted == (answer == "yes"), status
    assert [s for s, a in rows.items() if a == "yes"] == ["active"]


# --- 6. lint states ----------------------------------------------------------

def _act(inputs_hash: str) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"inputs_hash": inputs_hash}).encode()).rstrip(b"=").decode()
    return f"h.{payload}.s"


def _scar(**kw) -> Scar:
    base = dict(id=3, type="fence", title="t", severity="medium", body="b",
                status="active", path_anchors=["src/a.py"])
    base.update(kw)
    return Scar(**base)


def _signed(scar: Scar) -> Scar:
    scar.promotion_act = _act(record_hash(scar))
    return scar


ORG = "org-id"
SIGNED_FROM_1 = config.Config(org=ORG, signed_from=1)

# One fixture per branch of downgrade_findings that appends an error. A new
# branch needs a new entry here and a new row in the spec.
LINT_FIXTURES = {
    "act-without-org": lambda: (config.Config(), [_signed(_scar())], 4),
    "act-unreadable": lambda: (SIGNED_FROM_1, [_scar(promotion_act="not-a-jws")], 4),
    "act-stale": lambda: (SIGNED_FROM_1, [_scar(promotion_act=_act("uOTHER"))], 4),
    "act-missing": lambda: (SIGNED_FROM_1, [_scar()], 4),
    "config-no-signed-from": lambda: (config.Config(org=ORG), [], 4),
    "config-past-next-id": lambda: (config.Config(org=ORG, signed_from=9), [], 4),
}


def _lint_rows() -> dict[str, str]:
    return {r[0]: r[2] for r in _table(_section("Lint states"))}


def test_lint_states_match_the_fixed_list():
    assert set(_lint_rows()) == set(LINT_FIXTURES)


@pytest.mark.parametrize("kind", sorted(LINT_FIXTURES))
def test_each_lint_state_is_one_error_with_the_spec_phrase(kind):
    cfg, scars, next_id = LINT_FIXTURES[kind]()
    found = downgrade_findings([(f"s{i}.md", s) for i, s in enumerate(scars)], cfg, next_id)
    assert len(found) == 1, found
    finding = found[0][1]
    assert finding.level == "error"
    assert _lint_rows()[kind] in finding.message


@pytest.mark.parametrize("kind", ["act-unreadable", "act-stale", "act-missing"])
def test_resign_hint_only_for_an_active_scar(kind):
    cfg, scars, next_id = LINT_FIXTURES[kind]()
    for status, hinted in (("active", True), ("archived", False), ("challenged", False),
                           ("orphaned", False)):
        scars[0].status = status
        (_, finding), = downgrade_findings([("s.md", scars[0])], cfg, next_id)
        assert ("scar resign --id 3" in finding.message) == hinted, (kind, status)
    assert "`scar resign --id N`" in _section("Lint states")


def test_a_matching_act_is_not_a_lint_error():
    assert downgrade_findings([("s.md", _signed(_scar()))], SIGNED_FROM_1, 4) == []


# --- 7. verify states --------------------------------------------------------

def test_verify_states_equal_the_constants():
    states = [r[0] for r in _table(_section("Verify states"))]
    assert states == [
        verify.VERIFIED, verify.UNVERIFIED, "not proven: <reason>", verify.NO_VERIFIER,
        verify.NO_ORG, verify.NO_ROOT, verify.FAILED]


def test_not_proven_prefix_is_the_one_the_code_builds():
    """The prefix in the table is the one `_recheck` puts in front of the
    checker's reason."""
    src = (ROOT / "src" / "scar" / "verify.py").read_text(encoding="utf-8")
    assert 'f"not proven: {' in src


# --- spec text hygiene and cross references ----------------------------------

def test_section_has_no_em_dash():
    assert EM_DASH not in _top()


def test_section_names_the_tests_that_pin_the_gate_and_the_sizes():
    top = _top()
    for name in ("tests/test_write_gate.py", "tests/test_render_fit.py",
                 "tests/test_signing_contract.py"):
        assert name in top
    for name in re.findall(r"`(tests/[a-z_]+\.py)`", top):
        assert (ROOT / name).is_file(), name


def test_json_contract_still_finds_a_single_section_9_4():
    assert len(re.findall(r"^### 9\.4 ", SPEC, re.MULTILINE)) == 1


# --- 8. docs paragraph -------------------------------------------------------

OLD_SENTENCE = ("After `scar reanchor --apply` or a rename fix changes a scar's anchors, "
                "its act no longer matches the record.")


def _signing_paragraphs() -> str:
    return AGENTS_DOC.split("### Signing promotions", 1)[1].split("### Verifying promotions", 1)[0]


def test_agents_doc_no_longer_says_a_rewrite_leaves_the_act_stale():
    assert OLD_SENTENCE not in AGENTS_DOC
    assert "To sign the record again, run `scar resign --id N`: one touch" not in AGENTS_DOC


def test_agents_doc_states_the_in_place_rule():
    text = _signing_paragraphs()
    assert "signs the new record in place" in text
    assert "No flag writes a signed scar unsigned." in text
    assert "If it cannot sign, it refuses and the file is not changed." in text


def test_agents_doc_says_every_scar_file_and_the_refusal():
    text = _signing_paragraphs()
    assert "every scar file, archived ones included" in text
    assert "every scar that parses" not in text
    assert "while a scar file cannot be read" in text


def test_spec_says_signing_refuses_when_a_scar_file_cannot_be_read(tmp_path):
    order = _section("Order of a signature")
    assert "Signing refuses when any scar file cannot be read" in order
    assert "every scar that parses" not in order
    limits = _section("What this section does not claim")
    assert "signing\n  refuses while any scar file cannot be read" in limits
    assert "is skipped" not in limits
    # The code does what the sentence says.
    from scar.store import ScarStore, UnreadableScars, init_scars
    init_scars(tmp_path)
    (tmp_path / ".scars" / "0001-bad.fence.md").write_bytes(b"\xff\xfe")
    with pytest.raises(UnreadableScars):
        ScarStore.discover(tmp_path).seen_receipts()


def test_every_command_in_the_signing_paragraphs_is_in_the_command_table():
    table_names = {t.split()[1] for row in _commands_rows() for t in _ticks(row[0])}
    for token in _ticks(_signing_paragraphs()):
        if token.startswith("scar "):
            assert token.split()[1] in table_names, token


def test_agents_doc_lists_the_verify_states_the_code_has():
    text = AGENTS_DOC.split("### Verifying promotions", 1)[1]
    for state in (verify.VERIFIED, verify.UNVERIFIED, verify.NO_VERIFIER, verify.NO_ORG,
                  verify.NO_ROOT, verify.FAILED):
        assert f"`{state}`" in text, state
