"""REST /v1/agent-runs surface tests (ADR-014): thin edge → AgentRunService →
kernel → RunResult. Deterministic — a scripted model gateway, no network;
workspace runs use tmp dirs and the interpreter running the tests."""

import sys
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aci.adapters.inbound.rest import agent_runs as rest_agent_runs
from aci.adapters.inbound.rest.agent_run_wiring import build_agent_run_service
from aci.adapters.inbound.rest.errors import register_error_handlers
from aci.adapters.inbound.rest.wiring import Container, Settings
from aci.application.run_agent_task import AgentRunService, ModelGatewayFactory
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.actions import FinalCandidate, ToolCallBatchAction
from aci.domain.runtime.tools import ToolCall
from aci.runtime.model_gateway import FakeModelGateway
from tests.sandbox_support import available_sandbox


class _FakeFactory:
    """ModelGatewayFactory returning a scripted gateway (deterministic)."""

    def __init__(self, gateway: FakeModelGateway) -> None:
        self._gateway = gateway

    def build(self) -> object:
        return self._gateway


class _NullToolFactory:
    def build(self) -> object:
        from aci.runtime.tool_runtime import ToolRegistry, ToolRuntime

        class _NoDispatch:
            def dispatch(self, tool: object, args: object, envelope: object) -> object:
                raise DomainError(ErrorCode.TOOL_EXECUTION_FAILED, "no dispatcher")

        return ToolRuntime(ToolRegistry(), dispatcher=cast("Any", _NoDispatch()))


class _NullCapabilityFactory:
    def build(self) -> object:
        class _None:
            def handle_request(self, request: object, snapshot: object) -> list[object]:
                return []

        return _None()


class _NullContextFactory:
    def build(self) -> object:
        from aci.runtime.context_engine import ContextBudget, ContextEngine

        return ContextEngine(ContextBudget(total_tokens=60_000))


def _service(actions: list[Any], **options: Any) -> AgentRunService:
    return AgentRunService(
        model_gateway_factory=cast(ModelGatewayFactory, _FakeFactory(FakeModelGateway(actions))),
        tool_executor_factory=cast(ModelGatewayFactory, _NullToolFactory()),
        capability_runtime_factory=cast(ModelGatewayFactory, _NullCapabilityFactory()),
        context_engine_factory=cast(ModelGatewayFactory, _NullContextFactory()),
        **options,
    )


def _client(service: AgentRunService, settings: Settings | None = None) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    container = cast(
        Container,
        type(
            "C",
            (),
            {
                "agent_run_service": service,
                # The exposure gate reads the token off container settings;
                # default = unauthenticated mode (backward compatible).
                "settings": settings or Settings(agent_runs_token=""),
            },
        )(),
    )
    app.dependency_overrides[rest_agent_runs.get_container] = lambda: container
    app.include_router(rest_agent_runs.router)
    return TestClient(app)


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "objective": "fix the failing test",
        "acceptance_criteria": ["test passes"],
    }
    body.update(overrides)
    return body


def _workspace(tmp_path: Path) -> dict[str, Any]:
    """A workspace source `proj` holding notes.txt, plus service options that
    expose it with the test interpreter as the only allowed command."""
    source = tmp_path / "sources" / "proj"
    source.mkdir(parents=True)
    (source / "notes.txt").write_text("cause is X\n", encoding="utf-8")
    return {
        "workspace_root": tmp_path / "sources",
        "runs_root": tmp_path / "runs",
        "process_prefixes": [sys.executable],
        # Real bwrap where usable (CI), explicit opt-out elsewhere — these
        # tests are about run wiring, tests/security/test_process_sandbox.py
        # about the sandbox.
        "process_sandbox": available_sandbox(),
    }


def _read(path: str) -> ToolCallBatchAction:
    return ToolCallBatchAction(
        calls=[ToolCall(call_id="c1", tool_id="read_file", arguments={"path": path})]
    )


def _write(path: str, content: str = "x", call_id: str = "c1") -> ToolCallBatchAction:
    return ToolCallBatchAction(
        calls=[
            ToolCall(
                call_id=call_id,
                tool_id="write_file",
                arguments={"path": path, "content": content},
            )
        ]
    )


def _run_dir(tmp_path: Path, run_id: str) -> Path:
    return tmp_path / "runs" / run_id


