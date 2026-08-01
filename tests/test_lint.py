"""Lint rules: every rule exists because something already went wrong once."""

from scar.lint import lint_text

GOOD = """\
---
type: fence
title: The 7s sleep is intentional
severity: high
confidence: 0.9
created: 2026-06-09
authors: [mara]
anchors:
  - path: payments/retry.py
evidence:
  - commit: abc1234
status: active
---

Why it must stay.
"""


def test_clean_scar_yields_no_errors():
    assert lint_text(GOOD) == []


def test_missing_frontmatter_is_fatal():
    findings = lint_text("# daimon 0004 case\nplain markdown, never fires\n")
    assert any(f.level == "error" and "frontmatter" in f.message for f in findings)


def test_unknown_type_is_error():
    findings = lint_text(GOOD.replace("type: fence", "type: wart"))
    assert any(f.level == "error" and "type" in f.message for f in findings)


def test_missing_title_is_error():
    findings = lint_text(GOOD.replace("title: The 7s sleep is intentional\n", ""))
    assert any(f.level == "error" and "title" in f.message for f in findings)


def test_no_anchors_is_error():
    bad = GOOD.replace("anchors:\n  - path: payments/retry.py\n", "")
    findings = lint_text(bad)
    assert any(f.level == "error" and "anchor" in f.message for f in findings)


def test_no_evidence_is_warning_not_error():
    bad = GOOD.replace("evidence:\n  - commit: abc1234\n", "")
    findings = lint_text(bad)
    assert any(f.level == "warning" and "evidence" in f.message for f in findings)
    assert not any(f.level == "error" for f in findings)


def test_markdown_link_in_title_is_warning():
    # Candidates are committable, reviewer-facing files; markdown link syntax
    # in a title is harvester leakage, not prose (#159 — a downstream
    # harvester shipped `title: "**PR [#172](https://...)** ..."` verbatim).
    bad = GOOD.replace(
        "title: The 7s sleep is intentional",
        'title: "**PR [#172](https://github.com/o/r/pull/172)** broke boot"')
    findings = lint_text(bad)
    assert any(f.level == "warning" and "title" in f.message for f in findings)


def test_bare_url_in_title_is_warning():
    bad = GOOD.replace("title: The 7s sleep is intentional",
                       "title: see https://example.com/why before touching")
    findings = lint_text(bad)
    assert any(f.level == "warning" and "title" in f.message for f in findings)


def test_invalid_severity_is_error():
    findings = lint_text(GOOD.replace("severity: high", "severity: extreme"))
    assert any(f.level == "error" and "severity" in f.message for f in findings)


def test_invalid_status_is_error():
    findings = lint_text(GOOD.replace("status: active", "status: maybe"))
    assert any(f.level == "error" and "status" in f.message for f in findings)


def test_bad_pattern_regex_is_error():
    bad = GOOD.replace("  - path: payments/retry.py",
                       '  - pattern: "([unclosed"')
    findings = lint_text(bad)
    assert any(f.level == "error" and "pattern" in f.message for f in findings)


def test_nested_quantifier_pattern_is_error():
    # A valid-but-pathological anchor: re.compile accepts (a+)+$ but search()
    # backtracks catastrophically on adversarial input. Lint must reject it at
    # the gate so it can never be promoted to an active (firing) scar.
    bad = GOOD.replace("  - path: payments/retry.py",
                       '  - pattern: "(a+)+$"')
    findings = lint_text(bad)
    assert any(f.level == "error" and "pattern" in f.message for f in findings)


def test_normal_pattern_anchors_not_flagged_as_redos():
    # Ordinary anchors must NOT false-positive: an escaped literal paren
    # (redis\.get\() is not a group, and an alternation has no nested quantifier.
    ok = GOOD.replace(
        "  - path: payments/retry.py",
        '  - pattern: "redis\\.get\\("\n  - pattern: "TODO|FIXME"')
    findings = lint_text(ok)
    assert not any(f.level == "error" for f in findings)


def test_malformed_url_evidence_is_warning():
    bad = GOOD.replace(
        "evidence:\n  - commit: abc1234\n",
        "evidence:\n  - url: not-a-link\n",
    )
    findings = lint_text(bad)
    assert any(f.level == "warning" and "url" in f.message and "not-a-link" in f.message
               for f in findings)
    assert not any(f.level == "error" for f in findings)


def test_valid_url_evidence_no_warning():
    ok = GOOD.replace(
        "evidence:\n  - commit: abc1234\n",
        "evidence:\n  - url: https://github.com/org/repo/commit/abc1234\n",
    )
    findings = lint_text(ok)
    assert not any("url" in f.message for f in findings)


def test_issue_evidence_skips_url_check():
    ok = GOOD.replace(
        "evidence:\n  - commit: abc1234\n",
        "evidence:\n  - issue: 50\n",
    )
    findings = lint_text(ok)
    assert findings == []


def test_symbol_anchor_warns_to_install_extra_when_absent(monkeypatch):
    from scar import lint, symbols
    monkeypatch.setattr(symbols, "symbols_available", lambda: False)
    findings = lint.lint_text(
        "---\ntype: deadend\ntitle: t\nseverity: medium\nconfidence: 0.5\n"
        "anchors:\n  - path: p.py\n  - symbol: Foo\nstatus: active\n---\nbody\n")
    assert any(f.level == "warning" and "symbols" in f.message and "extra" in f.message
               for f in findings)


