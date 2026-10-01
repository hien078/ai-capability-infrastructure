"""E2B arm tests (job e2b-routed-vs-file) — the new arms' fairness pins.

Arms F/Bp/Bq extend the H-bench runner for the "registry+router vs
docs-in-repo" question. These pins keep the comparison honest WITHOUT a DB
or network:

* F is arm K's wiring EXACTLY plus ONE plain repo document in the workspace
  (the case's skill text at docs/standards/<skill_id>.md) — nothing in
  context, no hint in the objective; K's workspace has no such file.
* Bp/Bq dispatch through the EXPERIMENT registry container with the preload
  ON/OFF respectively, and the runner REFUSES (exit 2) to point them at
  aci/aci_bench or run them without the experiment registry flags.
* The setup script refuses aci/aci_bench the same way.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import e2b_setup_registry  # noqa: E402
import run_hbench  # noqa: E402
from private_tasks import PRIVATE_TASKS  # noqa: E402

from aci.domain.runtime.actions import ContinueAction  # noqa: E402
from aci.runtime.model_gateway import FakeModelGateway  # noqa: E402
from tests.sandbox_support import available_sandbox  # noqa: E402


def _materialize(fixture: dict[str, Any], target: Path) -> Path:
    task_dir = target / str(fixture["name"])
    task_dir.mkdir(parents=True, exist_ok=True)
    for rel, content in fixture["files"].items():
        path = task_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return task_dir


class TestFileArm:
    """Arm F: the case's skill text is a plain repo DOCUMENT in the run
    workspace — exactly one file, no capability plane, no context injection,
    no hint in the objective. F−K must isolate document-on-disk only."""

    def test_docs_path_is_the_skill_id(self) -> None:
        assert run_hbench.file_arm_docs_path("atlas-error-standard") == (
            "docs/standards/atlas-error-standard.md"
        )

    def test_materialize_writes_fixture_files_plus_exactly_one_doc(self, tmp_path: Path) -> None:
        fixture = PRIVATE_TASKS[0]
        task_dir = run_hbench.materialize_file_arm_source(fixture, tmp_path)
        # The fixture files are byte-identical to every other arm's...
        for rel, content in fixture["files"].items():
            assert (task_dir / rel).read_text(encoding="utf-8") == content
        # ...plus EXACTLY ONE document carrying the skill text verbatim.
        docs = [p for p in task_dir.rglob("docs/standards/*") if p.is_file()]
        assert [p.relative_to(task_dir).as_posix() for p in docs] == [
            run_hbench.file_arm_docs_path(str(fixture["skill_id"]))
        ]
        assert docs[0].read_text(encoding="utf-8") == fixture["skill"]

    def test_f_run_workspace_carries_the_doc_k_workspace_does_not(self, tmp_path: Path) -> None:
        """A FULL run per arm over fakes: after an F run the run workspace
        contains the plain document (and the fixture files); after a K run
        the workspace contains NO docs/ file at all."""
        fixture = PRIVATE_TASKS[0]
        sources = tmp_path / "src"
        _materialize(fixture, sources)
        sources_f = tmp_path / "sources-f"
        run_hbench.materialize_file_arm_source(fixture, sources_f)
        runs = tmp_path / "runs"

        f_contract, f_spec = run_hbench._contract_spec(fixture)
        f_record = run_hbench.run_file_arm(
            fixture,
            f_contract,
            f_spec,
            FakeModelGateway([ContinueAction()]),
            runs,
            max_turns=1,
            sources_f=sources_f,
            sandbox=available_sandbox(),
        )
        k_contract, k_spec = run_hbench._contract_spec(fixture)
        k_record = run_hbench.run_kernel_arm(
            fixture,
            k_contract,
            k_spec,
            FakeModelGateway([ContinueAction()]),
            sources,
            runs,
            max_turns=1,
            sandbox=available_sandbox(),
        )
        f_dir = runs / f_record["run_id"]
        k_dir = runs / k_record["run_id"]
        assert f_dir.is_dir() and k_dir.is_dir()

        # F: the document is IN the workspace, byte-identical to the skill.
        docs = run_hbench.file_arm_docs_path(str(fixture["skill_id"]))
        assert (f_dir / docs).read_text(encoding="utf-8") == fixture["skill"]
        # ...and it is the ONLY docs/ file F added.
        assert [
            p.relative_to(f_dir).as_posix() for p in (f_dir / "docs").rglob("*") if p.is_file()
        ] == [docs]
        # K: no docs/ file at all.
        assert not (k_dir / "docs").exists()
        assert list(k_dir.rglob("*.py")) and list(f_dir.rglob("*.py"))

        # The records: F is arm K's wiring plus the doc — no capability plane.
        assert f_record["arm"] == "F" and k_record["arm"] == "K"
        assert f_record["docs_file"] == docs
        assert f_record["capability_requests"] == 0
        assert f_record["skills_loaded"] == []
        assert f_record.get("skills_preloaded", []) == []

    def test_f_first_request_carries_no_skill_text(self, tmp_path: Path) -> None:
        """The whole F−R difference: F's FIRST ModelRequest contains NO skill
        text (the document is on disk, not in context) — R's does. F must not
        accidentally become a preload arm."""
        fixture = PRIVATE_TASKS[0]
        sources_f = tmp_path / "sources-f"
        run_hbench.materialize_file_arm_source(fixture, sources_f)
        gateway = FakeModelGateway([ContinueAction()])
        contract, spec = run_hbench._contract_spec(fixture)
        run_hbench.run_file_arm(
            fixture,
            contract,
            spec,
            gateway,
            tmp_path / "runs",
            max_turns=1,
            sources_f=sources_f,
            sandbox=available_sandbox(),
        )
        first = gateway.requests[0].messages
        assert all("Atlas Error Handling Standard" not in m.content for m in first)
        # No capability tool offered either — F is K's null plane.
        assert "request_capability" not in [t.tool_id for t in gateway.requests[0].tools]

    def test_f_needs_its_source_root(self, tmp_path: Path) -> None:
        """_run_one refuses arm F without the materialized source root —
        never a silent fallback to the plain (doc-less) sources."""
        fixture = PRIVATE_TASKS[0]
        import pytest

        with pytest.raises(ValueError, match="sources_f"):
            run_hbench._run_one(
                fixture,
                "F",
                "http://gateway.invalid/v1",
                "some-model",
                "key",
                tmp_path / "src",
                tmp_path / "runs",
                max_turns=1,
            )


class _RecordingClient:
    """A fake run-scoped registry client for the Bp/Bq dispatch pins: one
    decision with a kept private skill (rank 1) and one dropped distractor."""

    class _Entry:
        def __init__(self, capability_id: str, reason: str) -> None:
            self.capability_id = capability_id
            self.version = "1.0.0"
            self.score = 0.5
            self.reason = reason

    def __init__(self) -> None:
        entry = self._Entry("atlas-error-standard", "kept")
        dropped = self._Entry("some-distractor", "score_margin")
        self.decisions = [SimpleNamespace(kept=(entry,), dropped=(dropped,), scores_available=True)]

    def search(self, request: object) -> list[object]:  # noqa: ARG002
        return []

    def resolve(self, capability_id: str, version: str) -> tuple[bytes, str]:  # noqa: ARG002
        return b"", f"sha256:{'0' * 64}"


class TestRegistryArms:
    """Arms Bp/Bq: the dispatch wiring (preload ON/OFF, the EXPERIMENT
    container used) and the private-skill evidence read-out."""

    def test_dispatch_bp_preloads_bq_does_not(self, tmp_path: Path, monkeypatch: Any) -> None:
        """`_run_one` routes Bp to run_registry_arm with the preload ON and
        Bq with the preload OFF (today's product default) — the ONLY difference
        between the two dispatches is the arm label + the flag."""
        calls: list[dict[str, Any]] = []

        def _record_call(*args: object, **kwargs: object) -> dict[str, Any]:
            calls.append({"args": args, "kwargs": kwargs})
            return {}

        monkeypatch.setattr(run_hbench, "run_registry_arm", _record_call)
        fixture = PRIVATE_TASKS[0]
        container = object()
        for arm in ("Bp", "Bq"):
            run_hbench._run_one(
                fixture,
                arm,
                "http://gateway.invalid/v1",
                "some-model",
                "key",
                tmp_path / "src",
                tmp_path / "runs",
                max_turns=3,
                registry_container=container,
            )
        (bp_call, bq_call) = calls
        assert bp_call["kwargs"]["arm"] == "Bp"
        assert bp_call["kwargs"]["preload_capabilities"] is True
        assert bq_call["kwargs"]["arm"] == "Bq"
        assert bq_call["kwargs"]["preload_capabilities"] is False
        # The EXPERIMENT container is the one both dispatch through.
        assert bp_call["kwargs"]["container"] is container
        assert bq_call["kwargs"]["container"] is container
        # ...and the two dispatches share everything else.
        assert set(bp_call["kwargs"]) == set(bq_call["kwargs"])
        for key, value in bq_call["kwargs"].items():
            if key in ("arm", "preload_capabilities"):
                continue
            assert bp_call["kwargs"][key] == value

    def test_registry_arm_records_private_skill_evidence(self, tmp_path: Path) -> None:
        """A FULL Bp run over fakes (no DB, no network): the record carries
        the private-skill routing evidence from the client's decisions —
        selected + rank among the kept + the drop reason of the distractor."""
        fixture = PRIVATE_TASKS[0]
        client = _RecordingClient()
        container = SimpleNamespace(agent_capability_clients=lambda: client)
        sources = tmp_path / "src"
        _materialize(fixture, sources)
        gateway = FakeModelGateway([ContinueAction()])
        contract, spec = run_hbench._contract_spec(fixture)
        record = run_hbench.run_registry_arm(
            fixture,
            contract,
            spec,
            gateway,
            sources,
            tmp_path / "runs",
            max_turns=1,
            container=container,
            sandbox=available_sandbox(),
            arm="Bp",
            preload_capabilities=True,
        )
        assert record["arm"] == "Bp"
        assert record["private_skill_selected"] is True
        assert record["private_skill_rank"] == 1
        assert record["private_skill_drop_reason"] is None
        # The full decision evidence rides along (distractor + reason).
        assert record["capability_decisions"] == [
            {
                "kept": [
                    {
                        "capability_id": "atlas-error-standard",
                        "version": "1.0.0",
                        "score": 0.5,
                        "reason": "kept",
                    }
                ],
                "dropped": [
                    {
                        "capability_id": "some-distractor",
                        "version": "1.0.0",
                        "score": 0.5,
                        "reason": "score_margin",
                    }
                ],
                "scores_available": True,
            }
        ]

    def test_private_skill_evidence_miss_is_honest(self) -> None:
        """A client whose decisions never contain the private skill records
        selected=False / rank=None — a MISS is a result, never a guess."""
        client = SimpleNamespace(decisions=[])
        evidence = run_hbench.private_skill_evidence(client, "atlas-error-standard")
        assert evidence == {
            "private_skill_selected": False,
            "private_skill_rank": None,
            "private_skill_drop_reason": None,
            "capability_decisions": [],
        }

    def test_experiment_container_gets_the_given_db_and_store(self, monkeypatch: Any) -> None:
        """build_experiment_capability_container passes EXACTLY the given
        database URL + object-store root to the Container composition (with
        the semantic embedder) — the same composition S/P use, pointed at the
        experiment copy. No DB connection happens at build time."""
        recorded: dict[str, Any] = {}

        class _FakeContainer:
            def __init__(self, settings: Any) -> None:
                recorded["settings"] = settings

        import aci.adapters.inbound.rest.wiring as wiring

        monkeypatch.setattr(wiring, "Container", _FakeContainer)
        container = run_hbench.build_experiment_capability_container(
            "postgresql+psycopg://aci:aci@100.126.242.46:5432/aci_e2b",
            "/somewhere/e2b-objects",
        )
        assert container is not None
        settings = recorded["settings"]
        assert settings.database_url == "postgresql+psycopg://aci:aci@100.126.242.46:5432/aci_e2b"
        assert settings.object_store_root == "/somewhere/e2b-objects"
        assert settings.embedder == "fastembed"

    def test_main_refuses_operational_databases_for_bp_bq(self) -> None:
        """The runner REFUSES (exit 2) a --registry-db-url naming aci or
        aci_bench for arms Bp/Bq — the experiment may never touch either."""
        for url in (
            "postgresql+psycopg://aci:aci@localhost:5432/aci_bench",
            "postgresql+psycopg://aci:aci@localhost:5432/aci",
        ):
            assert (
                run_hbench.main(
                    [
                        "--api-key",
                        "k",
                        "--set",
                        "private",
                        "--arms",
                        "Bp,Bq",
                        "--registry-db-url",
                        url,
                        "--object-store-root",
                        "/tmp/objects",
                    ]
                )
                == 2
            )

    def test_main_requires_registry_flags_for_bp_bq(self) -> None:
        """Bp/Bq without BOTH experiment registry flags fail loudly BEFORE
        any run or report — never a silent default to aci_bench."""
        assert run_hbench.main(["--api-key", "k", "--set", "private", "--arms", "Bp"]) == 2
        assert (
            run_hbench.main(
                [
                    "--api-key",
                    "k",
                    "--set",
                    "private",
                    "--arms",
                    "Bq",
                    "--registry-db-url",
                    "postgresql+psycopg://aci:aci@h:5432/aci_e2b",
                ]
            )
            == 2
        )

    def test_main_refuses_bp_bq_outside_the_private_set(self) -> None:
        assert (
            run_hbench.main(
                [
                    "--api-key",
                    "k",
                    "--set",
                    "verified",
                    "--arms",
                    "Bp",
                    "--registry-db-url",
                    "postgresql+psycopg://aci:aci@h:5432/aci_e2b",
                    "--object-store-root",
                    "/tmp/objects",
                ]
            )
            == 2
        )

    def test_main_refuses_f_outside_the_private_set(self) -> None:
        assert run_hbench.main(["--api-key", "k", "--set", "verified", "--arms", "F"]) == 2


class TestSetupScriptRefusal:
    """scripts/e2b_setup_registry.py refuses aci/aci_bench (exit 2) BEFORE any
    DB connection — the experiment copy is the only acceptable target."""

    def test_refuses_aci_bench(self) -> None:
        assert (
            e2b_setup_registry.main(
                [
                    "--database-url",
                    "postgresql+psycopg://aci:aci@localhost:5432/aci_bench",
                    "--object-store-root",
                    "/tmp/objects",
                ]
            )
            == 2
        )

    def test_refuses_aci(self) -> None:
        assert (
            e2b_setup_registry.main(
                [
                    "--database-url",
                    "postgresql+psycopg://aci:aci@localhost:5432/aci",
                    "--object-store-root",
                    "/tmp/objects",
                ]
            )
            == 2
        )

    def test_allows_the_experiment_copy(self) -> None:
        assert (
            e2b_setup_registry.refuse_operational_database(
                "postgresql+psycopg://aci:aci@100.126.242.46:5432/aci_e2b"
            )
            is None
        )

    def test_package_is_skill_text_as_is_plus_license(self, tmp_path: Path) -> None:
        """The materialized package: SKILL.md is the fixture's skill text
        VERBATIM (never hand-tuned) plus the first-party MIT LICENSE file
        (the §24 evidence detect_license reads)."""
        fixture = PRIVATE_TASKS[0]
        package = e2b_setup_registry.materialize_package(fixture, tmp_path)
        assert (package / "SKILL.md").read_text(encoding="utf-8") == fixture["skill"]
        assert (package / "LICENSE").is_file()
        detection = e2b_setup_registry.detect_license(package)
        assert detection.spdx_id == "MIT", detection
