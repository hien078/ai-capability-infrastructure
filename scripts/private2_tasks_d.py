"""Private-knowledge fixture for the E2C skill axis (builder "d").

E2C replicates E2B (ADR-014 amendment 24: the same standard as a plain
repo file passed 9/20, the registry + §14 router with preload 19/20) on
7 NEW, varied fixtures (--set private2, arms F vs Bp). This builder's
fixture: CONFIGURATION PRECEDENCE & KEY NAMING (infra team), assigned
directness INDIRECT — the prompt says only "our internal policy", never
the standard's name or title words.

The required knowledge is a fictional internal standard invented with
this fixture (2026-10-02): the Harbor infra team's config resolution
rules — source precedence, key normalization, env-var prefixing and
secret reporting. They are arbitrary but precise, documented ONLY in the
fixture's private SKILL.md (served through the capability plane, never a
workspace file), and pinned in the test as sha256 DIGESTS over the
resolver's report lines — never plaintext, so the test cannot become a
copy of the standard and a naked model cannot derive the rules from the
test names, messages or the shipped code: the shipped code embodies the
PLAUSIBLE BUT WRONG public convention (env wins, case-insensitive keys,
a CFG_ prefix, substring secret matching), so guessing fails.

The digest output space is LARGE by design (the E2B §1.5 lesson — the
meridian digests were over small violation-code strings): each digest
covers a whole report — 3-13 manifest lines, each combining a
normalization decision, a precedence decision, a free-form string value
and a sealing decision — so brute-forcing a digest means guessing every
line byte-exact.

§34 caveat applies to any round run on this set: small n, author-built
fixtures, one model — directional only.
"""

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _private2_task(
    name: str,
    files: dict[str, str],
    prompt: str,
    skill_id: str,
    skill: str,
    directness: str,
    markers: list[str],
) -> dict[str, Any]:
    """A private2 fixture: workspace files + ticket prompt + the private
    SKILL.md text (NOT a workspace file — the model must never see it on
    disk; the arms serve it through the capability plane only)."""
    return {
        "name": name,
        "files": files,
        "prompt": prompt,
        "skill_id": skill_id,
        "skill": skill,
        "directness": directness,
        "markers": markers,
    }


# --- shipped workspace: sources.py (the WRONG public-practice constants) ----
_SHIPPED_SOURCES_PY = '''"""Config source conventions shared by the resolver and the loaders."""

#: Ascending precedence: later sources win (the environment beats all).
SOURCE_ORDER = ["defaults", "file", "profile", "env"]

#: Only environment variables with this prefix are config.
ENV_PREFIX = "CFG_"

#: A key containing any of these substrings is a secret.
SECRET_MARKERS = ("password", "secret", "token")

#: How a secret value is rendered in the report.
SECRET_RENDER = "***"
'''

# --- shipped workspace: config_resolver.py (the WRONG public practice) -----
_SHIPPED_CONFIG_RESOLVER_PY = '''"""Resolve configuration from defaults, file, profile and env.

The last source in SOURCE_ORDER wins a key; keys are compared
case-insensitively; environment variables are picked up by prefix, with
underscores as separators; keys that look like secrets are masked in
the report.
"""

from sources import ENV_PREFIX, SECRET_MARKERS, SECRET_RENDER, SOURCE_ORDER


def normalize(key):
    """Canonical form of a key: lowercase."""
    return str(key).lower()


def _from_env(env):
    """Map environment variables onto config keys."""
    picked = {}
    for name, value in env.items():
        if not name.startswith(ENV_PREFIX):
            continue
        picked[name[len(ENV_PREFIX) :].lower().replace("_", "-")] = value
    return picked


class ConfigResolver:
    def __init__(self, *, defaults=None, file=None, profile=None, env=None):
        self._sources = {
            "defaults": dict(defaults or {}),
            "file": dict(file or {}),
            "profile": dict(profile or {}),
            "env": _from_env(dict(env or {})),
        }

    def _winners(self):
        """The winning (value, source) per key, in SOURCE_ORDER."""
        winners = {}
        for source in SOURCE_ORDER:
            for key, value in self._sources[source].items():
                winners[normalize(key)] = (value, source)
        return winners

    def resolve(self):
        """The winning value per key."""
        return {key: value for key, (value, _) in self._winners().items()}

    def report(self):
        """One line per key: key=value@source, keys sorted."""
        winners = self._winners()
        lines = []
        for key in sorted(winners):
            value, source = winners[key]
            if any(marker in key for marker in SECRET_MARKERS):
                value = SECRET_RENDER
            lines.append(f"{key}={value}@{source}")
        return lines
'''

