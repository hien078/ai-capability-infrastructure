"""Skill content contracts (plan §§20-23): parsed metadata, provenance, ingestion result."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from aci.domain.capability.models import IngestionStatus


class SkillMetadata(BaseModel):
    """Parsed SKILL.md frontmatter. Body bytes stay in the content package."""

    model_config = {"frozen": True}

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    version: str | None = None
    license: str | None = None
    provides: list[str] = Field(default_factory=list)


class SourceProvenance(BaseModel):
    """Minimum provenance trail per ingestion (plan §23). Never erased by transforms."""

    model_config = {"frozen": True}

    record_id: str
    capability_id: str
    version: str
    #: Ingestion state machine (§22): quarantined → normalized → accepted,
    #: or rejected when a gate fails. Stored on the provenance record —
    #: ingestion state is NOT a release channel (§21).
    ingestion_status: IngestionStatus = "quarantined"
    source_type: str = "local-directory"
    source_repository: str | None = None
    source_path: str
    source_url_reference: str | None = None
    commit_sha: str | None = None
    source_version: str | None = None
    raw_snapshot_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    license_identifier: str | None = None
    ingested_at: datetime
    ingestion_tool_version: str
    local_transformations: list[str] = Field(default_factory=list)
    derived_from: str | None = None


class IngestionResult(BaseModel):
    model_config = {"frozen": True}

    capability_id: str
    version: str
    capability_created: bool
    already_ingested: bool
    raw_snapshot_digest: str
    package_digest: str
    file_count: int
    #: Ingestion state after this run (§22): quarantined until gates pass.
    ingestion_status: IngestionStatus = "quarantined"
    transformations: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)
