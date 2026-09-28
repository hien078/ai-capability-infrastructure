"""SQLAlchemy table models for the canonical registry (plan §41).

Identity uses natural keys throughout: versions are (capability_id, version),
releases are one row per (capability_id, channel) pointer. Normalized facet
tables arrive in Phase 5; versions carry facet metadata as JSONB + GIN now.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, ForeignKeyConstraint, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aci.adapters.outbound.postgres.base import Base


class CapabilityRow(Base):
    __tablename__ = "capabilities"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    owner_scope: Mapped[str] = mapped_column(Text, nullable=False, default="global")
    owner_scope_id: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)


class CapabilityVersionRow(Base):
    __tablename__ = "capability_versions"
    __table_args__ = (Index("ix_capability_versions_facets", "facets", postgresql_using="gin"),)

    capability_id: Mapped[str] = mapped_column(
        Text, ForeignKey("capabilities.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(nullable=False, default=1)
    content_digest: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    facets: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    compatibility: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class CapabilityReleaseRow(Base):
    __tablename__ = "capability_releases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["capability_id", "version"],
            ["capability_versions.capability_id", "capability_versions.version"],
            ondelete="CASCADE",
        ),
    )

    capability_id: Mapped[str] = mapped_column(Text, primary_key=True)
    channel: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    promoted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    approved_by: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    policy_snapshot_id: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)


class CapabilityBindingRow(Base):
    __tablename__ = "capability_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["capability_id", "version"],
            ["capability_versions.capability_id", "capability_versions.version"],
            ondelete="CASCADE",
        ),
    )

    binding_id: Mapped[str] = mapped_column(Text, primary_key=True)
    capability_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    binding_type: Mapped[str] = mapped_column(Text, nullable=False)
    visibility_scope: Mapped[str] = mapped_column(Text, nullable=False, default="public-production")
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class CapabilityArtifactRow(Base):
    __tablename__ = "capability_artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["capability_id", "version"],
            ["capability_versions.capability_id", "capability_versions.version"],
            ondelete="CASCADE",
        ),
    )

    capability_id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[str] = mapped_column(Text, primary_key=True)
    package_digest: Mapped[str] = mapped_column(Text, nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    files: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)


class SourceRecordRow(Base):
    __tablename__ = "source_records"
    __table_args__ = (
        ForeignKeyConstraint(["capability_id"], ["capabilities.id"], ondelete="CASCADE"),
        Index("ix_source_records_capability", "capability_id", "version"),
    )

    record_id: Mapped[str] = mapped_column(Text, primary_key=True)
    capability_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    #: Ingestion state machine (§22): quarantined/rejected/normalized/accepted.
    #: NOT a release channel — trust state lives on the provenance record.
    ingestion_status: Mapped[str] = mapped_column(Text, nullable=False, default="quarantined")
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_repository: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_url_reference: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    commit_sha: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    source_version: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    raw_snapshot_digest: Mapped[str] = mapped_column(Text, nullable=False)
    license_identifier: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingestion_tool_version: Mapped[str] = mapped_column(Text, nullable=False)
    local_transformations: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    derived_from: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)


class LicenseAssessmentRow(Base):
    __tablename__ = "license_assessments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["capability_id", "version"],
            ["capability_versions.capability_id", "capability_versions.version"],
            ondelete="CASCADE",
        ),
    )

    assessment_id: Mapped[str] = mapped_column(Text, primary_key=True)
    capability_id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[str] = mapped_column(Text, primary_key=True)
    license_identifier: Mapped[str] = mapped_column(Text, nullable=False)
    permissions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    assessed_by: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")


class SecurityAssessmentRow(Base):
    __tablename__ = "security_assessments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["capability_id", "version"],
            ["capability_versions.capability_id", "capability_versions.version"],
            ondelete="CASCADE",
        ),
    )

    assessment_id: Mapped[str] = mapped_column(Text, primary_key=True)
    capability_id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[str] = mapped_column(Text, primary_key=True)
    scan_status: Mapped[str] = mapped_column(Text, nullable=False)
    findings: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scanner_version: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)


class PolicySnapshotRow(Base):
    __tablename__ = "policy_snapshots"

    snapshot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class RouteRunRow(Base):
    __tablename__ = "route_runs"

    route_run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    request_id: Mapped[str] = mapped_column(Text, nullable=False)
    trace_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    organization_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_type: Mapped[str] = mapped_column(Text, nullable=False)
    client_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    protocol_type: Mapped[str] = mapped_column(Text, nullable=False)
    task_text: Mapped[str] = mapped_column(Text, nullable=False)
    policy_snapshot_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    eligible_count: Mapped[int] = mapped_column(nullable=False, default=0)
    stages: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    reranker_implementation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reranker_version: Mapped[str] = mapped_column(Text, nullable=False, default="")
    composer_implementation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    composer_version: Mapped[str] = mapped_column(Text, nullable=False, default="")
    latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    bundle_id: Mapped[str | None] = mapped_column(Text, nullable=True)


class BundleRow(Base):
    __tablename__ = "bundles"

    bundle_id: Mapped[str] = mapped_column(Text, primary_key=True)
    route_run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("route_runs.route_run_id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    execution_order: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    budget: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    policy_snapshot_id: Mapped[str | None] = mapped_column(Text, nullable=True)


class BundleItemRow(Base):
    __tablename__ = "bundle_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["capability_id", "version"],
            ["capability_versions.capability_id", "capability_versions.version"],
            ondelete="CASCADE",
        ),
    )

    bundle_id: Mapped[str] = mapped_column(
        Text, ForeignKey("bundles.bundle_id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(primary_key=True)
    capability_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    digest: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    load_mode: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False, default="")


class OutcomeEventRow(Base):
    __tablename__ = "outcome_events"

    outcome_id: Mapped[str] = mapped_column(Text, primary_key=True)
    route_run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("route_runs.route_run_id", ondelete="CASCADE"), nullable=False
    )
    bundle_id: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    tests_before: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    tests_after: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # §33 evidence envelope (Phase 13): client completion, build/test/lint
    # observations, human correction flag, and cost — all nullable.
    client_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    lint_passed: Mapped[bool | None] = mapped_column(nullable=True)
    build_passed: Mapped[bool | None] = mapped_column(nullable=True)
    changed_files: Mapped[int | None] = mapped_column(nullable=True)
    tool_calls: Mapped[int | None] = mapped_column(nullable=True)
    human_corrected: Mapped[bool | None] = mapped_column(nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    estimated_usd: Mapped[float | None] = mapped_column(nullable=True)


class OutcomeVerdictRow(Base):
    __tablename__ = "outcome_verdicts"

    outcome_id: Mapped[str] = mapped_column(
        Text, ForeignKey("outcome_events.outcome_id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str] = mapped_column(Text, nullable=False)


class BenchmarkCaseRow(Base):
    """§35 fixture, registered idempotently so results have integrity (§41)."""

    __tablename__ = "benchmark_cases"

    case_id: Mapped[str] = mapped_column(Text, primary_key=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    fixture: Mapped[str] = mapped_column(Text, nullable=False)
    task_text: Mapped[str] = mapped_column(Text, nullable=False)
    annotations: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    budget: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class BenchmarkRunRow(Base):
    __tablename__ = "benchmark_runs"

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    case_count: Mapped[int] = mapped_column(nullable=False)


class BenchmarkResultRow(Base):
    """One (case × variant) measurement with exact version references (§52)."""

    __tablename__ = "benchmark_results"
    __table_args__ = (
        ForeignKeyConstraint(["run_id"], ["benchmark_runs.run_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["case_id"], ["benchmark_cases.case_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["route_run_id"], ["route_runs.route_run_id"], ondelete="SET NULL"),
        Index("ix_benchmark_results_run", "run_id"),
    )

    result_id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    case_id: Mapped[str] = mapped_column(Text, nullable=False)
    variant: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    route_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    bundle_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    router: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class CapabilityRelationRow(Base):
    __tablename__ = "capability_relations"
    __table_args__ = (
        ForeignKeyConstraint(["source_capability_id"], ["capabilities.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["target_capability_id"], ["capabilities.id"], ondelete="CASCADE"),
        Index("ix_capability_relations_source", "source_capability_id"),
    )

    relation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_capability_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_version_constraint: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_capability_id: Mapped[str] = mapped_column(Text, nullable=False)
    target_version_constraint: Mapped[str | None] = mapped_column(Text, nullable=True)
    relation: Mapped[str] = mapped_column(Text, nullable=False)
    # Attribute name differs from the column name: `metadata` is reserved on
    # declarative bases. The physical column stays `metadata` (plan §41).
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class AgentTaskRow(Base):
    """V3 §56/§30.1: delegated task state; rows project immutable domain states."""

    __tablename__ = "agent_tasks"
    __table_args__ = (Index("ix_agent_tasks_capability", "capability_id"),)

    task_id: Mapped[str] = mapped_column(Text, primary_key=True)
    profile_id: Mapped[str] = mapped_column(Text, nullable=False)
    capability_id: Mapped[str] = mapped_column(Text, nullable=False)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TaskMessageRow(Base):
    """§30.1 Message: agent<->caller communication, append-only per task."""

    __tablename__ = "task_messages"
    __table_args__ = (
        ForeignKeyConstraint(["task_id"], ["agent_tasks.task_id"], ondelete="CASCADE"),
        Index("ix_task_messages_task", "task_id"),
    )

    message_id: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TaskArtifactRow(Base):
    """§30.1 Artifact: named, sha256-digest-pinned task output."""

    __tablename__ = "task_artifacts"
    __table_args__ = (
        ForeignKeyConstraint(["task_id"], ["agent_tasks.task_id"], ondelete="CASCADE"),
        Index("ix_task_artifacts_task", "task_id"),
    )

    artifact_id: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    digest: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
