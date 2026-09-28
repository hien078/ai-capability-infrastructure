"""Demand & acquisition schemas (crawl.md STEP 1, §2-5, §35).

The Demand/Excellence separation (crawl.md §2 — the plan's most
important architectural boundary):

    Demand Intelligence  answers "what should we SEARCH for?"
    Excellence Intelligence answers "what should we KEEP?"

Demand may decide what to investigate; it must NEVER directly imply
quality (§2.1) — the exact failure the §80 corpus-growth experiment
measured (growing debugging skills on demand made recall WORSE).

All schemas are frozen Pydantic contracts — data, never logic. The
engines that produce/consume them live in application/providers.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

#: crawl.md §3.2 demand windows.
DemandWindow = Literal["24h", "7d", "30d", "90d"]


class DemandSignal(BaseModel):
    """One observed demand signal (crawl.md §3, §4.1 canonical form).

    Never just keywords: the canonical representation is semantic —
    domain/subdomain/capability/intent — so the planner can reason
    about WHAT is wanted, not which words appeared.
    """

    model_config = {"frozen": True}

    signal_id: str = Field(min_length=1)
    source: Literal[
        "immediate_client_request",
        "recent_client_interest",
        "persistent_recurring_demand",
        "aggregate_platform_demand",
        "corpus_gap",
        "trend",
        "strategic_source_change",
        "exploration",
    ]
    window: DemandWindow | None = None
    domain: str = ""
    subdomain: str = ""
    capabilities: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    intent: list[str] = Field(default_factory=list)
    observed_at: datetime
    #: How the signal was derived (audit §80) — e.g. "taste-analyzer:1.0.0".
    derived_by: str = "unknown"
    evidence: dict[str, Any] = Field(default_factory=dict)


class AcquisitionOpportunity(BaseModel):
    """A ranked reason to search (crawl.md §2.1 output, §4.2 priority).

    The planner consumes these and emits AcquisitionJobs. An opportunity
    is DEMAND, never quality: priority high does not mean the found
    candidates will be good — it means the gap hurts.
    """

    model_config = {"frozen": True}

    opportunity_id: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    reasons: list[str] = Field(min_length=1)
    priority: Literal["P0", "P1", "P2", "P3", "P4", "P5"] = "P2"
    #: crawl.md §40: P0 regression/security … P5 exploration.
    priority_rationale: str = ""
    horizon_days: int = Field(default=30, ge=1, le=365)
    created_at: datetime
    created_by: str = "demand-intelligence"
    #: Search seeds the planner may expand from (§6 query generation).
    seed_queries: list[str] = Field(default_factory=list)


class DiscoveryCandidate(BaseModel):
    """One thing a discovery adapter found (crawl.md §7 canonical output).

    Source-agnostic: GitHub/arXiv/papers all emit this shape so the rest
    of the pipeline never depends on a source-specific schema.
    """

    model_config = {"frozen": True}

    candidate_id: str = Field(min_length=1)
    source: str = Field(min_length=1)  # adapter name: github, arxiv, …
    source_type: Literal["repository", "paper", "package", "blog", "official_feed"]
    canonical_url: str = Field(min_length=1)
    owner: str = ""
    artifact_type: str = ""
    discovered_at: datetime
    published_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    #: Trend/maintenance signals recorded but NEVER quality (§3.6:
    #: TREND = reason to investigate, not verdict).
    trend_signals: dict[str, Any] = Field(default_factory=dict)


#: crawl.md §12.1 supported capability types.
CapabilityType = Literal[
    "skill",
    "tool",
    "workflow",
    "agent",
    "agent-pattern",
    "harness",
    "evaluator",
    "router",
    "planner",
    "memory-strategy",
    "retrieval-strategy",
    "verifier",
    "guardrail",
    "benchmark",
    "resource",
    "service",
    "protocol-adapter",
]


class MinedCapability(BaseModel):
    """One mechanism mined from a repository (crawl.md §12, §12.2).

    The mining question is NOT "is this repo elite?" but "which
    mechanisms inside it deserve independent evaluation?" (§12) —
    a repo may yield zero, one, or many of these.
    """

    model_config = {"frozen": True}

    mined_id: str = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)  # file paths in the snapshot
    type: CapabilityType
    name: str = Field(min_length=1)
    problem: str = ""
    mechanism: str = ""
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    execution_pattern: str = ""
    claimed_benefits: list[str] = Field(default_factory=list)
    #: crawl.md §30: claim types are stored SEPARATELY, never collapsed.
    observed_in_code: bool = False
    known_limitations: list[str] = Field(default_factory=list)
    extractability: Literal["direct", "adapt", "concept_only", "blocked"] = "adapt"
    license_constraints: str = ""
    mined_by: str = "unknown"
    mined_at: datetime


class EvidenceItem(BaseModel):
    """One structured evidence claim (crawl.md §19).

    No unsupported "8.9/10" verdicts: every claim carries evidence,
    a source location, confidence, and — critically — counter-evidence.
    """

    model_config = {"frozen": True}

    claim: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)
    source_location: str = ""
    confidence: Literal["low", "medium", "medium_high", "high"] = "medium"
    counter_evidence: list[str] = Field(default_factory=list)
    uncertainty: str = ""


class EvidencePackage(BaseModel):
    """The controlled context judges consume (crawl.md §54).

    Judges never see arbitrary unbounded repo context — they see this
    package: provenance, the mined capability, static metrics, security
    findings, comparison candidates, benchmark results.
    """

    model_config = {"frozen": True}

    package_id: str = Field(min_length=1)
    mined_id: str = Field(min_length=1)
    provenance: dict[str, Any] = Field(default_factory=dict)
    repository_map: dict[str, Any] = Field(default_factory=dict)
    capability: dict[str, Any] = Field(default_factory=dict)
    source_references: list[str] = Field(default_factory=list)
    author_claims: list[str] = Field(default_factory=list)
    static_metrics: dict[str, Any] = Field(default_factory=dict)
    dependency_info: dict[str, Any] = Field(default_factory=dict)
    security_findings: list[str] = Field(default_factory=list)
    comparison_candidates: list[str] = Field(default_factory=list)
    benchmark_results: dict[str, Any] = Field(default_factory=dict)
    assembled_at: datetime
    assembled_by: str = "evidence-assembler"


#: crawl.md §15 Excellence Vector — never permanently compressed to one
#: scalar; a scalar may be DERIVED for queue ordering but the vector stays.
EXCELLENCE_DIMENSIONS: tuple[str, ...] = (
    "correctness",
    "empirical_performance",
    "reliability",
    "robustness",
    "security",
    "novelty",
    "generalizability",
    "composability",
    "maintainability",
    "observability",
    "reproducibility",
    "efficiency",
    "token_efficiency",
    "latency",
    "documentation",
    "maturity",
    "provenance_confidence",
    "freshness",
    "task_relevance",
    "capability_gain",
    "redundancy",
)


class ExcellenceVector(BaseModel):
    """The multi-dimensional quality record (crawl.md §15).

    Scores are 0..5 or None (unknown — §60.9: absence of evidence is
    never a score). The vector is the source of truth; any scalar
    ranking is derived, never stored as the verdict.
    """

    model_config = {"frozen": True}

    vector_id: str = Field(min_length=1)
    mined_id: str = Field(min_length=1)
    scores: dict[str, float | None] = Field(default_factory=dict)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    #: Which excellence profile governed this evaluation (§17).
    profile: str = "skill"
    constitution_version: str = ""
    evaluated_at: datetime
    evaluated_by: str = "unknown"

    def validate_dimensions(self) -> list[str]:
        """Unknown dimension names — callers must check and reject."""
        return [d for d in self.scores if d not in EXCELLENCE_DIMENSIONS]
