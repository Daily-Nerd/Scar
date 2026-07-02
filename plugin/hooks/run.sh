#!/bin/sh
# Plugin hook wrapper (#113): resolve the scar binary, then dispatch the hook.
#
# plugin.json cannot know where scar-cli was installed; a bare `scar` in a
# hook command silently no-ops forever when the plugin runtime's PATH misses
# it (hooks fail open by design, so nobody is ever told). Resolution order
# mirrors scar.agent._PLUGIN_CANDIDATE_DIRS — keep the two lists in sync.
kind="$1"

scar_bin=$(command -v scar 2>/dev/null)
if [ -z "$scar_bin" ]; then
    for cand in "$HOME/.local/bin/scar" \
                "$HOME/.pipx/venvs/scar-cli/bin/scar" \
                "$HOME/.local/pipx/venvs/scar-cli/bin/scar" \
                "$HOME/.local/share/uv/tools/scar-cli/bin/scar"; do
        if [ -x "$cand" ]; then
            scar_bin="$cand"
            break
        fi
    done
fi

if [ -n "$scar_bin" ]; then
    # 2>/dev/null: a stale binary (old install, plugin.json passing a hook
    # kind its argparse predates, e.g. "posttool") must not spray "invalid
    # choice" on every single edit — hook handlers are contractually always
    # exit 0 regardless of outcome, so this is pure noise, not signal. Not
    # `exec`: a plain call lets us force the exit code below rather than
    # trust the (possibly stale, possibly non-zero-on-argparse-error) binary.
    "$scar_bin" hook "$kind" 2>/dev/null
    exit 0
fi

# Unresolvable. precheck/stop-drafter fail open silently (hot path — never
# break or delay an edit). session-notice is the one visibility point: tell
# the user once per session instead of no-oping in the dark.
if [ "$kind" = "session-notice" ]; then
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"SCAR: plugin is installed but the scar-cli binary was not found on PATH or in known install locations — plugin hooks are inactive. Install with: uv tool install scar-cli"}}'
fi
exit 0
