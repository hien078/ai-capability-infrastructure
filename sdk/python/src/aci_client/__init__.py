"""aci-client — thin typed Python SDK for the ACI REST API (plan §31).

Convenience client only (ADR-007): it wraps the public REST surface
(``/v1/*`` + the OpenCode skill catalog) and never imports ``aci``
internals, so it works against a remote server. See :mod:`aci_client.client`
for the §31.1 retry policy and :mod:`aci_client.errors` for the §45 error
typing.
"""

from aci_client._version import __version__
from aci_client.client import ACIClient, AsyncACIClient
from aci_client.errors import (
    ACIConnectionError,
    ACIError,
    ACIValidationError,
    SkillIntegrityError,
)
from aci_client.models import (
    DEFAULT_MAX_CONTEXT_TOKENS,
    AgentRun,
    ArtifactFile,
    Budget,
    BundleBudget,
    BundleItem,
    CapabilityArtifact,
    CapabilityBundle,
    CapabilitySearchResult,
    CapabilityVersionSummary,
    OutcomeEvidence,
    OutcomeVerdict,
    ResolvedVersion,
    RouteResult,
    RunChanges,
    RunFileChange,
    RunUsage,
    SkillContent,
    SkillFile,
    TaskContext,
)

__all__ = [
    "__version__",
    # clients
    "ACIClient",
    "AsyncACIClient",
    # errors
    "ACIConnectionError",
    "ACIError",
    "ACIValidationError",
    "SkillIntegrityError",
    # models
    "AgentRun",
    "ArtifactFile",
    "Budget",
    "BundleBudget",
    "BundleItem",
    "CapabilityArtifact",
    "CapabilityBundle",
    "CapabilitySearchResult",
    "CapabilityVersionSummary",
    "DEFAULT_MAX_CONTEXT_TOKENS",
    "OutcomeEvidence",
    "OutcomeVerdict",
    "ResolvedVersion",
    "RouteResult",
    "RunChanges",
    "RunFileChange",
    "RunUsage",
    "SkillContent",
    "SkillFile",
    "TaskContext",
]
