"""Set up the EXPERIMENT registry copy for the E2B/E2C arms (Bp/Bq) — 2026-10-02.

Job e2b-routed-vs-file measures whether ACI's registry + §14 router adds
value over the simplest alternative (the standard as a plain document in the
repo). Arms Bp/Bq route through the REAL registry plane pointed at a
DISPOSABLE experiment copy of the operational registry (e.g. aci_e2b — a
pg_dump of aci_bench) with the private skills ingested alongside the real
corpus.

This script ingests + gates + promotes those private skills IN THE
EXPERIMENT COPY ONLY, through the REAL paths — nothing is bypassed:

    package (SKILL.md as-is + LICENSE) -> content-addressed blobs
      -> SkillIngestionService.ingest_local (providers/skills — the same
         path any skill takes; quarantined provenance record)
      -> license assessment (§24: detect_license on the package — the
         LICENSE file is the evidence; first-party internal standard,
         human-vouched on the lead's authorization for this experiment)
      -> pattern security scan (§25)
      -> IngestionGateService.accept (G2-G4: quarantined -> accepted)
      -> PromotionService.promote staging -> production (G5-G8: provenance
         chain, redistributable license, passed scan — every gate printed)

Sets (--set): 'private' (default) = the 2 E2B skills (scripts/private_tasks.py
— idempotent if they are already ingested); 'private2' = the 7 E2C skills
(scripts/private2_tasks.py, the four builders' fictional internal standards —
same faithful first-party MIT license path, lead-authorized for production
promotion IN THE EXPERIMENT COPY ONLY).

It then indexes them the way the server does (the Container's EmbeddingRetriever
indexes lazily on the first route query — fastembed, the same embedder the
REST server runs) and prints the ROUTER'S EX-ANTE HIT CHECK: a direct route
query with each case's objective text through the Container's route service
(plus the kernel-path preview through a fresh RegistryCapabilityClient —
what arm Bp will actually preload). A MISS IS A RESULT: the script never
tunes anything to make routing succeed — the fixture skill text is used
AS-IS (hand-tuning descriptions/summaries would game the router).

REFUSES (exit 2) any --database-url whose database name is `aci` (dev/test)
or `aci_bench` (operational) — the experiment must never touch either.

Usage:
    .venv/bin/python scripts/e2b_setup_registry.py \
        --database-url postgresql+psycopg://aci:aci@<host>:5432/aci_e2b \
        --object-store-root ~/aci-mac/e2b-objects [--set private|private2]

Idempotent: identical content re-ingests as a no-op, assessments are not
duplicated, promotion pointer moves are pointer-only.
"""

import argparse
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from private2_tasks import PRIVATE2_TASKS  # noqa: E402
from private_tasks import PRIVATE_TASKS  # noqa: E402

from aci.adapters.outbound.object_store.fs import FsObjectStore  # noqa: E402
from aci.adapters.outbound.postgres.assessments import (  # noqa: E402
    SqlAlchemyLicenseAssessmentRepository,
    SqlAlchemySecurityAssessmentRepository,
)
from aci.adapters.outbound.postgres.base import make_engine, make_session_factory  # noqa: E402
from aci.adapters.outbound.postgres.repositories import (  # noqa: E402
    SqlAlchemyArtifactStore,
    SqlAlchemyCapabilityRepository,
    SqlAlchemyReleaseRepository,
)
from aci.adapters.outbound.postgres.source_records import (  # noqa: E402
    SqlAlchemySourceRecordRepository,
)
from aci.config import Settings  # noqa: E402
from aci.control_plane.ingestion_gates.service import IngestionGateService  # noqa: E402
from aci.control_plane.promotion.service import PromotionService  # noqa: E402
from aci.domain.capability.models import (  # noqa: E402
    DEFAULT_MAX_CONTEXT_TOKENS,
    RouteCapabilitiesCommand,
    TaskContext,
)
from aci.domain.policy.models import (  # noqa: E402
    ClientDescriptor,
    ProtocolDescriptor,
    RequestContext,
)
from aci.domain.provenance.models import (  # noqa: E402
    LicenseAssessment,
    ScanStatus,
    SecurityAssessment,
)
from aci.providers.licensing import detect_license, spdx_permissions  # noqa: E402
from aci.providers.security import SCANNER_VERSION, scan_package, verdict  # noqa: E402
from aci.providers.skills.ingestion import SkillIngestionService  # noqa: E402
from aci.routing.retrieval import build_trusted_document  # noqa: E402

