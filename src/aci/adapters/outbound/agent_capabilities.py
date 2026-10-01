"""Registry-backed ACIClient for the HarnessKernel (harness.md §11; ADR-014).

The kernel's CapabilityRuntime reaches the capability plane through the
ACIClient port (``search`` / ``resolve``). This adapter answers from the ONE
registry with the SAME §14 routing use case ``POST /v1/routes`` runs — never
a parallel selection path:

- ``search`` turns the normalized need (objective + constraints; never the
  conversation, never the model's free-text ``reason`` — §11.3) into a
  ``RouteCapabilitiesCommand`` restricted to kind ``skill`` (the only kind
  with a loadable instruction payload). Eligibility runs before retrieval
  (ADR-009), so only active production releases that pass the live policy
  come back; an empty bundle is a valid, empty result (ADR-008).
- ``resolve`` reads the pinned version's entry file (``SKILL.md``) from the
  content-addressed object store exactly like the OpenCode catalog / MCP
  skills extension, and returns the bytes with the digest OF THE BYTES READ.
  CapabilityRuntime compares it with the selection's digest (the artifact
  manifest's entry hash), so a tampered blob fails activation (§26.3).

Defense in depth for activation: one client is built PER RUN, and
``resolve`` serves only (capability_id, version) pairs this run's ``search``
selected — and only while that exact version is still the active production
release of a skill (revocation between search and resolve is honored, §46).
A forged or stale selection can never load a non-production, quarantined or
ineligible capability.

Telemetry decision: ``search`` reuses ``RouteCapabilitiesService.route()``
unchanged, so every kernel capability request persists a ``route_runs`` row +
bundle exactly like ``/v1/routes`` (client_type ``harness-kernel``, protocol
``harness``). One telemetry stream for every selection ACI makes (§36) keeps
kernel selections measurable/evaluable with the existing tooling, and the
stored ``task_text`` is only the normalized need.

Kernel selection policy (kernel path ONLY — /v1/routes is untouched): the
routed bundle is what ACI judged relevant, but every selection the kernel
activates is resent to the model on every turn (capability items are a
protected context kind). So ``search`` narrows the routed bundle with a
``SelectionPolicy`` before returning selections — top-k, a relative rerank
score margin, and a total token cap over the REAL entry-file sizes
(``estimated_context_tokens``, not the router's summary-based estimate).
The rerank score is read back from the route run's persisted ``reranked``
stage trace (the same numbers /v1/routes telemetry stores) — no routing
contract changes. The route run still records the FULL routed bundle; the
kernel's kept/dropped decision (capability ids + reason codes, never text)
is logged on ``aci.agent_capabilities`` and kept on the client
(``decisions``). The event bus lives in the run controller, out of reach of
the ACIClient port, so a log line is the least invasive honest channel.
"""

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import ceil
from uuid import uuid4

from aci.application.protocols import (
    ArtifactStore,
    CapabilityRepository,
    ObjectStore,
    ReleaseRepository,
    RouteRunRepository,
)
from aci.application.report_outcome import ReportOutcomeService, new_outcome_id
from aci.application.route_capabilities import RouteCapabilitiesService
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import (
    DEFAULT_MAX_CONTEXT_TOKENS,
    ArtifactFile,
    BundleItem,
    CapabilityArtifact,
    OutcomeEvidence,
    OutcomeVerdict,
    RouteCapabilitiesCommand,
    SkillSpec,
    TaskContext,
)
from aci.domain.policy.models import ClientDescriptor, ProtocolDescriptor, RequestContext
from aci.domain.runtime.actions import CapabilityRequest
from aci.providers.skills.package import package_digest
from aci.routing.composer import CHARS_PER_TOKEN
from aci.runtime.capability_runtime import MAX_INSTRUCTION_TOKENS, ACISelection

logger = logging.getLogger("aci.agent_capabilities")

CLIENT_TYPE = "harness-kernel"
PROTOCOL_TYPE = "harness"
#: The kernel is a service-side principal with no tenant scope: only
#: public-scope capabilities are eligible (least privilege, §15 scope rule).
DEFAULT_PRINCIPAL = "harness-kernel"
_MAX_TASK_TEXT = 8000  # RouteCapabilitiesCommand.task_text bound


@dataclass
class RegistrySelection:
    """One routed, pinned skill (satisfies the ACISelection protocol, §11.4)."""

    capability_id: str
    version: str
    payload_ref: str
    digest: str
    estimated_context_tokens: int
    route_run_id: str
    bundle_id: str


