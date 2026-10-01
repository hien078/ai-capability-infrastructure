"""The OpenCode plugin's ENV contract (§62; commit eade43e, night review residual).

The plugin is TypeScript inside OpenCode (ADR-005) and cannot run under
pytest — the same is true of its environment surface as of its HTTP surface
(``test_opencode_plugin_contract.py``). What pytest CAN pin is the source:
the exact env names the plugin reads, their defaults when unset, and the
opt-out semantics. If someone renames an env var, changes a default, or
breaks the per-prompt opt-out read, this fails before any user's plugin
silently stops routing (or stops opting out).
"""

import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2] / "src/aci/adapters/inbound/opencode/plugin/index.ts"


def plugin_source() -> str:
    return PLUGIN.read_text(encoding="utf-8")


def test_defaults_block_names_and_defaults() -> None:
    """ACI_ROUTER_BASE_URL / ACI_ROUTER_PRINCIPAL with the documented
    fallbacks; the non-env defaults stay the shipped values."""
    src = plugin_source()
    assert re.search(
        r"baseUrl:\s*process\.env\.ACI_ROUTER_BASE_URL\s*\|\|\s*"
        r'"http://127\.0\.0\.1:8000"',
        src,
    ), "ACI_ROUTER_BASE_URL default must stay http://127.0.0.1:8000"
    assert re.search(
        r"principalId:\s*process\.env\.ACI_ROUTER_PRINCIPAL\s*\|\|\s*" r'"opencode"', src
    ), "ACI_ROUTER_PRINCIPAL default must stay opencode"
    assert re.search(r"maxItems:\s*5\b", src), "maxItems default must stay 5"
    assert re.search(r"timeoutMs:\s*2000\b", src), "timeoutMs default must stay 2000"
    assert re.search(r"failClosed:\s*false\b", src), "failClosed default must stay false"


def test_auth_token_is_bearer_and_empty_when_unset() -> None:
    """ACI_API_TOKEN rides as the Bearer header; unset = NO auth header
    (unauthenticated mode), never a fabricated token."""
    src = plugin_source()
    assert re.search(
        r"const token = process\.env\.ACI_API_TOKEN\s*\n?\s*"
        r"return token \? \{ authorization: `Bearer \$\{token\}` \} : \{\}",
        src,
    ), "authHeaders must read ACI_API_TOKEN and send Bearer, or nothing"


def test_disabled_optout_is_exact_string_one() -> None:
    """ACI_ROUTER_DISABLED=1 is an EXACT string compare — not truthiness:
    DISABLED=0 or DISABLED=false must NOT skip routing."""
    src = plugin_source()
    assert re.search(r'process\.env\.ACI_ROUTER_DISABLED === "1"', src), (
        "ACI_ROUTER_DISABLED must stay an exact === '1' compare"
    )


def test_skip_paths_are_colon_separated_substrings_of_pwd() -> None:
    """ACI_ROUTER_SKIP_PATHS: ':'-separated, empties filtered, SUBSTRING
    match against $PWD (falling back to cwd) — the documented shape for
    worker worktrees that must not be routed."""
    src = plugin_source()
    assert re.search(r"const pwd = process\.env\.PWD \?\? process\.cwd\(\)", src)
    assert re.search(
        r'\(process\.env\.ACI_ROUTER_SKIP_PATHS \?\? ""\)\.split\(":"\)\.filter\(\(p\) => p\)',
        src,
    ), "SKIP_PATHS must default to '', split on ':', and drop empty segments"
    assert re.search(r"skip\.some\(\(p\) => pwd\.includes\(p\)\)", src), (
        "the skip match is substring-in-PWD (not equality, not prefix)"
    )


def test_optout_is_read_per_prompt_not_at_module_load() -> None:
    """routingSkipped() is called INSIDE the prompt hook — every prompt
    re-reads the env, so toggling ACI_ROUTER_DISABLED affects the next
    prompt without reloading the plugin (and a module-load read could not
    see per-session PWD at all)."""
    src = plugin_source()
    hook = re.search(
        r'ctx\.session\.hook\("prompt",\s*async \(event\) => \{\s*\n(.*?)\n\s*\}',
        src,
        re.DOTALL,
    )
    assert hook, "the prompt hook must exist"
    assert "routingSkipped()" in hook.group(1), (
        "routingSkipped() must be called inside the prompt hook (per prompt)"
    )
    # and NOT at module scope: every CALL site lives inside the hook body
    # (the ``function routingSkipped`` DEFINITION is excluded by lookbehind).
    calls = [m.start() for m in re.finditer(r"(?<!function )routingSkipped\(\)", src)]
    assert calls and all(call_offset > hook.start() for call_offset in calls), (
        "routingSkipped() must not run at module load"
    )


def test_no_undocumented_aci_router_env_reads() -> None:
    """The plugin's ACI_ROUTER_* surface is exactly the documented four:
    BASE_URL, PRINCIPAL, DISABLED, SKIP_PATHS (plus ACI_API_TOKEN for
    auth). A stray new read would be an undocumented contract change."""
    src = plugin_source()
    names = sorted(set(re.findall(r"process\.env\.(ACI_ROUTER_[A-Z_]+)", src)))
    assert names == [
        "ACI_ROUTER_BASE_URL",
        "ACI_ROUTER_DISABLED",
        "ACI_ROUTER_PRINCIPAL",
        "ACI_ROUTER_SKIP_PATHS",
    ], f"ACI_ROUTER_* surface drifted: {names}"
    other = sorted(set(re.findall(r"process\.env\.(ACI_[A-Z_]+)", src)) - set(names))
    assert other == ["ACI_API_TOKEN"], f"unexpected ACI_* env reads: {other}"
