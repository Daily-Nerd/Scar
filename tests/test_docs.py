from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_agents_md_documents_authoring_skill_access():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "scar agent skill" in text
    assert "scar-authoring" in text


def test_readme_documents_plugin_and_cli_install():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "scar skill install" in text
    assert "Codex users" in text
    assert "/hooks" in text


def test_agents_doc_documents_the_cascade_hook_install():
    """Push injection is no longer Claude Code-only — the runtime claims in
    the docs have to say so, or adopters keep wiring MCP-only."""
    text = (ROOT / "website" / "docs" / "agents.md").read_text(encoding="utf-8")
    assert "scar hook install --runtime windsurf" in text
    assert "Restricted Mode" in text


def test_agents_doc_documents_native_codex_hooks_and_trust():
    """#246: the plugin's hooks.json is not materialized into every Codex
    install, so the docs must lead with the installer, not the plugin. And
    Codex skips untrusted hooks SILENTLY — a doc that omits that ships users
    an install they cannot tell is dead."""
    text = (ROOT / "website" / "docs" / "agents.md").read_text(encoding="utf-8")
    assert "## Codex (native hooks)" in text
    assert "scar hook install --runtime codex" in text
    assert "~/.codex/hooks.json" in text
    assert "Bash" in text and "apply_patch" in text
    assert "/hooks" in text
    assert "silently" in text


def test_agents_doc_documents_review_queue_recipe():
    """#354: the rolling review issue is a documented recipe over
    `scar lint --review-queue`, not a subcommand."""
    text = (ROOT / "website" / "docs" / "agents.md").read_text(encoding="utf-8")
    assert "### Review queue" in text
    assert "scar lint --review-queue" in text
    assert "scar-review-queue" in text
    assert "issues: write" in text
    assert "fetch-depth: 0" in text
    # #359: the sync must not be skipped by lint's exit code, and failures of
    # the gh calls must not be swallowed.
    recipe = text.split("### Review queue")[1].split("## Machine-readable")[0]
    assert "set +e" in recipe and "status=$?" in recipe
    assert "2>/dev/null" not in recipe and "|| true" not in recipe
    assert "lint errors" in recipe
    assert "\u2014" not in text.split("### Review queue")[1].split("## Machine-readable")[0]