@dataclass(frozen=True)
class SelectionPolicy:
    """How many of a routed bundle's skills one kernel capability request
    activates. ``None`` disables a rule (the bare client default: no
    narrowing — the composition root supplies the configured policy).

    - ``max_items``: top-k, in rank order.
    - ``min_score_margin``: keep items whose rerank score is >= top score -
      margin (relative to the bundle's top item). Skipped when scores are
      unavailable (route run unreadable) — top-k + token cap still apply.
    - ``max_total_tokens``: running total of real entry sizes; an item that
      would push the total over the cap is dropped (greedy in rank order).
    - ``max_skill_tokens``: the per-skill cap (CapabilityRuntime's instruction
      ceiling). The top item is kept even when it ALONE exceeds
      ``max_total_tokens``, as long as it fits this per-skill cap — a request
      that routed something relevant never comes back empty just because the
      total cap is tight.
    """

    max_items: int | None = None
    min_score_margin: float | None = None
    max_total_tokens: int | None = None
    max_skill_tokens: int = MAX_INSTRUCTION_TOKENS

    def __post_init__(self) -> None:
        if self.max_items is not None and self.max_items < 1:
            raise ValueError("max_items must be >= 1")
        if self.min_score_margin is not None and self.min_score_margin < 0:
            raise ValueError("min_score_margin must be >= 0")
        if self.max_total_tokens is not None and self.max_total_tokens < 1:
            raise ValueError("max_total_tokens must be >= 1")
        if self.max_skill_tokens < 1:
            raise ValueError("max_skill_tokens must be >= 1")


#: Reason codes for the kernel's selection decision (telemetry vocabulary).
KEPT = "kept"
KEPT_TOP_OVER_TOTAL_CAP = "kept_top_over_token_cap"
DROPPED_MAX_ITEMS = "max_items"
DROPPED_SCORE_MARGIN = "score_margin"
DROPPED_TOTAL_CAP = "token_cap"
#: Float tolerance so the margin boundary is inclusive (0.50 - 0.05 vs 0.45).
_SCORE_EPSILON = 1e-9


@dataclass(frozen=True)
class SelectionEntry:
    capability_id: str
    version: str
    score: float | None
    tokens: int
    reason: str


@dataclass(frozen=True)
class SelectionDecision:
    """The kernel's narrowing of one routed bundle: ids + reason codes only."""

    route_run_id: str
    bundle_id: str
    scores_available: bool
    kept: tuple[SelectionEntry, ...] = field(default_factory=tuple)
    dropped: tuple[SelectionEntry, ...] = field(default_factory=tuple)


def apply_selection_policy(
    ranked: Sequence[tuple[RegistrySelection, float | None]],
    policy: SelectionPolicy,
) -> tuple[list[RegistrySelection], list[SelectionEntry], list[SelectionEntry]]:
    """Pure + deterministic: ``ranked`` is the bundle in rank order with each
    item's rerank score (``None`` = unknown). Returns (kept selections in rank
    order, kept entries, dropped entries)."""
    top_score = ranked[0][1] if ranked else None
    kept: list[RegistrySelection] = []
    kept_entries: list[SelectionEntry] = []
    dropped: list[SelectionEntry] = []
    spent = 0
    for position, (selection, score) in enumerate(ranked):
        tokens = selection.estimated_context_tokens
        reason = KEPT
        if policy.max_items is not None and len(kept) >= policy.max_items:
            reason = DROPPED_MAX_ITEMS
        elif (
            policy.min_score_margin is not None
            and top_score is not None
            and score is not None
            and score < top_score - policy.min_score_margin - _SCORE_EPSILON
        ):
            reason = DROPPED_SCORE_MARGIN
        elif policy.max_total_tokens is not None and spent + tokens > policy.max_total_tokens:
            alone_top = position == 0 and not kept and tokens <= policy.max_skill_tokens
            reason = KEPT_TOP_OVER_TOTAL_CAP if alone_top else DROPPED_TOTAL_CAP
        entry = SelectionEntry(
            capability_id=selection.capability_id,
            version=selection.version,
            score=score,
            tokens=tokens,
            reason=reason,
        )
        if reason in (KEPT, KEPT_TOP_OVER_TOTAL_CAP):
            kept.append(selection)
            kept_entries.append(entry)
            spent += tokens
        else:
            dropped.append(entry)
    return kept, kept_entries, dropped