#: The databases this script must NEVER write to (the experiment copy is the
#: only acceptable target — same rule as run_hbench's Bp/Bq refusal).
OPERATIONAL_DATABASES = frozenset({"aci", "aci_bench"})

#: First-party internal standard, authored for this experiment: the org's own
#: content, licensed MIT exactly like the repo's other first-party capability
#: (register_agent.py's aci-coder default). The LICENSE file travels IN the
#: package so detect_license() finds it from evidence, not an override.
_LICENSE_MIT = """MIT License

Copyright (c) 2026 The ACI platform team (internal standard, authored for
the e2b experiment)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

_APPROVED_BY = "e2b-setup-registry.py (lead-authorized: disposable experiment copy only)"

#: Provenance labels per set: the private2 skills are authored for the E2C
#: experiment — the same faithful first-party path, a distinct source record.
_SOURCE_LABELS: dict[str, tuple[str, str]] = {
    "private": (
        "aci/e2b-private-standards",
        "first-party internal standard (authored for the e2b experiment)",
    ),
    "private2": (
        "aci/e2c-private-standards",
        "first-party internal standard (authored for the e2c experiment)",
    ),
}


def selected_tasks(set_name: str) -> list[dict[str, Any]]:
    """The fixture set to ingest: 'private' = the 2 E2B skills (default),
    'private2' = the 7 E2C skills."""
    if set_name == "private2":
        return list(PRIVATE2_TASKS)
    return list(PRIVATE_TASKS)


def database_name_from_url(url: str) -> str:
    """The database name from a SQLAlchemy URL (the last path segment)."""
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]


def refuse_operational_database(url: str) -> str | None:
    """None when this script may write ``url``; otherwise the refusal reason
    (the script exits 2 on it — fail closed, never a warning)."""
    name = database_name_from_url(url)
    if name in OPERATIONAL_DATABASES:
        return (
            f"refusing to write the database {name!r} — aci/aci_bench are the "
            "dev-test and OPERATIONAL databases; this script ingests into a "
            "DISPOSABLE experiment copy only (e.g. aci_e2b)"
        )
    return None


def materialize_package(task: dict[str, Any], root: Path) -> Path:
    """The private skill's package directory: SKILL.md is the fixture's skill
    text VERBATIM (never hand-tuned — that would game the router) plus the
    first-party MIT LICENSE file (the §24 evidence)."""
    skill_id = str(task["skill_id"])
    package = root / skill_id
    package.mkdir(parents=True, exist_ok=True)
    (package / "SKILL.md").write_text(str(task["skill"]), encoding="utf-8")
    (package / "LICENSE").write_text(_LICENSE_MIT, encoding="utf-8")
    return package


def _new_id() -> str:
    return uuid4().hex[:12]


def ingest_one(
    task: dict[str, Any],
    *,
    ingestion: SkillIngestionService,
    gates: IngestionGateService,
    promotion: PromotionService,
    license_repo: SqlAlchemyLicenseAssessmentRepository,
    security_repo: SqlAlchemySecurityAssessmentRepository,
    capabilities: SqlAlchemyCapabilityRepository,
    workdir: Path,
    now: datetime,
    source_repository: str = "aci/e2b-private-standards",
    source_url_reference: str = "first-party internal standard (authored for the e2b experiment)",
    experiment_label: str = "e2b",
) -> dict[str, Any]:
    """Ingest + gate + promote ONE private skill through the real paths.
    Returns what happened (for the summary printout)."""
    skill_id = str(task["skill_id"])
    package = materialize_package(task, workdir)

    # §24 license: detected from the package (the LICENSE file is the
    # evidence — first-party MIT, the same license the repo's own
    # first-party capability carries).
    det = detect_license(package)
    if det.spdx_id == "unknown":
        raise SystemExit(
            f"FAIL {skill_id}: license detection found {det.spdx_id!r} "
            f"({det.method} {det.evidence}) — the first-party MIT LICENSE file "
            "was not recognized; refusing to guess"
        )
    permissions = spdx_permissions(det.spdx_id)

    # The REAL ingestion path (parse -> hash -> blobs -> version/artifact ->
    # quarantined provenance record). The fixture skill text is used AS-IS.
    result = ingestion.ingest_local(
        package,
        license_identifier=det.spdx_id,
        source_repository=source_repository,
        source_url_reference=source_url_reference,
        commit_sha=None,
        now=now,
    )
    cid, ver = result.capability_id, result.version
    state = "already-ingested" if result.already_ingested else "ingested"
    print(
        f"OK  {cid}@{ver} [{state}] license={det.spdx_id} ({det.method}) files={result.file_count}"
    )

    if license_repo.get_assessment(cid, ver) is None:
        license_repo.put_assessment(
            LicenseAssessment(
                assessment_id=f"lic-{_new_id()}",
                capability_id=cid,
                version=ver,
                license_identifier=det.spdx_id,
                permissions=permissions,
                assessed_at=now,
                assessed_by="e2b-setup:platform-owner",
                notes=(
                    f"first-party internal standard authored for the {experiment_label} "
                    f"experiment; detected from package {det.evidence}"
                ),
            )
        )
    if security_repo.get_assessment(cid, ver) is None:
        findings = scan_package(package)
        scan_verdict: ScanStatus = verdict(findings)
        security_repo.put_assessment(
            SecurityAssessment(
                assessment_id=f"sec-{_new_id()}",
                capability_id=cid,
                version=ver,
                scan_status=scan_verdict,
                findings=[str(f) for f in findings],
                scanned_at=now,
                scanner_version=SCANNER_VERSION,
                reviewed_by="pattern-scanner",
            )
        )
        print(f"    scan: {scan_verdict} ({len(findings)} finding(s))")

    # INGESTION GATES (G2-G4): quarantined -> accepted, or FAIL loudly.
    if not gates.accept(cid, ver):
        for check in gates.evaluate(cid, ver)[0]:
            print(f"    gate {check.name}: {'PASS' if check.passed else 'FAIL'} — {check.detail}")
        raise SystemExit(f"FAIL {cid}@{ver}: ingestion gates did not pass — stays QUARANTINED")

    # STAGING then PRODUCTION through the normal promotion service (every
    # production gate printed; a failed gate raises POLICY_DENIED).
    promotion.promote(cid, ver, "staging", approved_by=_APPROVED_BY, now=now)
    for check in promotion.prerequisites(cid, ver, "production"):
        print(f"    gate {check.name}: {'PASS' if check.passed else 'FAIL'} — {check.detail}")
    promotion.promote(cid, ver, "production", approved_by=_APPROVED_BY, now=now)
    print(f"OK  {cid}@{ver} [production] (staging + production through the real gates)")

    # The trusted routing summary the ingestion produced (recorded, never
    # tuned): name/description/provides from the version metadata AS-IS.
    version = capabilities.get_version(cid, ver)
    if version is None:
        raise SystemExit(f"FAIL {cid}@{ver}: version vanished after ingestion")
    document = build_trusted_document(version, model_id="fastembed:BAAI/bge-small-en-v1.5", now=now)
    print(f"    trusted routing document ({len(document.text)} chars):")
    for line in document.text.splitlines():
        print(f"      | {line}")
    return {
        "capability_id": cid,
        "version": ver,
        "expected_id": skill_id,
        "license": det.spdx_id,
        "trusted_document": document.text,
    }


def exante_check(container: Any, task: dict[str, Any]) -> dict[str, Any]:
    """The router's EX-ANTE hit check for one case: a direct route query with
    the case's objective text through the Container's route service (the raw
    §14 bundle, rank order), plus the kernel-path preview through a fresh
    RegistryCapabilityClient (what arm Bp will actually preload after the
    kernel-side narrowing). Also triggers the lazy indexing (the same
    embedder/indexing path the server uses). A MISS IS A RESULT — nothing is
    tuned here."""
    from aci.adapters.outbound.agent_capabilities import CLIENT_TYPE, PROTOCOL_TYPE
    from aci.domain.runtime.actions import CapabilityRequest

    skill_id = str(task["skill_id"])
    objective = str(task["prompt"])

    command = RouteCapabilitiesCommand(
        task_text=objective[:8000],
        context=TaskContext(),
        max_items=5,
        max_context_tokens=DEFAULT_MAX_CONTEXT_TOKENS,
        allowed_kinds=["skill"],
    )
    envelope = RequestContext(
        request_id=f"req_{uuid4().hex}",
        trace_id=f"trc_{uuid4().hex}",
        principal_id="harness-kernel",
        client=ClientDescriptor(type=CLIENT_TYPE),
        protocol=ProtocolDescriptor(type=PROTOCOL_TYPE, version="1"),
    )
    result = container.route_service.route(
        command, envelope.to_routing_context(command.context), request=envelope
    )
    routed = [item.capability_id for item in result.bundle.items]
    routed_rank = next(
        (position for position, cid in enumerate(routed, start=1) if cid == skill_id), None
    )
    print(f"    route bundle (rank order): {routed}")
    print(f"    {skill_id} routed rank: {routed_rank}")

    # The kernel path (arm Bp's exact preload): route + kernel-side narrowing.
    client = container.agent_capability_clients()
    selections = client.search(CapabilityRequest(objective=objective, constraints=[]))
    kept = [f"{s.capability_id}@{s.version}" for s in selections]
    decision = client.decisions[-1] if client.decisions else None
    kept_rank = next(
        (
            position
            for position, entry in enumerate(decision.kept if decision else [], start=1)
            if entry.capability_id == skill_id
        ),
        None,
    )
    dropped = [f"{e.capability_id}:{e.reason}" for e in (decision.dropped if decision else [])]
    print(f"    kernel preload (Bp preview): {kept}")
    if dropped:
        print(f"    kernel narrowed out: {dropped}")
    print(f"    {skill_id} kept rank: {kept_rank}")
    return {
        "routed_bundle": routed,
        "routed_rank": routed_rank,
        "kernel_kept": kept,
        "kernel_kept_rank": kept_rank,
        "kernel_dropped": dropped,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        required=True,
        help="the EXPERIMENT registry database (a disposable copy, e.g. aci_e2b)",
    )
    parser.add_argument(
        "--object-store-root",
        required=True,
        help=(
            "the experiment copy's object-store root (must be the SAME root the "
            "runner's --object-store-root names — blobs are content-addressed"
        ),
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="where to materialize the skill packages (default: a temp dir)",
    )
    parser.add_argument(
        "--set",
        default="private",
        choices=["private", "private2"],
        help=(
            "which private skills to ingest: 'private' = the 2 E2B skills "
            "(default), 'private2' = the 7 E2C skills (the four builders' "
            "fictional internal standards)"
        ),
    )
    args = parser.parse_args(argv)

    refusal = refuse_operational_database(args.database_url)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return 2

    tasks = selected_tasks(args.set)
    source_repository, source_url_reference = _SOURCE_LABELS[args.set]
    experiment_label = "e2b" if args.set == "private" else "e2c"
    print(f"set: {args.set} ({len(tasks)} private skill(s) to ingest)")
    settings = Settings(
        database_url=args.database_url,
        object_store_root=str(Path(args.object_store_root).expanduser()),
        embedder="fastembed",
    )
    print(f"experiment registry: database={database_name_from_url(args.database_url)!r}")
    print(f"object store root: {settings.object_store_root}")
    engine = make_engine(settings.database_url)
    sessions = make_session_factory(engine)

    capabilities = SqlAlchemyCapabilityRepository(sessions)
    releases = SqlAlchemyReleaseRepository(sessions)
    artifacts = SqlAlchemyArtifactStore(sessions)
    source_records = SqlAlchemySourceRecordRepository(sessions)
    objects = FsObjectStore(Path(settings.object_store_root))
    license_repo = SqlAlchemyLicenseAssessmentRepository(sessions)
    security_repo = SqlAlchemySecurityAssessmentRepository(sessions)

    ingestion = SkillIngestionService(
        capabilities=capabilities,
        releases=releases,
        artifacts=artifacts,
        source_records=source_records,
        objects=objects,
    )
    gates = IngestionGateService(
        source_records=source_records, licenses=license_repo, securities=security_repo
    )
    promotion = PromotionService(
        capabilities=capabilities,
        releases=releases,
        source_records=source_records,
        licenses=license_repo,
        securities=security_repo,
    )

    now = datetime.now(UTC)
    ingested: list[dict[str, Any]] = []
    workdir = args.workdir.expanduser() if args.workdir is not None else None
    with tempfile.TemporaryDirectory(prefix="e2b-packages-") as tmp:
        root = workdir if workdir is not None else Path(tmp)
        root.mkdir(parents=True, exist_ok=True)
        for task in tasks:
            print(f"\n=== {task['name']} -> {task['skill_id']} ===")
            record = ingest_one(
                task,
                ingestion=ingestion,
                gates=gates,
                promotion=promotion,
                license_repo=license_repo,
                security_repo=security_repo,
                capabilities=capabilities,
                workdir=root,
                now=now,
                source_repository=source_repository,
                source_url_reference=source_url_reference,
                experiment_label=experiment_label,
            )
            ingested.append(record)

    # Sanity: the canonical capability id must equal the fixture's skill id
    # (the frontmatter name IS the id — no silent rename).
    for record in ingested:
        if record["capability_id"] != record["expected_id"]:
            print(
                f"WARNING: canonical id {record['capability_id']!r} != fixture skill id "
                f"{record['expected_id']!r} (name normalization applied)",
                file=sys.stderr,
            )

    # Index + the router's ex-ante hit check, through the SAME Container
    # composition the runner's Bp/Bq arms use (fastembed semantics).
    print("\n=== ex-ante router check (Container route service + kernel path) ===")
    from aci.adapters.inbound.rest.wiring import Container

    container = Container(settings)
    exante: dict[str, Any] = {}
    for task in tasks:
        print(f"\n--- {task['name']} ---")
        exante[str(task["name"])] = exante_check(container, task)

    print("\n=== summary ===")
    for record in ingested:
        print(
            f"  {record['capability_id']}@{record['version']} production "
            f"(license {record['license']}) in {database_name_from_url(args.database_url)!r}"
        )
    for name, check in exante.items():
        verdict = "HIT" if check["kernel_kept_rank"] is not None else "MISS"
        print(
            f"  {name}: ex-ante {verdict} — routed rank {check['routed_rank']}, "
            f"kernel kept rank {check['kernel_kept_rank']}"
        )
    print(
        f"\nAll {len(ingested)} private skill(s) of set {args.set!r} ingested, gated, "
        "promoted to production (in the experiment copy only) and indexed. "
        "A MISS above IS a result — do not change anything to make routing succeed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