# --- shipped workspace: the contract test (digests only, never rules) ------
_SHIPPED_TEST_PY = '''"""Contract tests for the config resolver against the INTERNAL config
resolution policy of the infra team. The policy is not public: each
scenario's expected report is pinned as a sha256 DIGEST over the
resolver's report lines, so this file cannot become a copy of the
policy."""

import hashlib

import pytest

from config_resolver import ConfigResolver


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def drive(scenario):
    """One scenario: the resolver's report lines, joined."""
    resolver = ConfigResolver(
        defaults=scenario.get("defaults"),
        file=scenario.get("file"),
        profile=scenario.get("profile"),
        env=scenario.get("env"),
    )
    return "\\n".join(resolver.report())


SCENARIOS = [
    {
        "defaults": {
            "Api_Retry": "2",
            "server.host_name": "db-9.internal",
            "LOG__LEVEL": "info",
            "MAX_CONNS": 20,
        },
        "file": {
            "api.retry": "4",
            "SERVER_HOST_NAME": "db-7.internal",
            "log_level": "warn",
        },
        "profile": {"API_RETRY": "6"},
        "env": {"HBR_API__RETRY": "9", "HBR_LOG__LEVEL": "debug", "CFG_LEGACY": "x"},
    },
    {
        "defaults": {},
        "file": {"queue.region": "eu-west-1"},
        "profile": {},
        "env": {
            "HBR_QUEUE__REGION": "eu-west-3",
            "HBR_QUEUE_NAME": "orders",
            "HBR_TOKEN": "tok-9f31",
            "hbr_queue__region": "eu-west-9",
            "HBR_DB__PASSWORD": "hunter2",
        },
    },
    {
        "defaults": {
            "client_secret": "s3cr3t-aa17",
            "rotation_secrets": "weekly",
            "api_tokens": "tts-55",
            "auth_token": "tok-4411",
        },
        "file": {"AUTH__TOKEN": "tok-0000"},
        "profile": {"Api__Tokens": "tts-99"},
        "env": {"HBR_CLIENT__SECRET": "s3cr3t-zz29"},
    },
    {
        "defaults": {"__edge_case__": "a", "deploy.mode": "blue", "Feature_Flag": "off"},
        "file": {"EDGE.CASE": "b", "feature-flag": "on"},
        "profile": {"DEPLOY__MODE": "green"},
        "env": {
            "HBR_DEPLOY__MODE": "canary",
            "HBR_EDGE__CASE": "c",
            "HBR_A_B": "x",
            "HBR_FEATURE__FLAG": "test",
        },
    },
    {
        "defaults": {
            "Api_Retry": "2",
            "server.host_name": "db-9.internal",
            "LOG__LEVEL": "info",
            "MAX_CONNS": 20,
            "client_secret": "s3cr3t-aa17",
            "rotation_secrets": "weekly",
            "__edge_case__": "a",
            "deploy.mode": "blue",
            "Feature_Flag": "off",
            "db_password": "pg-dev-7735",
        },
        "file": {
            "api.retry": "4",
            "SERVER_HOST_NAME": "db-7.internal",
            "log_level": "warn",
            "EDGE.CASE": "b",
            "feature-flag": "on",
            "AUTH__TOKEN": "tok-0000",
        },
        "profile": {"API_RETRY": "6", "DEPLOY__MODE": "green", "Api__Tokens": "tts-99"},
        "env": {
            "HBR_API__RETRY": "9",
            "HBR_LOG__LEVEL": "debug",
            "HBR_DEPLOY__MODE": "canary",
            "HBR_EDGE__CASE": "c",
            "HBR_FEATURE__FLAG": "test",
            "HBR_TOKEN": "tok-9f31",
            "HBR_DB__PASSWORD": "hunter2",
            "HBR_QUEUE_NAME": "orders",
            "CFG_LEGACY": "x",
            "hbr_queue__region": "eu-west-9",
        },
    },
]

# sha256 of drive(scenario) per scenario, from the policy.
DIGESTS = [
    "5869ab1717adc11d95b5d3459ccaca335ae1153b849602cbd13c412ad922c980",
    "7510a5a43010fd689cb1697c8216399f0e9bd93333b6d326f6833158d4f786d5",
    "c6d78786c421b0572775320f9adfcbcebb7f2649690e84eea608ba850d74ed17",
    "05e918114cfbb1d33bfe2915ff5031d45cbe6a949110bbc8d55c69f6226d35d6",
    "6097c6a440db059613678cb923dcba0ceceb6abf6876be594be660aba68c9937",
]


@pytest.mark.parametrize(
    ("scenario", "digest"),
    zip(SCENARIOS, DIGESTS, strict=True),
    ids=[f"s{i:02d}" for i in range(1, len(SCENARIOS) + 1)],
)
def test_scenario(scenario, digest):
    text = drive(scenario)
    assert _digest(text) == digest, text
'''

