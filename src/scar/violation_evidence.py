"""Bounded, text-free evidence captured from the original regex match."""

from __future__ import annotations

import hashlib
import re


SUMMARY_FORMAT = "summary-v1"
EXCERPT_CHARS = 120


def summarize_match(match: re.Match[str]) -> tuple[str, dict]:
    """Return the display excerpt and its persistable summary, not raw text."""
    text = match.string
    start = match.start()
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", start)
    if line_end == -1:
        line_end = len(text)
    excerpt = text[line_start:line_end][:EXCERPT_CHARS]

    def digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()

    return excerpt, {
        "format": SUMMARY_FORMAT,
        "pattern_sha256": digest(match.re.pattern),
        "match_sha256": digest(match.group()),
        "excerpt_sha256": digest(excerpt),
        "line": text.count("\n", 0, start) + 1,
        "column": start - line_start + 1,
        "match_chars": match.end() - start,
        "excerpt_chars": len(excerpt),
        "excerpt_truncated": line_end - line_start > EXCERPT_CHARS,
    }


def is_violation_summary(value: object) -> bool:
    """Validate persisted evidence without consulting present-day source files."""
    if not isinstance(value, dict) or value.get("format") != SUMMARY_FORMAT:
        return False
    for key in ("pattern_sha256", "match_sha256", "excerpt_sha256"):
        digest = value.get(key)
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            return False
    for key, minimum in (("line", 1), ("column", 1), ("match_chars", 0),
                         ("excerpt_chars", 0)):
        count = value.get(key)
        if type(count) is not int or count < minimum:
            return False
    return (value["excerpt_chars"] <= EXCERPT_CHARS
            and type(value.get("excerpt_truncated")) is bool
            and (not value["excerpt_truncated"]
                 or value["excerpt_chars"] == EXCERPT_CHARS))
