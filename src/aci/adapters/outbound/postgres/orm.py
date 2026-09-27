"""SQLAlchemy table models for the canonical registry (plan §41).

Identity uses natural keys throughout: versions are (capability_id, version),
releases are one row per (capability_id, channel) pointer. Normalized facet
tables arrive in Phase 5; versions carry facet metadata as JSONB + GIN now.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aci.adapters.outbound.postgres.base import Base


class CapabilityRow(Base):
    __tablename__ = "capabilities"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    owner_scope: Mapped[str] = mapped_column(Text, nullable=False, default="global")


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
