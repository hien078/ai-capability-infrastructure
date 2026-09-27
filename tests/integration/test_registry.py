"""Phase 2 acceptance (plan §52): one registry, immutable versions,
release-pointer promotion/rollback, bindings, artifacts.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from aci.adapters.outbound.postgres.repositories import (
    SqlAlchemyArtifactStore,
    SqlAlchemyCapabilityRepository,
    SqlAlchemyReleaseRepository,
)
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import (
    ArtifactFile,
    Capability,
    CapabilityArtifact,
    CapabilityBinding,
    CapabilityRelease,
    CapabilityVersion,
    SkillSpec,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 27, tzinfo=UTC)
DIGEST = "sha256:" + "ab" * 32


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def make_capability(cap_id: str) -> Capability:
    return Capability(id=cap_id, kind="skill", created_at=NOW)


def make_version(cap_id: str, ver: str) -> CapabilityVersion:
    return CapabilityVersion(
        capability_id=cap_id,
        version=ver,
        kind="skill",
        content_digest=DIGEST,
        created_at=NOW,
        display_name="Systematic Debugging",
        description="Evidence-driven debugging workflow.",
        facets={"domain": ["software-engineering"], "task_type": ["debugging"]},
        spec=SkillSpec(provides=["root-cause-analysis"]),
    )


def seed_two_versions(
    capability_repo: SqlAlchemyCapabilityRepository, cap_id: str
) -> tuple[CapabilityVersion, CapabilityVersion]:
    capability_repo.create_capability(make_capability(cap_id))
    v1 = capability_repo.create_version(make_version(cap_id, "1.0.0"))
    v2 = capability_repo.create_version(make_version(cap_id, "2.0.0"))
    return v1, v2


def test_version_roundtrip_preserves_spec_and_facets(
    capability_repo: SqlAlchemyCapabilityRepository,
) -> None:
    cap_id = uid("cap")
    capability_repo.create_capability(make_capability(cap_id))
    capability_repo.create_version(make_version(cap_id, "1.0.0"))

    got = capability_repo.get_version(cap_id, "1.0.0")
    assert got is not None
    assert got.facets == {"domain": ["software-engineering"], "task_type": ["debugging"]}
    assert got.spec.provides == ["root-cause-analysis"]
    assert got.content_digest == DIGEST
    assert [v.version for v in capability_repo.list_versions(cap_id)] == ["1.0.0"]
    assert capability_repo.get_version(cap_id, "9.9.9") is None


def test_duplicate_capability_and_version_rejected(
    capability_repo: SqlAlchemyCapabilityRepository,
) -> None:
    cap_id = uid("cap")
    capability_repo.create_capability(make_capability(cap_id))
    with pytest.raises(DomainError) as exc:
        capability_repo.create_capability(make_capability(cap_id))
    assert exc.value.code == ErrorCode.CAPABILITY_ALREADY_EXISTS

    capability_repo.create_version(make_version(cap_id, "1.0.0"))
    with pytest.raises(DomainError) as exc:
        capability_repo.create_version(make_version(cap_id, "1.0.0"))
    assert exc.value.code == ErrorCode.CAPABILITY_ALREADY_EXISTS


def test_version_requires_parent_capability(
    capability_repo: SqlAlchemyCapabilityRepository,
) -> None:
    with pytest.raises(DomainError) as exc:
        capability_repo.create_version(make_version(uid("ghost"), "1.0.0"))
    assert exc.value.code == ErrorCode.CAPABILITY_NOT_FOUND


def test_published_version_is_immutable_in_db(
    capability_repo: SqlAlchemyCapabilityRepository,
    sessions: sessionmaker[Session],
) -> None:
    cap_id = uid("cap")
    seed_two_versions(capability_repo, cap_id)

    with sessions() as session:
        with pytest.raises(DBAPIError, match="[Ii]mmutable"):
            session.execute(
                text(
                    "UPDATE capability_versions SET display_name = 'hacked' "
                    "WHERE capability_id = :c AND version = '1.0.0'"
                ),
                {"c": cap_id},
            )
            session.commit()

    assert capability_repo.get_version(cap_id, "1.0.0") is not None
    got = capability_repo.get_version(cap_id, "1.0.0")
    assert got is not None and got.display_name == "Systematic Debugging"


def test_promotion_moves_pointer_without_touching_versions(
    capability_repo: SqlAlchemyCapabilityRepository,
    release_repo: SqlAlchemyReleaseRepository,
) -> None:
    cap_id = uid("cap")
    v1, v2 = seed_two_versions(capability_repo, cap_id)

    release_repo.set_release(
        CapabilityRelease(
            capability_id=cap_id, version="1.0.0", channel="production", status="active"
        )
    )
    prod = release_repo.get_release(cap_id, "production")
    assert prod is not None and prod.version == "1.0.0"

    release_repo.set_release(
        CapabilityRelease(
            capability_id=cap_id,
            version="2.0.0",
            channel="production",
            status="active",
            promoted_at=NOW,
            approved_by="reviewer-1",
        )
    )
    prod = release_repo.get_release(cap_id, "production")
    assert prod is not None and prod.version == "2.0.0"
    assert prod.approved_by == "reviewer-1"

    # Version rows untouched by promotion.
    assert capability_repo.get_version(cap_id, "1.0.0") == v1
    assert capability_repo.get_version(cap_id, "2.0.0") == v2


def test_rollback_restores_previous_pointer(
    capability_repo: SqlAlchemyCapabilityRepository,
    release_repo: SqlAlchemyReleaseRepository,
) -> None:
    cap_id = uid("cap")
    seed_two_versions(capability_repo, cap_id)
    release_repo.set_release(
        CapabilityRelease(
            capability_id=cap_id, version="2.0.0", channel="production", status="active"
        )
    )
    release_repo.set_release(
        CapabilityRelease(
            capability_id=cap_id, version="1.0.0", channel="production", status="active"
        )
    )
    prod = release_repo.get_release(cap_id, "production")
    assert prod is not None and prod.version == "1.0.0"


def test_release_requires_existing_version(
    capability_repo: SqlAlchemyCapabilityRepository,
    release_repo: SqlAlchemyReleaseRepository,
) -> None:
    cap_id = uid("cap")
    capability_repo.create_capability(make_capability(cap_id))
    with pytest.raises(DomainError) as exc:
        release_repo.set_release(
            CapabilityRelease(
                capability_id=cap_id, version="9.9.9", channel="production", status="active"
            )
        )
    assert exc.value.code == ErrorCode.CAPABILITY_VERSION_NOT_FOUND


def test_revoked_status_persists_on_pointer(
    capability_repo: SqlAlchemyCapabilityRepository,
    release_repo: SqlAlchemyReleaseRepository,
) -> None:
    cap_id = uid("cap")
    seed_two_versions(capability_repo, cap_id)
    release_repo.set_release(
        CapabilityRelease(
            capability_id=cap_id, version="1.0.0", channel="production", status="active"
        )
    )
    release_repo.set_release(
        CapabilityRelease(
            capability_id=cap_id, version="1.0.0", channel="production", status="revoked"
        )
    )
    prod = release_repo.get_release(cap_id, "production")
    assert prod is not None and prod.status == "revoked"


def test_release_list_and_misses(
    capability_repo: SqlAlchemyCapabilityRepository,
    release_repo: SqlAlchemyReleaseRepository,
) -> None:
    cap_id = uid("cap")
    seed_two_versions(capability_repo, cap_id)
    release_repo.set_release(
        CapabilityRelease(capability_id=cap_id, version="1.0.0", channel="staging", status="active")
    )
    release_repo.set_release(
        CapabilityRelease(
            capability_id=cap_id, version="2.0.0", channel="production", status="active"
        )
    )
    channels = [(r.channel, r.version) for r in release_repo.list_releases(cap_id)]
    assert channels == [("production", "2.0.0"), ("staging", "1.0.0")]
    assert release_repo.get_release(cap_id, "candidate") is None
    assert capability_repo.get_capability(uid("ghost")) is None


def test_binding_and_artifact_reput(
    capability_repo: SqlAlchemyCapabilityRepository,
    artifact_store: SqlAlchemyArtifactStore,
) -> None:
    cap_id = uid("cap")
    seed_two_versions(capability_repo, cap_id)

    binding = CapabilityBinding(
        binding_id=f"bind-{cap_id}",
        capability_id=cap_id,
        version="1.0.0",
        binding_type="opencode-http-skill",
        config={},
    )
    capability_repo.put_binding(binding)
    capability_repo.put_binding(
        CapabilityBinding(
            binding_id=f"bind-{cap_id}",
            capability_id=cap_id,
            version="2.0.0",
            binding_type="opencode-http-skill",
            config={"skill_id": cap_id},
        )
    )
    got = capability_repo.get_binding(f"bind-{cap_id}")
    assert got is not None and got.version == "2.0.0"

    artifact = CapabilityArtifact(capability_id=cap_id, version="1.0.0", package_digest=DIGEST)
    artifact_store.put_artifact(artifact)
    new_digest = "sha256:" + "cd" * 32
    artifact_store.put_artifact(
        CapabilityArtifact(capability_id=cap_id, version="1.0.0", package_digest=new_digest)
    )
    got_artifact = artifact_store.get_artifact(cap_id, "1.0.0")
    assert got_artifact is not None and got_artifact.package_digest == new_digest


def test_binding_and_artifact_roundtrip(
    capability_repo: SqlAlchemyCapabilityRepository,
    artifact_store: SqlAlchemyArtifactStore,
) -> None:
    cap_id = uid("cap")
    seed_two_versions(capability_repo, cap_id)

    binding = CapabilityBinding(
        binding_id=f"bind-{cap_id}",
        capability_id=cap_id,
        version="1.0.0",
        binding_type="opencode-http-skill",
        config={"skill_id": cap_id, "autoinvoke": False},
    )
    capability_repo.put_binding(binding)
    got = capability_repo.get_binding(f"bind-{cap_id}")
    assert got == binding
    assert capability_repo.get_binding("bind-missing") is None

    artifact = CapabilityArtifact(
        capability_id=cap_id,
        version="1.0.0",
        package_digest=DIGEST,
        manifest={"entrypoint": "SKILL.md"},
        files=[ArtifactFile(path="SKILL.md", sha256="ab" * 32, size_bytes=128)],
    )
    artifact_store.put_artifact(artifact)
    assert artifact_store.get_artifact(cap_id, "1.0.0") == artifact
    assert artifact_store.get_artifact(cap_id, "2.0.0") is None
