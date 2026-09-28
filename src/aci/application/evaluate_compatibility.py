"""Compatibility evaluation (auto.md §20; plan §48).

"Skill tốt nhưng client không dùng được thì cũng vô ích." A candidate
reaching staging must be checked against the clients that will consume
it — OpenCode (catalog + lazy-load), MCP (skills extension + resource
template), REST (bundle resolution).

This is a FILTER, not a preference (§48): the evaluation reads the
version's ``Compatibility`` declaration (intrinsic, declared by the
package) against a client's requirements and returns a structured
report. It never mutates anything and never blocks on its own — the
promotion proposal (§24) carries the report; the human/policy gate
decides.

V2 scope (auto.md §20): basic client compatibility — OpenCode/MCP/REST
representation validity, OS-agnostic (skills are markdown + data
files), language/framework declared vs required.
"""

from dataclasses import dataclass

from aci.domain.capability.models import CapabilityVersion, Compatibility


@dataclass(frozen=True)
class ClientRequirement:
    """What one client needs to consume a skill (auto.md §20 checks)."""

    client: str
    #: entrypoint file the client resolves (OpenCode/MCP: SKILL.md)
    required_entrypoint: str | None = None
    #: language the client's runtime assumes, None = agnostic
    language: str | None = None


#: The real clients this deployment serves (§28 OpenCode preferred, §29 MCP).
DEFAULT_CLIENTS: tuple[ClientRequirement, ...] = (
    ClientRequirement(client="opencode", required_entrypoint="SKILL.md"),
    ClientRequirement(client="mcp", required_entrypoint="SKILL.md"),
    ClientRequirement(client="rest"),
)


@dataclass(frozen=True)
class CompatibilityReport:
    """Per-client compatibility verdicts (auto.md §20 output shape)."""

    capability_id: str
    version: str
    results: dict[str, str]  # client -> compatible | incompatible | unknown
    notes: list[str]

    @property
    def all_compatible_or_unknown(self) -> bool:
        """No hard incompatibility anywhere — the promotion proposal
        carries this; unknown is honest (§60.9), never silently pass."""
        return all(v != "incompatible" for v in self.results.values())


def evaluate_compatibility(
    version: CapabilityVersion,
    clients: tuple[ClientRequirement, ...] = DEFAULT_CLIENTS,
) -> CompatibilityReport:
    """Evaluate one canonical version against the serving clients.

    Reads ONLY intrinsic declarations (Compatibility + SkillSpec) —
    never fetches, never executes, never inspects raw bodies beyond the
    already-parsed spec (§16.1 boundary holds here too).
    """
    compat: Compatibility | None = version.compatibility
    results: dict[str, str] = {}
    notes: list[str] = []

    if compat is None:
        # No declaration: the DECLARATION questions are unknown (§60.9)…
        for req in clients:
            results[req.client] = "unknown"
        notes.append(
            "no Compatibility declared — declaration unknown (§60.9); "
            "intrinsic checks (entrypoint) still evaluated below"
        )
    elif compat.supported_clients is not None:
        known = set(compat.supported_clients)
        for req in clients:
            results[req.client] = (
                "compatible" if req.client in known or "any" in known else "incompatible"
            )
    else:
        for req in clients:
            results[req.client] = "compatible"
        notes.append("no supported_clients declared — any client allowed (§48)")

    # Intrinsic checks run regardless of declaration: the entrypoint is
    # spec fact, not declaration — a mismatch is incompatible even when
    # nothing else is declared.
    for req in clients:
        if req.required_entrypoint is not None and version.spec is not None:
            entry = getattr(version.spec, "entrypoint", None)
            if entry is not None and entry != req.required_entrypoint:
                results[req.client] = "incompatible"
                notes.append(
                    f"{req.client}: entrypoint {entry!r} != required {req.required_entrypoint!r}"
                )
        if compat is not None and req.language is not None and compat.supported_languages:
            if req.language not in compat.supported_languages:
                results[req.client] = "incompatible"
                notes.append(
                    f"{req.client}: requires language {req.language}, "
                    f"version declares {compat.supported_languages}"
                )

    if compat is not None and compat.minimum_client_features:
        notes.append(
            "minimum_client_features declared but not yet checked "
            "(no client advertises features in V2 — recorded, not blocking)"
        )

    return CompatibilityReport(
        capability_id=version.capability_id,
        version=version.version,
        results=results,
        notes=notes,
    )