# --- the private skill: the ONLY source of the standard ---------------------
_SKILL_HARBOR_CONFIG_RESOLUTION = """---
name: harbor-config-resolution
description: Harbor infra config resolution - precedence, key normalization, env prefixing, secrets.
version: 1.0.0
---

# Harbor Config Resolution Standard (internal)

How every service on the Harbor infra team resolves configuration.
Internal to the Harbor infra team - not published anywhere.

A resolver merges four sources, by name: `defaults`, `file`, `profile`,
`env`. Values are compared and reported as strings (a non-string is
rendered with `str()`).

## The mooring order (precedence)

Highest first: **profile, env, file, defaults**. A key's value is the
one from its highest source. A profile overlay outranks the
environment: a profile is the deploy-time override of record, and
environment variables are a developer convenience.

## Keel form (key normalization)

Before any comparison, merge or report, every key from
`defaults`/`file`/`profile` is folded to keel form:

- lowercase (ASCII),
- every `_` and every `.` becomes `-`,
- runs of consecutive separators collapse to one,
- leading and trailing separators are stripped.

`Server.Host_Name`, `SERVER__HOST_NAME__` and `server-host-name` are
one key. A key that folds to the empty string is dropped. Keel form
never contains `_`, `.` or an edge separator.

## The gangway rule (environment variables)

An environment variable belongs to the config iff its name starts
with the uppercase prefix `HBR_` (case-sensitive). Take the name
after the prefix, lowercase it, and turn every `__` (double
underscore) into one `-`. If a `_` remains, the variable is not ours
and is ignored. Then the same collapse-and-strip cleanup as keel
form applies; a result that folds to empty is dropped.

`HBR_API__TOKEN` -> `api-token`. `HBR_API_TOKEN` is ignored (a lone
`_` is not a separator). `hbr_api__token` is ignored (the prefix is
case-sensitive).

## Sealed keys (secrets)

A key is sealed iff its FINAL segment (after the last `-`) is `token`
or `secret`. `password` is not sealed: passwords are owned by the
auth subsystem, and a `password` key in config is a local dev
placeholder, not a credential. Plurals are not sealed either:
`api-tokens` and `rotation-secrets` are reported in the clear.

A sealed value is never reported: the manifest line shows the literal
`[hbr-redacted]` as the value.

## The manifest line (report)

The report is one line per resolved key, keys in ascending
lexicographic order, each line exactly:

    <key>=<value>@<source>

`<source>` is the winning source's name (`defaults`, `file`,
`profile`, `env`). Sealed keys report `[hbr-redacted]` as the value;
the source is still shown.

## Worked example

defaults `{"Cache_Ttl": "30"}`, file `{"cache.ttl": "60"}`,
profile `{"CACHE_TTL": "120"}`, env `{"HBR_CACHE__TTL": "300"}`:

- keel form: all four spellings are `cache-ttl`.
- mooring order: profile (120) outranks env (300).
- the report is exactly one line: `cache-ttl=120@profile`.
"""

