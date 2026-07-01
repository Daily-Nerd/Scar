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


def test_symbol_anchor_is_resolution_pending_warning():
    from scar.lint import lint_text
    findings = lint_text(
        "---\ntype: deadend\ntitle: t\nseverity: medium\nconfidence: 0.5\n"
        "anchors:\n  - path: payments/retry.py\n  - symbol: Foo\nstatus: active\n---\nbody\n")
    assert any(f.level == "warning" and "resolution pending" in f.message for f in findings)
    assert not any("unsupported" in f.message for f in findings)


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
