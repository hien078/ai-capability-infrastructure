"""§61 acquisition-era boundaries — what automation may never expose.

ADR-013's load-bearing claims, enforced as tests:

- Staging is NOT exposure: a release on the staging channel is invisible
  to every client surface (OpenCode catalog, REST search, MCP skills,
  eligibility). Automation may auto-stage; nothing can auto-expose.
- Canary is fail-closed: a canary without its percentage routes NOTHING,
  never everything; the split is deterministic per (request, capability).
- The auto-approve tier boundary: unreviewed sources are HELD (no
  automatic advance at all); known sources advance only to
  approved_for_fetch — the fetch/quarantine/gates pipeline is the only
  path onward, and promotion stays human.
- Candidate content is structurally unreachable from routing: routing
  does not import the acquisition domain, and the candidate wire models
  carry no permission/tool surface (§61 case 3 discipline).
"""

import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aci.adapters.inbound.mcp.skills import SkillCatalog
from aci.adapters.inbound.rest.wiring import Container
from aci.config import Settings
from aci.control_plane.promotion.service import PromotionService
from aci.domain.policy.models import EligibleCandidate, PolicyRules, RoutingRequestContext
from aci.main import create_app
from aci.providers.skills.ingestion import SkillIngestionService
from aci.routing.eligibility import DefaultEligibilityPolicy

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 29, tzinfo=UTC)
DB_URL = "postgresql+psycopg://aci:aci@localhost:5432/aci"
REPO_ROOT = Path(__file__).resolve().parents[2]


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def write_skill(tmp_path: Path, name: str, description: str, body: str) -> Path:
    src = tmp_path / name
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nversion: 1.0.0\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return src


def test_staging_release_invisible_to_every_client_surface(
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    tmp_path: Path,
) -> None:
    """ADR-013: automation may move a candidate into STAGING — staging must
    then be invisible to runtime. Every client surface reads production
    only; a staging release is not exposure by construction."""

    name = uid("sec-staging")
    src = write_skill(tmp_path, name, "calibrates resonance manifolds", "harmless body")
    result = ingestion.ingest_local(src, now=NOW)
    cap = result.capability_id

    # Pre-production channel needs only the version (ADR-012) — this is
    # exactly what auto-staging ends at.
    promotion.promote(cap, "1.0.0", "staging", approved_by="auto-approve:known-tier", now=NOW)

    settings = Settings(database_url=DB_URL, object_store_root=str(tmp_path / "objects"))
    app = create_app(Container(settings))
    with TestClient(app) as client:
        # OpenCode catalog: production-active only.
        index = client.get("/opencode/skills/index.json").json()
        assert cap not in {e["name"] for e in index["skills"]}
        missing = client.get(f"/opencode/skills/{cap}/{cap}.md")
        assert missing.status_code == 404

        # REST search: discovery-only, production-active listing.
        found = client.post(
            "/v1/capabilities/search",
            json={"query": name, "filters": {"kinds": [], "domains": []}, "limit": 50},
        ).json()
        assert cap not in {c["capability_id"] for c in found}

        # MCP skills: same projection rules (§28.2), production only.
        container = app.state.container
        catalog = SkillCatalog(
            container.releases, container.capabilities, container.artifacts, container.objects
        )
        assert cap not in {e.skill_id for e in catalog.entries()}

    # Eligibility: a staging-channel candidate never routes (§15 channel
    # check — the routing-side half of "staging is not exposure").
    staging_candidate = EligibleCandidate.model_validate(
        {
            "capability_id": cap,
            "version": "1.0.0",
            "digest": "sha256:" + "ab" * 32,
            "kind": "skill",
            "channel": "staging",
            "status": "active",
        }
    )
    context = RoutingRequestContext.model_validate(
        {
            "client": {"type": "test", "supported_features": ["skills"]},
            "scope": {"principal_id": "p-1"},
            "request_id": "req-staging",
        }
    )
    decision = DefaultEligibilityPolicy().filter(
        [staging_candidate], context, PolicyRules(), allowed_kinds=["skill"]
    )
    assert decision.kept == []
    assert decision.excluded[0].reason == "CAPABILITY_NOT_ELIGIBLE"