def _rerank_scores(run: object) -> dict[tuple[str, str], float]:
    """(capability_id, version) → rerank score from a route run's persisted
    ``reranked`` stage trace (RouteCapabilitiesService writes it)."""
    stages = getattr(run, "stages", None)
    rows = stages.get("reranked") if isinstance(stages, dict) else None
    scores: dict[tuple[str, str], float] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        cid, version, score = row.get("capability_id"), row.get("version"), row.get("score")
        if isinstance(cid, str) and isinstance(version, str) and isinstance(score, int | float):
            scores[(cid, version)] = float(score)
    return scores


def normalized_need(request: CapabilityRequest) -> str:
    """§11.3 — the routing text is the objective + declared constraints only."""
    parts = [request.objective.strip() or request.objective]
    parts.extend(c.strip() for c in request.constraints if c.strip())
    return "\n".join(parts)[:_MAX_TASK_TEXT]


def _entry_path(version_spec: object) -> str:
    return version_spec.entrypoint if isinstance(version_spec, SkillSpec) else "SKILL.md"


def _entry_file(artifact: CapabilityArtifact, path: str) -> ArtifactFile | None:
    return next((f for f in artifact.files if f.path == path), None)


class RegistryCapabilityClient:
    """ACIClient over the live registry + §14 router. Build one per run."""

    def __init__(
        self,
        routes: RouteCapabilitiesService,
        releases: ReleaseRepository,
        capabilities: CapabilityRepository,
        artifacts: ArtifactStore,
        objects: ObjectStore,
        *,
        principal_id: str = DEFAULT_PRINCIPAL,
        max_items: int = 5,
        max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
        route_runs: RouteRunRepository | None = None,
        selection_policy: SelectionPolicy | None = None,
    ) -> None:
        self._routes = routes
        #: Read-back of the persisted route run for rerank scores; without it
        #: the margin rule is skipped (scores unavailable).
        self._route_runs = route_runs
        self._policy = selection_policy or SelectionPolicy()
        #: The kernel's narrowing decision per search (ids + reasons only).
        self.decisions: list[SelectionDecision] = []
        self._releases = releases
        self._capabilities = capabilities
        self._artifacts = artifacts
        self._objects = objects
        self._principal_id = principal_id
        self._max_items = max_items
        self._max_context_tokens = max_context_tokens
        #: (capability_id, version) pairs this run's search selected — the
        #: only pairs resolve() will ever serve.
        self._issued: set[tuple[str, str]] = set()
        #: §3.2 feedback linkage: the (route_run_id, bundle_id) pairs this
        #: run's searches routed WITH kept skills — the §33 attach points the
        #: terminal-state sink (RegistryCapabilityFeedback) joins the run's
        #: outcome to. Empty bundles (ADR-008 abstentions) are honest
        #: non-attachments: nothing was ever loadable from them.
        self.routed_bundles: list[tuple[str, str]] = []

    # -- search ----------------------------------------------------------------

    def search(self, request: CapabilityRequest) -> list[ACISelection]:
        if request.desired_kinds and "skill" not in request.desired_kinds:
            return []  # only skills carry a loadable payload; nothing to route
        command = RouteCapabilitiesCommand(
            task_text=normalized_need(request),
            context=TaskContext(),
            max_items=self._max_items,
            max_context_tokens=self._max_context_tokens,
            allowed_kinds=["skill"],
        )
        envelope = RequestContext(
            request_id=f"req_{uuid4().hex}",
            trace_id=f"trc_{uuid4().hex}",
            principal_id=self._principal_id,
            client=ClientDescriptor(type=CLIENT_TYPE),
            protocol=ProtocolDescriptor(type=PROTOCOL_TYPE, version="1"),
        )
        result = self._routes.route(
            command, envelope.to_routing_context(command.context), request=envelope
        )
        routed: list[RegistrySelection] = []
        for item in result.bundle.items:
            selection = self._select(
                item, route_run_id=result.route_run_id, bundle_id=result.bundle.bundle_id
            )
            if selection is not None:
                routed.append(selection)
        scores = self._scores(result.route_run_id) if routed else None
        kept, kept_entries, dropped = apply_selection_policy(
            [
                (s, None if scores is None else scores.get((s.capability_id, s.version)))
                for s in routed
            ],
            self._policy,
        )
        decision = SelectionDecision(
            route_run_id=result.route_run_id,
            bundle_id=result.bundle.bundle_id,
            scores_available=scores is not None,
            kept=tuple(kept_entries),
            dropped=tuple(dropped),
        )
        self.decisions.append(decision)
        self._log(decision)
        if kept:
            self.routed_bundles.append((decision.route_run_id, decision.bundle_id))
        # Only KEPT pairs are ever resolvable for this run (defense in depth).
        for selection in kept:
            self._issued.add((selection.capability_id, selection.version))
        return list(kept)

    def _scores(self, route_run_id: str) -> dict[tuple[str, str], float] | None:
        if self._route_runs is None:
            return None
        run = self._route_runs.get_route_run(route_run_id)
        return _rerank_scores(run) if run is not None else None

    @staticmethod
    def _log(decision: SelectionDecision) -> None:
        if not decision.kept and not decision.dropped:
            return
        logger.info(
            "capability.selection route_run=%s bundle=%s scores=%s kept=%s dropped=%s",
            decision.route_run_id,
            decision.bundle_id,
            "rerank" if decision.scores_available else "unavailable",
            [f"{e.capability_id}@{e.version}:{e.reason}" for e in decision.kept],
            [f"{e.capability_id}@{e.version}:{e.reason}" for e in decision.dropped],
        )

    def _select(
        self, item: BundleItem, *, route_run_id: str, bundle_id: str
    ) -> RegistrySelection | None:
        """Pinned bundle item → selection, binding the bundle digest to the
        entry file: content_digest == artifact.package_digest ==
        package_digest(manifest files) ∋ entry sha256."""
        if item.kind != "skill":
            return None
        version = self._capabilities.get_version(item.capability_id, item.version)
        if version is None or version.kind != "skill":
            return None
        artifact = self._artifacts.get_artifact(item.capability_id, item.version)
        if artifact is None:
            return None  # same rule as the catalog: no artifact, not loadable
        if (
            artifact.package_digest != item.digest
            or f"sha256:{package_digest(artifact.files)}" != artifact.package_digest
        ):
            raise DomainError(
                ErrorCode.ARTIFACT_INTEGRITY_ERROR,
                f"artifact manifest for {item.capability_id}@{item.version} "
                "does not match its pinned digest",
            )
        path = _entry_path(version.spec)
        entry = _entry_file(artifact, path)
        if entry is None:
            return None
        return RegistrySelection(
            capability_id=item.capability_id,
            version=item.version,
            payload_ref=f"skill://{item.capability_id}@{item.version}/{path}",
            digest=f"sha256:{entry.sha256}",
            estimated_context_tokens=max(1, ceil(entry.size_bytes / CHARS_PER_TOKEN)),
            route_run_id=route_run_id,
            bundle_id=bundle_id,
        )

    # -- resolve ---------------------------------------------------------------

    def resolve(self, capability_id: str, version: str) -> tuple[bytes, str]:
        """(entry bytes, ``sha256:<hex>`` of those bytes). Verification is the
        caller's job (CapabilityRuntime compares against the selection)."""
        if (capability_id, version) not in self._issued:
            raise DomainError(
                ErrorCode.CAPABILITY_NOT_FOUND,
                f"{capability_id}@{version} was not selected for this run",
            )
        release = self._releases.get_release(capability_id, "production")
        if release is None or release.status != "active" or release.version != version:
            raise DomainError(
                ErrorCode.CAPABILITY_NOT_FOUND,
                f"{capability_id}@{version} is not the active production release",
            )
        pinned = self._capabilities.get_version(capability_id, version)
        if pinned is None or pinned.kind != "skill":
            raise DomainError(ErrorCode.CAPABILITY_NOT_FOUND, f"unknown skill {capability_id}")
        artifact = self._artifacts.get_artifact(capability_id, version)
        if artifact is None:
            raise DomainError(ErrorCode.CAPABILITY_NOT_FOUND, f"no artifact for {capability_id}")
        path = _entry_path(pinned.spec)
        entry = _entry_file(artifact, path)
        if entry is None:
            raise DomainError(
                ErrorCode.CAPABILITY_NOT_FOUND, f"{capability_id}@{version} has no file {path}"
            )
        data = self._objects.get(entry.sha256)
        if data is None:
            raise DomainError(
                ErrorCode.ARTIFACT_INTEGRITY_ERROR,
                f"blob {entry.sha256} missing for {capability_id}/{path}",
            )
        return data, f"sha256:{hashlib.sha256(data).hexdigest()}"