TASKS: list[dict[str, Any]] = [
    _private2_task(
        "private2-infra-config",
        {
            "sources.py": _SHIPPED_SOURCES_PY,
            "config_resolver.py": _SHIPPED_CONFIG_RESOLVER_PY,
            "test_config_convention.py": _SHIPPED_TEST_PY,
        },
        "The tests in test_config_convention.py fail. This config resolver doesn't "
        "follow how we resolve configuration on the infra team — our internal policy "
        "decides which source wins, how keys are spelled, how environment variables are "
        "picked up, and which keys are secret. Find the root cause before proposing any "
        "fix, then fix it and make the whole test suite green.",
        "harbor-config-resolution",
        _SKILL_HARBOR_CONFIG_RESOLUTION,
        "indirect",
        [
            "mooring order",
            "keel form",
            "gangway rule",
            "sealed",
            "[hbr-redacted]",
            "manifest line",
        ],
    ),
]

# --- the known root-cause fix: the resolver conforming to the standard ------
# Whole-file replacements (the fix IS the resolver re-implemented against
# the private standard), same shape as verify_private_fixtures.PRIVATE_FIXES.
_FIXED_SOURCES_PY = '''"""Config source conventions (the internal resolution policy).

The mooring order (a profile overlay outranks the environment), the
gangway prefix and the sealed-key report rendering.
"""

#: Ascending precedence: later sources win (profile outranks env).
SOURCE_ORDER = ["defaults", "file", "env", "profile"]

#: Only environment variables with this (case-sensitive) prefix are
#: config; `__` in the remainder is the separator.
ENV_PREFIX = "HBR_"

#: A key whose FINAL segment is one of these is sealed.
SEALED_SEGMENTS = ("token", "secret")

#: How a sealed value is rendered in the report.
SEALED_RENDER = "[hbr-redacted]"
'''

_FIXED_CONFIG_RESOLVER_PY = '''"""Resolve configuration from defaults, file, profile and env.

Implements the internal config resolution policy of the infra team:
keel-form key normalization, the gangway env mapping, the mooring
order and the sealed-key manifest lines.
"""

from sources import ENV_PREFIX, SEALED_RENDER, SEALED_SEGMENTS, SOURCE_ORDER


def _collapse_strip(text):
    """Collapse separator runs to one `-` and strip the edges."""
    while "--" in text:
        text = text.replace("--", "-")
    return text.strip("-")


def keel_form(key):
    """Canonical key form: lowercase, `_`/`.` folded to `-`, runs
    collapsed, edges stripped."""
    return _collapse_strip(str(key).lower().replace("_", "-").replace(".", "-"))


def gangway(name):
    """Env var name -> canonical key, or None when the variable is not
    ours: no HBR_ prefix, or a `_` left after the `__` -> `-` fold."""
    if not name.startswith(ENV_PREFIX):
        return None
    remainder = name[len(ENV_PREFIX) :].lower().replace("__", "-")
    if "_" in remainder:
        return None
    return _collapse_strip(remainder.replace(".", "-")) or None


class ConfigResolver:
    def __init__(self, *, defaults=None, file=None, profile=None, env=None):
        self._raw = {
            "defaults": dict(defaults or {}),
            "file": dict(file or {}),
            "profile": dict(profile or {}),
        }
        self._env = {}
        for name, value in dict(env or {}).items():
            key = gangway(name)
            if key is not None:
                self._env[key] = value

    def _winners(self):
        """The winning (value, source) per canonical key, in SOURCE_ORDER."""
        winners = {}
        for source in SOURCE_ORDER:
            source_dict = self._env if source == "env" else self._raw[source]
            for key, value in source_dict.items():
                canonical = key if source == "env" else keel_form(key)
                if not canonical:
                    continue
                winners[canonical] = (value, source)
        return winners

    def resolve(self):
        """The winning value per key."""
        return {key: value for key, (value, _) in self._winners().items()}

    def report(self):
        """One manifest line per key: key=value@source, keys sorted."""
        winners = self._winners()
        lines = []
        for key in sorted(winners):
            value, source = winners[key]
            if key.rsplit("-", 1)[-1] in SEALED_SEGMENTS:
                value = SEALED_RENDER
            lines.append(f"{key}={value}@{source}")
        return lines
'''

FIXES: dict[str, list[tuple[str, str | None, str]]] = {
    "private2-infra-config": [
        ("sources.py", None, _FIXED_SOURCES_PY),
        ("config_resolver.py", None, _FIXED_CONFIG_RESOLVER_PY),
    ],
}