def test_canary_fail_closed_and_deterministic() -> None:
    """ADR-013 §4: a canary without its percentage is a broken release
    record and routes NOTHING (fail-closed); with a percentage the split
    is deterministic — same request, same decision, always."""

    def candidate(**overrides: object) -> EligibleCandidate:
        base: dict = {
            "capability_id": "canary-cap",
            "version": "2.0.0",
            "digest": "sha256:" + "ab" * 32,
            "kind": "skill",
            "channel": "production",
            "status": "active",
        }
        base.update(overrides)
        return EligibleCandidate.model_validate(base)

    def context(request_id: str) -> RoutingRequestContext:
        return RoutingRequestContext.model_validate(
            {
                "client": {"type": "test", "supported_features": ["skills"]},
                "scope": {"principal_id": "p-1"},
                "request_id": request_id,
            }
        )

    policy = DefaultEligibilityPolicy()
    rules = PolicyRules()

    # Broken record: canary status without canary_percent → excluded.
    broken = candidate(status="canary", canary_percent=None)
    decision = policy.filter([broken], context("r-1"), rules, allowed_kinds=["skill"])
    assert decision.kept == []
    assert decision.excluded[0].reason == "CAPABILITY_NOT_ELIGIBLE"
    assert "fail-closed" in decision.excluded[0].detail

    # Deterministic split: the same (request, capability) pair always gets
    # the same decision across repeated filters — reproducible canary
    # populations (§27 telemetry comparison depends on this).
    canary = candidate(status="canary", canary_percent=50)
    verdicts = set()
    for _ in range(8):
        d = policy.filter([canary], context("r-fixed"), rules, allowed_kinds=["skill"])
        verdicts.add("kept" if d.kept else "excluded")
    assert len(verdicts) == 1


def test_auto_approve_holds_unreviewed_and_ceils_at_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-013 §1-2: the tier boundary of the weekly cycle.

    An UNREVIEWED-source candidate is HELD — no automatic advance at all.
    A KNOWN-source candidate advances only to approved_for_fetch: the
    script's ceiling is fetch approval; quarantine, gates, and the human
    promotion gate are all downstream and untouched by automation.
    """

    import scripts.auto_approve as auto_approve

    store = tmp_path / "candidates"
    store.mkdir()
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "\n".join(
            [
                "sources:",
                "  - repo: https://github.com/anthropics/skills",
                "    trust: known",
                "    acquisition_policy:",
                "      require_human_source_approval: false",
                "  - repo: https://github.com/random-stranger/skills",
                "    trust: unreviewed",
                "    acquisition_policy:",
                "      require_human_source_approval: true",
            ]
        ),
        encoding="utf-8",
    )

    def record(candidate_id: str, repo: str) -> dict:
        return {
            "proposal": {
                "candidate_id": candidate_id,
                "source_repo": repo,
                "source_path": "skills/whatever",
                "observed_revision": "abc123",
                "reason": "fills a measured demand gap",
                "signals": {"has_skill_md": True},
                "discovery_confidence": 0.7,
                "proposed_at": NOW.isoformat(),
                "proposed_by": "scout",
            },
            "status": "proposed",
            "decided_by": "scout",
        }

    (store / "known.json").write_text(
        json.dumps(record("c-known", "https://github.com/anthropics/skills")), encoding="utf-8"
    )
    (store / "stranger.json").write_text(
        json.dumps(record("c-stranger", "https://github.com/random-stranger/skills")),
        encoding="utf-8",
    )

    monkeypatch.setattr(auto_approve, "CANDIDATE_ROOT", store)
    monkeypatch.setattr(auto_approve, "SOURCES_YAML", sources)
    monkeypatch.setattr(sys, "argv", ["auto_approve.py"])
    assert auto_approve.main() == 0

    known = json.loads((store / "known.json").read_text(encoding="utf-8"))
    stranger = json.loads((store / "stranger.json").read_text(encoding="utf-8"))

    # Known tier: advanced to the fetch ceiling — and NOT beyond.
    assert known["status"] == "approved_for_fetch"
    assert [step[0] for step in known["history"]] == ["source_approved", "approved_for_fetch"]
    assert all("auto-approve:known-tier" == step[1] for step in known["history"])

    # Unreviewed tier: HELD at proposed — zero automatic advance (the
    # script never even rewrites the record).
    assert stranger["status"] == "proposed"
    assert stranger.get("history", []) == []


def test_candidate_content_structurally_unreachable_from_routing() -> None:
    """§61 case 3 discipline, acquisition edition: candidate proposals are
    scout output about EXTERNAL repos — they are not registry content.
    Routing must not even know the acquisition domain exists (import
    boundary), and the candidate wire models carry no permission/tool
    surface that a crawled body could hope to influence."""

    from aci.domain.acquisition.models import CandidateProposal, CandidateRecord

    # Import boundary: no routing module may import the acquisition domain.
    routing_dir = REPO_ROOT / "src" / "aci" / "routing"
    offenders = [
        p.relative_to(REPO_ROOT).as_posix()
        for p in routing_dir.rglob("*.py")
        if "acquisition" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"routing must not know the acquisition domain: {offenders}"

    # Wire-surface boundary: the proposal/record models carry evidence
    # fields only — no permissions, tools, or config surface.
    proposal_fields = set(CandidateProposal.model_fields)
    assert proposal_fields == {
        "candidate_id",
        "gap_id",
        "source_repo",
        "source_path",
        "observed_revision",
        "candidate_type",
        "reason",
        "signals",
        "discovery_confidence",
        "recommended_action",
        "proposed_at",
        "proposed_by",
    }
    for forbidden in ("permissions", "tools", "config", "allowed_tools", "execute"):
        assert forbidden not in proposal_fields
        assert forbidden not in set(CandidateRecord.model_fields)