class TestAgentRunsRest:
    # Success-path plumbing uses the researcher profile on a workspace: its
    # claims must cite a file the run READ (INV-08 — evidence-grounded).
    def test_run_success_round_trip(self, tmp_path: Path) -> None:
        service = _service(
            [_read("notes.txt"), FinalCandidate(summary="found it", claims=["notes.txt: cause X"])],
            **_workspace(tmp_path),
        )
        client = _client(service)
        response = client.post(
            "/v1/agent-runs", json=_body(requested_profile="researcher", workspace="proj")
        )
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "succeeded"
        assert data["stop_reason"] == "SUCCESS"
        assert data["summary"] == "found it"
        assert data["evidence_verdict"] == "PASS"
        assert "PASS:claims_grounded" in data["checks"]
        assert data["turns"] == 2
        assert data["tool_calls"] == 1

    def test_coder_claim_without_observed_change_fails(self) -> None:
        """INV-08 at the edge: the model saying it changed src/x.py is not
        evidence — no tool effect was observed, so the run never succeeds
        (three candidates: the kernel feeds verification back twice)."""
        candidate = FinalCandidate(summary="fixed", changes=["src/x.py"])
        service = _service([candidate, candidate, candidate])
        client = _client(service)
        data = client.post("/v1/agent-runs", json=_body()).json()
        assert data["status"] == "failed"
        assert data["stop_reason"] == "VERIFICATION_FAILED"
        assert "claimed_changes_observed" in data["summary"]

    def test_unknown_profile_rejected(self) -> None:
        service = _service([FinalCandidate(summary="x")])
        client = _client(service)
        response = client.post("/v1/agent-runs", json=_body(requested_profile="wizard"))
        assert response.status_code == 422
        assert "unknown profile" in response.json()["error"]["message"]

    def test_unconfigured_gateway_fails_caller_visibly(self) -> None:
        service = _service([])
        client = _client(service)
        # Script exhaustion → MODEL_FAILURE classified by the kernel → the
        # run result carries the failure caller-visibly (never a fake success).
        response = client.post("/v1/agent-runs", json=_body())
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "failed"
        assert data["stop_reason"] == "MODEL_FAILURE"

    def test_cancel_unknown_run(self) -> None:
        service = _service([FinalCandidate(summary="x")])
        client = _client(service)
        response = client.post("/v1/agent-runs/ghost/cancel")
        assert response.status_code == 200
        assert response.json() == {"cancelled": False}

    def test_get_run_after_completion(self, tmp_path: Path) -> None:
        service = _service(
            [_read("notes.txt"), FinalCandidate(summary="done", claims=["notes.txt: cause X"])],
            **_workspace(tmp_path),
        )
        client = _client(service)
        created = client.post(
            "/v1/agent-runs", json=_body(requested_profile="researcher", workspace="proj")
        ).json()
        fetched = client.get(f"/v1/agent-runs/{created['run_id']}")
        assert fetched.status_code == 200
        assert fetched.json()["run_id"] == created["run_id"]
        assert fetched.json()["status"] == "succeeded"

    def test_get_unknown_run_404(self) -> None:
        service = _service([FinalCandidate(summary="x")])
        client = _client(service)
        response = client.get("/v1/agent-runs/ghost")
        assert response.status_code == 404

    def test_revise_links_previous_attempt(self, tmp_path: Path) -> None:
        bad = FinalCandidate(summary="bad")
        service = _service(
            [
                bad,
                bad,
                bad,
                _read("notes.txt"),
                FinalCandidate(summary="fixed", claims=["notes.txt: cause X"]),
            ],
            **_workspace(tmp_path),
        )
        client = _client(service)
        first = client.post(
            "/v1/agent-runs", json=_body(requested_profile="researcher", workspace="proj")
        ).json()
        assert first["status"] == "failed"
        revision = client.post(
            f"/v1/agent-runs/{first['run_id']}/revise",
            json={"failed_criteria": ["test passes"], "feedback": "the summary was empty"},
        )
        assert revision.status_code == 201
        assert revision.json()["summary"] == "fixed"
        assert revision.json()["status"] == "succeeded"
        # The revision is built FROM the previous attempt (§29A delta revision).
        contract = service.contract(revision.json()["run_id"])
        assert contract is not None
        assert contract.parent_task_id == first["run_id"]
        assert contract.objective == "fix the failing test"
        assert contract.requested_profile == "researcher"
        assert [c.description for c in contract.acceptance_criteria] == ["test passes"]
        assert f"Previous attempt {first['run_id']} failed criteria: test passes" in (
            contract.global_context
        )
        assert "Feedback: the summary was empty" in contract.global_context
        assert service.get(first["run_id"]) is not None

    def test_revise_unknown_run_404(self) -> None:
        service = _service([FinalCandidate(summary="x")])
        client = _client(service)
        response = client.post("/v1/agent-runs/ghost/revise", json={"feedback": "again"})
        assert response.status_code == 404

    def test_cancel_of_a_paused_run(self, tmp_path: Path) -> None:
        """POST cancel on a PAUSED run: the same ``{"cancelled": bool}`` shape;
        the run is then CANCELLED on GET and its pause cannot be resumed."""
        service = _service([_read("notes.txt")], **_workspace(tmp_path))
        client = _client(service)
        paused = client.post(
            "/v1/agent-runs",
            json=_body(
                requested_profile="researcher",
                workspace="proj",
                approval_required_tools=["read_file"],
            ),
        ).json()
        assert paused["status"] == "interrupted_approval", paused
        run_id = paused["run_id"]

        cancelled = client.post(f"/v1/agent-runs/{run_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json() == {"cancelled": True}
        got = client.get(f"/v1/agent-runs/{run_id}").json()
        assert got["status"] == "cancelled"
        assert got["stop_reason"] == "CANCELLED"
        assert got["detail_code"] == "CANCELLED_WHILE_PAUSED"
        assert got["approval_id"] is None
        resumed = client.post(
            f"/v1/agent-runs/{run_id}/resume",
            json={"approval_id": paused["approval_id"], "approve": True},
        )
        assert resumed.status_code == 409
        assert resumed.json()["error"]["code"] == "CHECKPOINT_CONSUMED"
        again = client.post(f"/v1/agent-runs/{run_id}/cancel")
        assert again.status_code == 200
        assert again.json() == {"cancelled": False}

    def test_revise_of_a_paused_run_supersedes_its_pause(self, tmp_path: Path) -> None:
        service = _service([_read("notes.txt"), _read("notes.txt")], **_workspace(tmp_path))
        client = _client(service)
        paused = client.post(
            "/v1/agent-runs",
            json=_body(
                requested_profile="researcher",
                workspace="proj",
                approval_required_tools=["read_file"],
            ),
        ).json()
        assert paused["status"] == "interrupted_approval", paused
        revision = client.post(
            f"/v1/agent-runs/{paused['run_id']}/revise", json={"feedback": "read it"}
        )
        assert revision.status_code == 201, revision.text
        assert revision.json()["run_id"] != paused["run_id"]
        parent = client.get(f"/v1/agent-runs/{paused['run_id']}").json()
        assert parent["status"] == "cancelled"
        assert parent["detail_code"] == "SUPERSEDED_BY_REVISION"
        resumed = client.post(
            f"/v1/agent-runs/{paused['run_id']}/resume",
            json={"approval_id": paused["approval_id"], "approve": True},
        )
        assert resumed.status_code == 409
        assert resumed.json()["error"]["code"] == "CHECKPOINT_CONSUMED"

    # -- workspace runs (§16) ----------------------------------------------------

    def test_run_token_required_when_configured(self) -> None:
        """Exposure gate: with ACI_AGENT_RUNS_TOKEN set, every agent-run
        route requires `Authorization: Bearer <token>` — a run executes
        under the server user, so the port must not be open without it."""
        service = _service([FinalCandidate(summary="x")])
        client = _client(service, Settings(agent_runs_token="s3cret"))
        assert client.post("/v1/agent-runs", json=_body()).status_code == 401
        wrong = client.post(
            "/v1/agent-runs", json=_body(), headers={"Authorization": "Bearer nope"}
        )
        assert wrong.status_code == 401
        ok = client.post("/v1/agent-runs", json=_body(), headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 201

    def test_run_token_gate_covers_read_and_cancel(self) -> None:
        service = _service([FinalCandidate(summary="x")])
        client = _client(service, Settings(agent_runs_token="s3cret"))
        assert client.get("/v1/agent-runs/run-1").status_code == 401
        assert client.post("/v1/agent-runs/run-1/cancel").status_code == 401

    def test_workspace_guardrails_block_shell_injection(self, tmp_path: Path) -> None:
        """The REST path wires the standard guardrail set (§14): a command
        carrying shell metacharacters is BLOCKED as an observation — never
        executed — and the run continues to its normal terminal state."""
        injected = ToolCallBatchAction(
            calls=[
                ToolCall(
                    call_id="c1",
                    tool_id="run_command",
                    arguments={
                        "command": [
                            sys.executable,
                            "-c",
                            "import os; os.system('echo pwned > pwned.txt') # $(marker)",
                        ]
                    },
                )
            ]
        )
        service = _service(
            [
                injected,
                _read("notes.txt"),
                FinalCandidate(summary="ok", claims=["notes.txt: cause X"]),
            ],
            **_workspace(tmp_path),
        )
        client = _client(service)
        data = client.post(
            "/v1/agent-runs", json=_body(requested_profile="researcher", workspace="proj")
        ).json()
        # The block is an observation for the model, never a crash (§58) —
        # the run still reaches its verifier-gated terminal state.
        assert data["status"] == "succeeded"
        # And the injected command never executed: without the guardrail
        # the os.system call would have written pwned.txt into the run dir.
        assert not (_run_dir(tmp_path, data["run_id"]) / "pwned.txt").exists()

    def test_workspace_guardrails_block_path_traversal(self, tmp_path: Path) -> None:
        """`..` escapes are blocked by the guardrail BEFORE the dispatcher
        ever sees them (the workspace path check is the first line; this
        is the second)."""
        escape = ToolCallBatchAction(
            calls=[
                ToolCall(
                    call_id="c1",
                    tool_id="write_file",
                    arguments={"path": "../escape.txt", "content": "x"},
                )
            ]
        )
        service = _service(
            [
                escape,
                _read("notes.txt"),
                FinalCandidate(summary="ok", claims=["notes.txt: cause X"]),
            ],
            **_workspace(tmp_path),
        )
        client = _client(service)
        data = client.post(
            "/v1/agent-runs", json=_body(requested_profile="researcher", workspace="proj")
        ).json()
        assert data["status"] == "succeeded"
        assert not (tmp_path / "sources" / "escape.txt").exists()
        assert not (_run_dir(tmp_path, data["run_id"]) / "escape.txt").exists()

    def test_turn_limit_records_passing_verification_as_evidence(self, tmp_path: Path) -> None:
        """2026-10-01 review: LIMIT_TURNS is a FAILURE even when the fix
        landed (INV-08 is never waived) — but the verifier's command still
        runs once and its outcome is recorded as EVIDENCE, so "budget
        expired, suite green, unclaimed" is distinguishable from "nothing
        landed" on the wire."""
        actions = [
            _write("test_ok.py", "def test_ok():\n    assert True\n"),
            _read("notes.txt"),
            _read("notes.txt"),
        ]
        service = _service(actions, **_workspace(tmp_path))
        client = _client(service)
        data = client.post(
            "/v1/agent-runs",
            json=_body(
                workspace="proj",
                verification_command=[sys.executable, "-m", "pytest", "-q"],
                max_turns=3,
            ),
        ).json()
        assert data["status"] == "failed"
        assert data["stop_reason"] == "LIMIT_TURNS"
        # The suite IS green at the limit — recorded, never converted to success.
        assert "PASS:verification_at_limit" in data["checks"]
        assert data["evidence_verdict"] == "INCONCLUSIVE"

    def test_turn_limit_records_failing_verification_as_evidence(self, tmp_path: Path) -> None:
        """Nothing landed → the at-limit check FAILs: the two limit flavors
        are distinguishable in the result."""
        actions = [_read("notes.txt"), _read("notes.txt"), _read("notes.txt")]
        service = _service(actions, **_workspace(tmp_path))
        client = _client(service)
        data = client.post(
            "/v1/agent-runs",
            json=_body(
                workspace="proj",
                verification_command=[sys.executable, "-m", "pytest", "-q"],
                max_turns=3,
            ),
        ).json()
        assert data["status"] == "failed"
        assert data["stop_reason"] == "LIMIT_TURNS"
        assert "FAIL:verification_at_limit" in data["checks"]

    def test_turn_limit_without_verification_command_records_no_evidence(
        self, tmp_path: Path
    ) -> None:
        """No command configured → nothing to run at the limit → no evidence
        pack, plain LIMIT_TURNS."""
        actions = [_read("notes.txt"), _read("notes.txt"), _read("notes.txt")]
        service = _service(actions, **_workspace(tmp_path))
        client = _client(service)
        data = client.post("/v1/agent-runs", json=_body(workspace="proj", max_turns=3)).json()
        assert data["status"] == "failed"
        assert data["stop_reason"] == "LIMIT_TURNS"
        assert data["checks"] == []
        assert data["evidence_verdict"] is None

    def test_workspace_run_writes_file(self, tmp_path: Path) -> None:
        service = _service(
            [_write("out.txt"), FinalCandidate(summary="done", changes=["out.txt"])],
            **_workspace(tmp_path),
        )
        client = _client(service)
        response = client.post("/v1/agent-runs", json=_body(workspace="proj"))
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "succeeded"
        assert "PASS:claimed_changes_observed" in data["checks"]
        written = _run_dir(tmp_path, data["run_id"]) / "out.txt"
        assert written.read_text(encoding="utf-8") == "x"
        # The source is never touched: the run worked in its own copy.
        assert not (tmp_path / "sources" / "proj" / "out.txt").exists()
        assert (_run_dir(tmp_path, data["run_id"]) / "notes.txt").exists()

    def test_verification_command_failure_fails_run(self, tmp_path: Path) -> None:
        done = FinalCandidate(summary="done", changes=["out.txt"])
        service = _service([_write("out.txt"), done, done, done], **_workspace(tmp_path))
        client = _client(service)
        data = client.post(
            "/v1/agent-runs",
            json=_body(
                workspace="proj",
                verification_command=[sys.executable, "-c", "import sys; sys.exit(1)"],
            ),
        ).json()
        assert data["status"] == "failed"
        assert data["stop_reason"] == "VERIFICATION_FAILED"
        assert "FAIL:verification_command_passes" in data["checks"]

    def test_verification_command_passes(self, tmp_path: Path) -> None:
        service = _service(
            [_write("out.txt"), FinalCandidate(summary="done", changes=["out.txt"])],
            **_workspace(tmp_path),
        )
        client = _client(service)
        data = client.post(
            "/v1/agent-runs",
            json=_body(
                workspace="proj",
                verification_command=[sys.executable, "-c", "import sys; sys.exit(0)"],
            ),
        ).json()
        assert data["status"] == "succeeded"
        assert "PASS:verification_command_passes" in data["checks"]

    def test_verification_command_outside_ceiling_403(self, tmp_path: Path) -> None:
        service = _service([FinalCandidate(summary="x")], **_workspace(tmp_path))
        client = _client(service)
        response = client.post(
            "/v1/agent-runs",
            json=_body(workspace="proj", verification_command=["rm", "-rf", "/"]),
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"
        # Refused before anything was provisioned.
        assert not (tmp_path / "runs").exists()

    def test_verification_command_requires_workspace_422(self) -> None:
        service = _service([FinalCandidate(summary="x")])
        client = _client(service)
        response = client.post(
            "/v1/agent-runs", json=_body(verification_command=[sys.executable, "-V"])
        )
        assert response.status_code == 422

    def test_command_prefixes_outside_ceiling_403(self, tmp_path: Path) -> None:
        """INV-02: a client may narrow the server ceiling, never widen it."""
        service = _service([FinalCandidate(summary="x")], **_workspace(tmp_path))
        client = _client(service)
        response = client.post(
            "/v1/agent-runs", json=_body(workspace="proj", command_prefixes=["rm -rf"])
        )
        assert response.status_code == 403

    def test_bad_workspace_name_422(self, tmp_path: Path) -> None:
        service = _service([FinalCandidate(summary="x")], **_workspace(tmp_path))
        client = _client(service)
        for name in ("../sources", "a/b", "..", "", "proj\\x"):
            response = client.post("/v1/agent-runs", json=_body(workspace=name))
            assert response.status_code == 422, name
        assert not (tmp_path / "runs").exists()

    def test_unknown_workspace_404(self, tmp_path: Path) -> None:
        service = _service([FinalCandidate(summary="x")], **_workspace(tmp_path))
        client = _client(service)
        response = client.post("/v1/agent-runs", json=_body(workspace="ghost"))
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "WORKSPACE_NOT_FOUND"

    def test_workspace_without_root_configured_422(self) -> None:
        service = _service([FinalCandidate(summary="x")])
        client = _client(service)
        response = client.post("/v1/agent-runs", json=_body(workspace="proj"))
        assert response.status_code == 422

    def test_write_scopes_enforced(self, tmp_path: Path) -> None:
        """Grants bind the workspace too (defense in depth, INV-04): a write
        outside the requested scope never lands on disk."""
        batch = ToolCallBatchAction(
            calls=[
                ToolCall(
                    call_id="c1",
                    tool_id="write_file",
                    arguments={"path": "allowed/a.txt", "content": "a"},
                ),
                ToolCall(
                    call_id="c2", tool_id="write_file", arguments={"path": "b.txt", "content": "b"}
                ),
            ]
        )
        service = _service(
            [batch, FinalCandidate(summary="done", changes=["allowed/a.txt"])],
            **_workspace(tmp_path),
        )
        client = _client(service)
        data = client.post(
            "/v1/agent-runs", json=_body(workspace="proj", write_scopes=["allowed"])
        ).json()
        assert data["status"] == "succeeded"
        run_dir = _run_dir(tmp_path, data["run_id"])
        assert (run_dir / "allowed" / "a.txt").exists()
        assert not (run_dir / "b.txt").exists()

    def test_revise_continues_from_previous_run_dir(self, tmp_path: Path) -> None:
        service = _service(
            [
                _write("out.txt"),
                FinalCandidate(summary="first", changes=["out.txt"]),
                _write("second.txt", content="y"),
                FinalCandidate(summary="second", changes=["second.txt"]),
            ],
            **_workspace(tmp_path),
        )
        client = _client(service)
        first = client.post("/v1/agent-runs", json=_body(workspace="proj")).json()
        assert first["status"] == "succeeded"
        revision = client.post(
            f"/v1/agent-runs/{first['run_id']}/revise",
            json={"failed_criteria": ["test passes"], "feedback": "also add second.txt"},
        ).json()
        assert revision["status"] == "succeeded"
        first_dir = _run_dir(tmp_path, first["run_id"])
        revision_dir = _run_dir(tmp_path, revision["run_id"])
        assert revision_dir != first_dir
        # Attempt 2 started from attempt 1's working copy, not from the source.
        assert (revision_dir / "out.txt").read_text(encoding="utf-8") == "x"
        assert (revision_dir / "second.txt").read_text(encoding="utf-8") == "y"
        assert not (first_dir / "second.txt").exists()
        assert not (tmp_path / "sources" / "proj" / "out.txt").exists()

    # -- wiring --------------------------------------------------------------------

    def test_wiring_builds_with_unconfigured_settings(self) -> None:
        settings = Settings(agent_model_base_url="", agent_model_id="")
        service = build_agent_run_service(settings)
        assert isinstance(service, AgentRunService)

    def test_wiring_passes_workspace_settings(self, tmp_path: Path) -> None:
        """The workspace root and process ceiling reach the service: an
        unknown workspace is 404, a command outside the ceiling is 403, and a
        command inside it starts a run (which then fails on the honest-null
        gateway, never silently)."""
        options = _workspace(tmp_path)
        settings = Settings(
            agent_model_base_url="",
            agent_workspace_root=str(options["workspace_root"]),
            agent_runs_root=str(options["runs_root"]),
            agent_process_prefixes=[sys.executable],
            agent_sandbox="bwrap" if available_sandbox().name == "bwrap" else "none",
        )
        client = _client(build_agent_run_service(settings))
        assert client.post("/v1/agent-runs", json=_body(workspace="ghost")).status_code == 404
        outside = client.post(
            "/v1/agent-runs", json=_body(workspace="proj", verification_command=["rm", "-rf"])
        )
        assert outside.status_code == 403
        inside = client.post(
            "/v1/agent-runs",
            json=_body(workspace="proj", verification_command=[sys.executable, "-V"]),
        )
        assert inside.status_code == 201
        assert inside.json()["stop_reason"] == "MODEL_FAILURE"
        assert (_run_dir(tmp_path, inside.json()["run_id"]) / "notes.txt").exists()

    def test_real_wiring_context_engine_has_build(self) -> None:
        """The production wiring must satisfy the kernel's context seam: the
        kernel calls ``context_engine.build(snapshot, turn=...)`` — a factory
        that returns anything else crashes every real run at turn 1."""
        from aci.runtime.context_engine import ContextEngine

        settings = Settings(agent_model_base_url="", agent_model_id="")
        service = build_agent_run_service(settings)
        context = service._context_factory.build()  # noqa: SLF001
        assert isinstance(context, ContextEngine)
        assert hasattr(context, "build")