# -- §33 feedback sink (plan §3.2) --------------------------------------------


def kernel_outcome_verdicts(outcome: str, failure_class: str | None) -> list[OutcomeVerdict]:
    """Map the kernel seam's ``(outcome, failure_class)`` to §33 verdicts —
    ONE source per REAL observation, never a merge (ADR-010).

    - ``run_success``: succeeded is verifier-gated (INV-08 — the kernel only
      reports succeeded on a verified proposal), so ``test_harness
      success/high`` is a real observation, plus the run's own claim.
    - ``VERIFICATION_FAILED``: the verifier ran and refuted — a real
      ``test_harness failure/high`` observation, plus the run's claim.
    - any OTHER failure class (MODEL_FAILURE, TOOL_FAILURE, …): the run failed
      for reasons that are NOT a test observation — the run's own claim
      only; the tests state stays unclaimed, never guessed.
    - no failure class (limits, cancelled, partial): ``unknown/low`` — §33
      keeps unknown as unknown.
    """
    if outcome == "run_success":
        return [
            OutcomeVerdict(source="test_harness", status="success", confidence="high"),
            OutcomeVerdict(source="agent_self_report", status="success", confidence="medium"),
        ]
    if failure_class == "VERIFICATION_FAILED":
        return [
            OutcomeVerdict(source="test_harness", status="failure", confidence="high"),
            OutcomeVerdict(source="agent_self_report", status="failure", confidence="medium"),
        ]
    if failure_class is not None:
        return [OutcomeVerdict(source="agent_self_report", status="failure", confidence="medium")]
    return [OutcomeVerdict(source="agent_self_report", status="unknown", confidence="low")]


