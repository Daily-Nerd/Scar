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
    text = (ROOT / "website" / "docs" / "agents.md").read_text(encoding="utf-8")
    assert "## Codex (native plugin hooks)" in text
    assert "hooks/hooks.json" in text
    assert "Bash" in text and "apply_patch" in text
    assert "/hooks" in text