def test_symbol_anchor_no_warning_when_extra_present(monkeypatch):
    from scar import lint, symbols
    monkeypatch.setattr(symbols, "symbols_available", lambda: True)
    findings = lint.lint_text(
        "---\ntype: deadend\ntitle: t\nseverity: medium\nconfidence: 0.5\n"
        "anchors:\n  - path: p.py\n  - symbol: Foo\nstatus: active\n---\nbody\n")
    assert not any("symbol" in f.message for f in findings)


def test_symbol_only_scar_is_not_anchorless():
    from scar.lint import lint_text
    findings = lint_text(
        "---\ntype: deadend\ntitle: t\nseverity: medium\nconfidence: 0.5\n"
        "anchors:\n  - symbol: Foo\nstatus: active\n---\nbody\n")
    assert not any("protects nothing" in f.message for f in findings)


def test_unsupported_touch_and_breaks_anchors_are_warnings():
    bad = GOOD.replace(
        "  - path: payments/retry.py",
        "  - path: payments/retry.py\n  - touch: a.py\n  - breaks: b.py")
    findings = lint_text(bad)
    assert any(f.level == "warning" and "touch" in f.message for f in findings)
    assert any(f.level == "warning" and "breaks" in f.message for f in findings)
    assert not any(f.level == "error" for f in findings)


def test_supported_anchors_yield_no_unsupported_warning():
    # A normal path/pattern scar must not trip the unsupported-anchor warning.
    ok = GOOD.replace(
        "  - path: payments/retry.py",
        '  - path: payments/retry.py\n  - pattern: "redis"')
    findings = lint_text(ok)
    assert not any(
        "unsupported" in f.message or "ignored" in f.message for f in findings)


def test_bad_violation_regex_is_error():
    bad = GOOD.replace("status: active", 'violation: "[unclosed"\nstatus: active')
    findings = lint_text(bad)
    assert any(f.level == "error" and "violation" in f.message for f in findings)


def test_nested_quantifier_violation_is_error():
    # A valid-but-pathological violation: re.compile accepts (a+)+$ but search()
    # backtracks catastrophically on adversarial input. Lint must reject it at
    # the gate so it can never cause ReDoS on a code read.
    bad = GOOD.replace("status: active", 'violation: "(a+)+$"\nstatus: active')
    findings = lint_text(bad)
    assert any(f.level == "error" and "violation" in f.message for f in findings)


def test_valid_violation_not_flagged():
    ok = GOOD.replace("status: active", 'violation: "foo"\nstatus: active')
    findings = lint_text(ok)
    assert not any("violation" in f.message for f in findings)


def test_absent_violation_silent():
    # When violation is not specified, no violation-related messages.
    findings = lint_text(GOOD)
    assert not any("violation" in f.message for f in findings)


# --- command anchors (#175) ---

def _command_scar(regex: str) -> str:
    return f"""---
type: deadend
title: command trap
severity: high
confidence: 0.9
created: 2026-07-30
authors: ["kib"]
anchors:
  - command: "{regex}"
evidence:
  - issue: 175
status: active
---

body
"""


def test_command_only_scar_counts_as_anchored():
    findings = lint_text(_command_scar("uv sync"))
    assert not any("no anchors" in f.message for f in findings)


def test_invalid_command_regex_is_an_error():
    findings = lint_text(_command_scar("uv sync ("))
    assert any(f.level == "error" and "command" in f.message for f in findings)


def test_empty_matching_command_regex_is_an_error():
    findings = lint_text(_command_scar(".*"))
    assert any(f.level == "error" and "empty" in f.message for f in findings)


def test_redos_command_regex_is_an_error():
    findings = lint_text(_command_scar("(a+)+b"))
    assert any(f.level == "error" and "command" in f.message
               and "backtracking" in f.message for f in findings)


# --- author identity drift (#182) ---

def _authors_scar(authors: str) -> str:
    return f"""---
type: deadend
title: authors drift probe
severity: medium
confidence: 0.7
created: 2026-07-31
authors: [{authors}]
anchors:
  - path: src/
evidence:
  - issue: 182
status: active
---

body
"""


def test_lint_warns_on_case_only_author_drift_within_one_scar():
    findings = lint_text(_authors_scar('"kibukx", "Kibukx"'))
    assert any(f.level == "warning" and "author" in f.message.lower()
               and "case" in f.message.lower() for f in findings)


def test_lint_silent_on_distinct_authors():
    findings = lint_text(_authors_scar('"claude-code", "kibukx"'))
    assert not any("author" in f.message.lower() and "case" in f.message.lower()
                   for f in findings)


# --- ReDoS gate: nested-group and alternation-overlap evasions (#184) ---

def test_redos_gate_catches_nested_group_at_depth():
    from scar.lint import _is_redos_prone
    assert _is_redos_prone("((a+))+$")
    assert _is_redos_prone("(x(y+)z)*")
    assert _is_redos_prone("(?:(a*))+")


def test_redos_gate_catches_overlapping_alternation():
    from scar.lint import _is_redos_prone
    assert _is_redos_prone("(a|a)*$")
    assert _is_redos_prone("(x|xy)*$")
    assert _is_redos_prone("(?:foo|foobar)+")


def test_redos_gate_still_accepts_ordinary_anchors():
    from scar.lint import _is_redos_prone
    for benign in (r"redis\.get\(", r"TODO|FIXME", r"(abc)+", r"(a|b)*",
                   r"uv sync(?!.* --all-extras)", r"except \((KeyError|TypeError)[^)]*\)",
                   r"[+*]+", r"\(a\+\)\+"):
        assert not _is_redos_prone(benign), benign


def test_redos_gate_still_catches_original_form():
    from scar.lint import _is_redos_prone
    assert _is_redos_prone("(a+)+$")
    assert _is_redos_prone("([a-z]+)*")