class RegistryCapabilityFeedback:
    """§3.2 wiring: the kernel's per-capability outcome seam → §33
    ``OutcomeEvidence`` on the run's linked bundles (appending — the kernel's
    verifier is ONE source among sources, ADR-010; loaded-in-context is never
    a causal claim, ADR-014 amendments 14–17).

    The seam (``run_controller``, terminal state) fires ``report`` once per
    ACTIVATED capability; §33 attaches evidence to the BUNDLE, so the sink
    records ONE event per routed bundle (``client.routed_bundles`` — the
    pairs its searches kept skills for) and dedupes the rest.
    ``capability_id``/``version``/``evidence_refs`` stay in the run's own event
    stream (``capability.exposure``) — the §33 envelope has no field for them
    and nothing is fabricated. Exceptions propagate to the seam's own except
    (feedback must never fail the run, §50 telemetry).
    """

    def __init__(
        self, client: RegistryCapabilityClient, outcome_service: ReportOutcomeService
    ) -> None:
        self._client = client
        self._outcomes = outcome_service
        self._recorded: set[str] = set()

    def report(
        self,
        *,
        capability_id: str,
        version: str,
        outcome: str,
        failure_class: str | None,
        evidence_refs: list[str],
    ) -> None:
        for route_run_id, bundle_id in list(self._client.routed_bundles):
            if bundle_id in self._recorded:
                continue  # one §33 event per bundle; the seam fires per capability
            self._outcomes.report(
                OutcomeEvidence(
                    outcome_id=new_outcome_id(),
                    route_run_id=route_run_id,
                    bundle_id=bundle_id,
                    received_at=datetime.now(UTC),
                    verdicts=kernel_outcome_verdicts(outcome, failure_class),
                    client_status=outcome,
                )
            )
            self._recorded.add(bundle_id)


class RegistryCapabilityClientFactory:
    """Composition-root handle: one fresh, run-scoped client per kernel run."""

    def __init__(
        self,
        routes: RouteCapabilitiesService,
        releases: ReleaseRepository,
        capabilities: CapabilityRepository,
        artifacts: ArtifactStore,
        objects: ObjectStore,
        *,
        principal_id: str = DEFAULT_PRINCIPAL,
        max_items: int = 5,
        max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
        route_runs: RouteRunRepository | None = None,
        selection_policy: SelectionPolicy | None = None,
    ) -> None:
        # Bundle budgets default to the /v1/routes request defaults, so the
        # route run records the same full bundle /v1/routes would; the
        # kernel-side narrowing is the separate ``selection_policy``.
        self._deps = (routes, releases, capabilities, artifacts, objects)
        self._principal_id = principal_id
        self._max_items = max_items
        self._max_context_tokens = max_context_tokens
        self._route_runs = route_runs
        self.selection_policy = selection_policy or SelectionPolicy()

    def __call__(self) -> RegistryCapabilityClient:
        return RegistryCapabilityClient(
            *self._deps,
            principal_id=self._principal_id,
            max_items=self._max_items,
            max_context_tokens=self._max_context_tokens,
            route_runs=self._route_runs,
            selection_policy=self.selection_policy,
        )
