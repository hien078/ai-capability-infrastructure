"""H-bench runner structural tests — the A/B fairness invariants (§42).

The runner measures HARNESS value (kernel arm K vs naive loop arm N); these
pins keep the comparison honest: same fixtures, same ceiling, same model
action protocol, and a verification command that can actually run.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_hbench  # noqa: E402

from aci.domain.runtime.actions import ContinueAction  # noqa: E402
from aci.evaluation.harness_cases import HARNESS_CASE_IDS  # noqa: E402
from aci.runtime.event_bus import EventBus  # noqa: E402
from aci.runtime.model_gateway import FakeModelGateway, ModelRequest  # noqa: E402
from aci.runtime.workspace import command_within_prefixes  # noqa: E402
from tests.sandbox_support import available_sandbox  # noqa: E402


class TestHbenchRunner:
    def test_fixture_pack_is_the_verified_eight(self) -> None:
        """The 8 §80-verified fixtures (verify_multi_fixtures.py: each fails
        as shipped, passes after the root-cause fix) — pinned so proof_loop
        drift breaks loudly HERE, not silently inside a report."""
        assert {f["name"] for f in run_hbench._all_fixtures()} == {
            "multi-config-precedence",
            "multi-cache-invalidation",
            "multi-pipeline-ordering",
            "multi-error-translation",
            "multi-event-aliasing",
            "long-order-pipeline",
            "long-auth-session",
            "long-notify-fanout",
        }

    def test_every_fixture_is_labeled_with_a_real_h_case(self) -> None:
        for fixture in run_hbench._all_fixtures():
            ref = run_hbench.H_REFS.get(fixture["name"])
            assert ref in HARNESS_CASE_IDS, fixture["name"]

    def test_pending_h_cases_are_recorded_not_hidden(self) -> None:
        """H005/H006/H007/H011 need dedicated fixtures (context flood,
        approval, injected transient failure, crash/resume) — the report
        must SAY they are pending instead of implying 20/20 coverage."""
        assert "H005" in run_hbench.PENDING_H_CASES
        assert "H011" in run_hbench.PENDING_H_CASES

    def test_verification_command_is_within_the_process_ceiling(self) -> None:
        """If this breaks, both arms refuse to verify (INV-06 fail-closed)
        and every `accepted` in every report is a lie."""
        assert command_within_prefixes(run_hbench.VERIFICATION, run_hbench.PROCESS_PREFIXES)

    def test_naive_arm_uses_the_kernel_system_prompt(self) -> None:
        """§42: same model interface in both arms — N gets the kernel's own
        _system_prompt over the same contract/spec/grants (identity +
        objective + authority summary + action protocol), so the A/B
        measures harness mechanisms (verification gate, recovery, context),
        not prompts."""
        import inspect

        source = inspect.getsource(run_hbench.run_naive_arm)
        assert "_system_prompt" in source
        assert "task_state_from(contract)" in source


class TestDomainFixtureSet:
    """The --set domain selection (the skill axis): the runner can run the
    domain-knowledge fixtures WITHOUT changing the default pack — the 8 §80
    fixtures stay the default so every existing round stays byte-comparable,
    and the two sets never mix."""

    def test_default_set_is_still_the_verified_eight(self) -> None:
        assert [f["name"] for f in run_hbench._fixture_set("verified")] == [
            f["name"] for f in run_hbench._all_fixtures()
        ]

    def test_domain_set_is_the_domain_fixtures(self) -> None:
        domain = run_hbench._fixture_set("domain")
        assert [f["name"] for f in domain] == [f["name"] for f in run_hbench.DOMAIN_TASKS]
        assert len(domain) >= 3

    def test_domain_and_verified_sets_are_disjoint(self) -> None:
        domain = {f["name"] for f in run_hbench._fixture_set("domain")}
        verified = {f["name"] for f in run_hbench._fixture_set("verified")}
        assert not domain & verified

    def test_domain_fixtures_carry_the_same_shape_as_the_verified_pack(self) -> None:
        for fixture in run_hbench._fixture_set("domain"):
            assert fixture["name"] and fixture["prompt"]
            assert fixture["files"]
            assert any(name.startswith("test_") for name in fixture["files"])

    def test_every_domain_fixture_names_its_intended_skill(self) -> None:
        for fixture in run_hbench._fixture_set("domain"):
            assert fixture["name"] in run_hbench.DOMAIN_INTENDED_SKILLS, fixture["name"]

    def test_domain_prompts_do_not_name_the_intended_skill(self) -> None:
        """The task prompt must never name the skill it is supposed to route
        to — otherwise the round measures prompt-echo, not retrieval."""
        for fixture in run_hbench._fixture_set("domain"):
            intended = run_hbench.DOMAIN_INTENDED_SKILLS[fixture["name"]]
            prompt = fixture["prompt"].lower()
            assert intended not in prompt, fixture["name"]
            assert intended.replace("-", " ") not in prompt, fixture["name"]


class TestMechanismCounts:
    """The §44 cost columns are counted from the events the kernel ACTUALLY
    emits — driven through a real HarnessKernel, so an event-name drift
    (the original `repairs` counter listened for RECOVERY_STARTED, which the
    kernel never emits, and silently reported 0) breaks here."""

    def _run(
        self,
        script: list[object],
        check_results: list[bool],
        *,
        tools: list[object] | None = None,
        dispatcher: object | None = None,
    ) -> dict[str, int]:
        from datetime import UTC, datetime

        from aci.domain.runtime.evidence import CheckResult, ResultContract
        from aci.domain.runtime.spec import RuntimeSpec
        from aci.domain.runtime.subtask import SubtaskContract
        from aci.runtime.context_engine import ContextBudget, ContextEngine
        from aci.runtime.event_bus import EventBus
        from aci.runtime.model_gateway import FakeModelGateway
        from aci.runtime.recovery import RecoveryManager
        from aci.runtime.run_controller import HarnessKernel
        from aci.runtime.state_manager import StateManager
        from aci.runtime.tool_runtime import ToolRegistry, ToolRuntime
        from aci.runtime.verification import VerificationManager, VerifierCallable

        verdicts = iter(check_results)
        check = VerifierCallable(
            "gate", lambda s, c: CheckResult(name="gate", passed=next(verdicts))
        )
        bus = EventBus()
        registry = ToolRegistry()
        for tool in tools or []:
            registry.register(tool)  # type: ignore[arg-type]
        kernel = HarnessKernel(
            state=StateManager(),
            model_gateway=FakeModelGateway(script),  # type: ignore[arg-type]
            tool_executor=ToolRuntime(registry, dispatcher=dispatcher or object()),  # type: ignore[arg-type]
            context_engine=ContextEngine(ContextBudget(total_tokens=60_000)),
            verifier=VerificationManager([check]),
            recovery=RecoveryManager(),
            capability_runtime=object(),  # type: ignore[arg-type]
            event_bus=bus,
            sleep=lambda _s: None,
        )
        contract = SubtaskContract(task_id="hb-1", objective="fix", created_at=datetime.now(UTC))
        from aci.domain.runtime.authority import FilesystemScope, GrantEnvelope

        spec = RuntimeSpec(
            result_contract=ResultContract(contract_id="c", required_fields=["summary"]),
            initial_grants=GrantEnvelope(filesystem=FilesystemScope(read=["out"])),
            created_at=datetime.now(UTC),
        )
        kernel.run(contract, spec)
        return run_hbench.mechanism_counts(bus.history("hb-1"))

    def test_repairs_and_model_recoveries_are_counted_separately(self) -> None:
        from aci.domain.capability.errors import DomainError, ErrorCode
        from aci.domain.runtime.actions import FinalCandidate

        def unavailable(_request: object) -> object:
            raise DomainError(ErrorCode.MODEL_UNAVAILABLE, "503")

        counts = self._run(
            [unavailable, FinalCandidate(summary="try 1"), FinalCandidate(summary="try 2")],
            check_results=[False, True],
        )
        assert counts == {
            "verification_rounds": 2,
            "verification_fails": 1,
            "repairs": 1,
            "model_recoveries": 1,
            "tool_recoveries": 0,
        }

    def test_terminal_escalation_is_not_a_repair(self) -> None:
        from aci.domain.runtime.actions import FinalCandidate

        counts = self._run(
            [FinalCandidate(summary=f"try {i}") for i in range(3)],
            check_results=[False, False, False],
        )
        # 3 failed rounds: the first two became repair turns, the third
        # escalated (terminal) — it ended the run, it was not a repair.
        assert counts["verification_fails"] == 3
        assert counts["repairs"] == 2
        assert counts["model_recoveries"] == 0

    def test_tool_recoveries_are_not_counted_as_model_recoveries(self) -> None:
        """A transient tool failure emits RECOVERY_ACTION with
        component="tool_runtime" — it is a TOOL recovery, not a model one
        (the old split counted every non-verification recovery as model)."""
        from aci.domain.capability.errors import DomainError, ErrorCode
        from aci.domain.runtime.actions import FinalCandidate, ToolCall, ToolCallBatchAction
        from aci.domain.runtime.tools import ToolAuthority, ToolSpec
        from aci.runtime.protocols import ToolDispatchResult

        read = ToolSpec(
            tool_id="fs.read",
            version="1.0.0",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            side_effect_class="READ_ONLY",
            idempotency_class="IDEMPOTENT",
            authority_requirements=ToolAuthority(read_path_args=["path"]),
        )

        class Flaky:
            def __init__(self) -> None:
                self.failures = [ConnectionResetError("reset"), TimeoutError("slow")]

            def dispatch(self, tool: object, args: object, envelope: object) -> object:
                if self.failures:
                    raise self.failures.pop(0)
                return ToolDispatchResult(output="ok")

        def unavailable(_request: object) -> object:
            raise DomainError(ErrorCode.MODEL_UNAVAILABLE, "503")

        call = ToolCallBatchAction(
            calls=[ToolCall(call_id="c1", tool_id="fs.read", arguments={"path": "out/a.py"})]
        )
        counts = self._run(
            [call, unavailable, FinalCandidate(summary="done")],
            check_results=[True],
            tools=[read],
            dispatcher=Flaky(),
        )
        assert counts == {
            "verification_rounds": 1,
            "verification_fails": 0,
            "repairs": 0,
            "model_recoveries": 1,
            "tool_recoveries": 2,
        }

    def test_split_on_component_from_payload(self) -> None:
        from types import SimpleNamespace

        from aci.runtime.event_bus import RECOVERY_ACTION

        def ev(**payload: object) -> object:
            return SimpleNamespace(event_type=RECOVERY_ACTION, payload=payload)

        counts = run_hbench.mechanism_counts(
            [
                ev(failure_class="TRANSIENT_TOOL", action="RETRY_SAME", component="tool_runtime"),
                ev(failure_class="TOOL_TIMEOUT", action="FAIL", component="tool_runtime"),
                ev(failure_class="TRANSIENT_MODEL", action="RETRY_SAME", component="model_gateway"),
                ev(failure_class="VERIFICATION_FAILED", action="REPAIR", component="verifier"),
            ]
        )
        assert counts["tool_recoveries"] == 1  # the terminal FAIL is not a recovery taken
        assert counts["model_recoveries"] == 1
        assert counts["repairs"] == 1

    def test_no_mechanism_row_has_every_counted_key(self) -> None:
        """Arm N / crashed rows must carry the same columns the printer and
        aggregator read."""
        assert set(run_hbench._NO_MECHANISM) == set(run_hbench.mechanism_counts([]))


class TestTurnBudgetFairness:
    """§42: arm N gets the kernel's turn-budget note — identical text,
    identical threshold — so the A/B does not hand the signal to K only."""

    def test_naive_arm_uses_the_kernel_turn_budget_note(self) -> None:
        import inspect

        from aci.runtime import run_controller

        assert run_hbench.turn_budget_note is run_controller.turn_budget_note
        source = inspect.getsource(run_hbench.run_naive_arm)
        assert "turn_budget_note(max_turns - turns + 1)" in source

    def test_naive_arm_sends_the_note_on_its_last_two_turns(self, tmp_path: Path) -> None:
        """Drive run_naive_arm with a recording gateway: the note text the
        naive loop sends equals the kernel's, at the same turns."""
        from aci.domain.runtime.actions import ContinueAction
        from aci.runtime.model_gateway import ModelResponse, ModelUsage
        from aci.runtime.run_controller import turn_budget_note

        class Recorder:
            def __init__(self) -> None:
                self.requests: list[object] = []

            def invoke(self, request: object) -> ModelResponse:
                self.requests.append(request)
                return ModelResponse(
                    action=ContinueAction(),
                    raw_text="thinking",
                    usage=ModelUsage(input_tokens=1, output_tokens=1, latency_ms=1),
                )

        fixture = {"name": "fx", "prompt": "fix the bug"}
        (tmp_path / "src" / "fx").mkdir(parents=True)
        (tmp_path / "src" / "fx" / "a.py").write_text("x = 1\n", encoding="utf-8")
        contract, spec = run_hbench._contract_spec(fixture)
        gateway = Recorder()
        record = run_hbench.run_naive_arm(
            fixture,
            contract,
            spec,
            gateway,  # type: ignore[arg-type]
            tmp_path / "src",
            tmp_path / "runs",
            max_turns=4,
            sandbox=available_sandbox(),
        )
        assert record["turns"] == 4
        notes = [
            [m.content for m in r.messages if "Turn budget:" in m.content]  # type: ignore[attr-defined]
            for r in gateway.requests
        ]
        assert notes == [[], [], [turn_budget_note(2)], [turn_budget_note(1)]]
        # A system message right after the system prompt — the kernel's slot.
        last = gateway.requests[-1].messages  # type: ignore[attr-defined]
        assert last[1].role == "system" and last[1].content == turn_budget_note(1)
        # Per-request only: the note never accumulates in the history.
        assert sum("Turn budget:" in m.content for m in last) == 1


class _FakeACIClient:
    """ACIClient stand-in for the arm-S wiring tests: no network, no DB."""

    def search(self, request: object) -> list[object]:  # noqa: ARG002
        return []

    def resolve(self, capability_id: str, version: str) -> tuple[bytes, str]:  # noqa: ARG002
        return b"", f"sha256:{'0' * 64}"


class TestSkillsArm:
    """Arm S = arm K + the REAL registry capability plane, and NOTHING else
    different. §42 fairness pins: S's handler is advertised (the model IS
    offered request_capability), K stays on the null handler (offered
    nothing), and the first ModelRequest of the two arms differs by EXACTLY
    the capability tool + the capability protocol text — anything else would
    confound the S−K A/B."""

    def _first_request(self, tmp_path: Path, capability_factory: object | None) -> ModelRequest:
        """One kernel turn through the runner's OWN service builder (the same
        _kernel_service every K/S run goes through) — fake gateway, fake
        capability client, no network, no DB."""
        gateway = FakeModelGateway([ContinueAction()])
        sources = tmp_path / "src"
        fixture_dir = sources / "fx"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        (fixture_dir / "a.py").write_text("x = 1\n", encoding="utf-8")
        service = run_hbench._kernel_service(
            gateway,
            sources,
            tmp_path / "runs",
            EventBus(),
            sandbox=available_sandbox(),
            capability_factory=capability_factory,
        )
        contract, spec = run_hbench._contract_spec({"name": "fx", "prompt": "fix the bug"})
        service.run(contract, spec, max_turns=1, workspace="fx")
        assert len(gateway.requests) == 1
        return gateway.requests[0]

    def test_arm_s_wires_an_advertised_capability_handler(self, tmp_path: Path) -> None:
        """The handler arm S wires is the runner's counting runtime over a
        run-scoped client — advertised by the kernel's own rule, so the model
        IS offered the capability tool + protocol text."""
        handler = run_hbench._skills_capability_runtime(_FakeACIClient)
        assert callable(handler.handle_request)
        # The kernel's advertised test (run_controller): no advertised=False.
        assert bool(getattr(handler, "advertised", True))
        request = self._first_request(tmp_path, run_hbench._Factory(handler))
        assert "request_capability" in [t.tool_id for t in request.tools]
        assert '"type": "capability_request"' in request.messages[0].content

    def test_arm_k_keeps_the_null_handler(self, tmp_path: Path) -> None:
        """K's default wiring is unchanged: the null handler, not advertised —
        the model is offered no dead action."""
        service = run_hbench._kernel_service(
            FakeModelGateway([]), tmp_path, tmp_path, EventBus(), sandbox=available_sandbox()
        )
        handler = service._capability_factory.build()
        assert isinstance(handler, run_hbench._NullHandler)
        assert handler.advertised is False

    def test_s_k_protocol_difference_is_exactly_the_capability_plane(self, tmp_path: Path) -> None:
        """The arms' first ModelRequest differs by EXACTLY the synthetic
        request_capability tool (appended last) + the capability protocol
        text (appended to the system prompt) — same message count, same
        roles, every other message byte-identical."""
        from aci.runtime.run_controller import _CAPABILITY_PROTOCOL, CAPABILITY_TOOL

        k = self._first_request(tmp_path, None)
        s = self._first_request(
            tmp_path, run_hbench._Factory(run_hbench._skills_capability_runtime(_FakeACIClient))
        )
        assert [t.tool_id for t in s.tools] == [
            *[t.tool_id for t in k.tools],
            CAPABILITY_TOOL.tool_id,
        ]
        assert len(s.messages) == len(k.messages)
        for ms, mk in zip(s.messages, k.messages, strict=True):
            assert ms.role == mk.role
            assert ms.tool_call_id == mk.tool_call_id
        # The ONLY content difference: the capability protocol appended to
        # the system prompt (the first system message).
        assert s.messages[0].content == k.messages[0].content + "\n" + _CAPABILITY_PROTOCOL
        for ms, mk in zip(s.messages[1:], k.messages[1:], strict=True):
            assert ms.content == mk.content


class _OneSkillACIClient:
    """A fake registry client that routes every need to one skill."""

    BODY = b"# fx-skill\nRun pytest -x first.\n"

    class _Selection:
        capability_id = "cap.fx"
        version = "1"
        payload_ref = "skill://cap.fx@1/SKILL.md"
        estimated_context_tokens = 20

        def __init__(self, digest: str) -> None:
            self.digest = digest

    def __init__(self) -> None:
        import hashlib

        self._digest = hashlib.sha256(self.BODY).hexdigest()
        self.searches = 0

    def search(self, request: object) -> list[object]:  # noqa: ARG002
        self.searches += 1
        return [self._Selection(self._digest)]

    def resolve(self, capability_id: str, version: str) -> tuple[bytes, str]:  # noqa: ARG002
        return self.BODY, self._digest


class TestPreloadPlumbing:
    """The plumbing the preload arm needs: the runner's own service builder
    can turn the kernel's run-start preload on, and it stays OFF by default
    so K/N/S are unchanged. (The P arm itself is pinned in TestPreloadArm.)"""

    def test_kernel_service_defaults_preload_off(self, tmp_path: Path) -> None:
        service = run_hbench._kernel_service(
            FakeModelGateway([]), tmp_path, tmp_path, EventBus(), sandbox=available_sandbox()
        )
        assert service._preload_capabilities is False  # noqa: SLF001

    def test_kernel_service_can_preload_through_the_counting_runtime(self, tmp_path: Path) -> None:
        client = _OneSkillACIClient()
        runtime = run_hbench._skills_capability_runtime(lambda: client)
        gateway = FakeModelGateway([ContinueAction()])
        sources = tmp_path / "src"
        (sources / "fx").mkdir(parents=True)
        (sources / "fx" / "a.py").write_text("x = 1\n", encoding="utf-8")
        service = run_hbench._kernel_service(
            gateway,
            sources,
            tmp_path / "runs",
            EventBus(),
            sandbox=available_sandbox(),
            capability_factory=run_hbench._Factory(runtime),
            preload_capabilities=True,
        )
        contract, spec = run_hbench._contract_spec({"name": "fx", "prompt": "fix the bug"})
        service.run(contract, spec, max_turns=1, workspace="fx")
        assert "Run pytest -x first." in "\n".join(
            m.content
            for m in gateway.requests[0].messages  # type: ignore[attr-defined]
        )
        assert client.searches == 1
        # Preloaded skills are loaded skills, but NOT model requests.
        assert runtime.requests == 0
        assert runtime.preloaded == ["cap.fx@1"] and runtime.loaded == ["cap.fx@1"]


class TestPreloadArm:
    """Arm P = arm S + the kernel's run-start preload, NOTHING else different
    (§42): the dispatch pin — P reaches run_skills_arm with
    preload_capabilities=True while S does not — and the end-to-end pin —
    the FIRST ModelRequest of P already carries the preloaded skill text
    while S's does not. Together they make P−S a clean preload-only A/B."""

    def test_dispatch_p_preloads_and_s_does_not(self, tmp_path: Path, monkeypatch: Any) -> None:
        """`_run_one` routes arm P to run_skills_arm with the preload ON and
        arm S with the defaults (preload OFF) — and NOTHING else differs
        between the two dispatches."""
        calls: list[dict[str, Any]] = []

        def _record_call(*args: object, **kwargs: object) -> dict[str, Any]:
            calls.append({"args": args, "kwargs": kwargs})
            return {}

        monkeypatch.setattr(run_hbench, "run_skills_arm", _record_call)
        fixture = {"name": "fx", "prompt": "fix the bug"}
        container = object()
        for arm in ("S", "P"):
            run_hbench._run_one(
                fixture,
                arm,
                "http://gateway.invalid/v1",
                "some-model",
                "key",
                tmp_path / "src",
                tmp_path / "runs",
                max_turns=3,
                container=container,
            )
        (s_call, p_call) = calls
        assert p_call["kwargs"]["arm"] == "P"
        assert p_call["kwargs"]["preload_capabilities"] is True
        # S passes NEITHER — the run_skills_arm defaults hold (arm="S",
        # preload off), so S stays byte-identical to the pre-P runner.
        assert "arm" not in s_call["kwargs"]
        assert "preload_capabilities" not in s_call["kwargs"]
        # ...and the two dispatches share everything else.
        assert set(p_call["kwargs"]) == set(s_call["kwargs"]) | {"arm", "preload_capabilities"}
        for key, value in s_call["kwargs"].items():
            assert p_call["kwargs"][key] == value

    def _skills_arm_run(
        self, tmp_path: Path, *, arm: str, preload: bool
    ) -> tuple[dict[str, Any], FakeModelGateway, _OneSkillACIClient]:
        """One FULL run_skills_arm run over fakes — no network, no DB: the
        real kernel path (workspace copy, at-limit verification, post-hoc
        yardstick) with a fake gateway and a fake advertised registry
        client, exactly the plane arm S/P wires."""
        client = _OneSkillACIClient()
        container = SimpleNamespace(agent_capability_clients=lambda: client)
        gateway = FakeModelGateway([ContinueAction()])
        sources = tmp_path / "src"
        (sources / "fx").mkdir(parents=True, exist_ok=True)
        (sources / "fx" / "a.py").write_text("x = 1\n", encoding="utf-8")
        fixture = {"name": "fx", "prompt": "fix the bug"}
        contract, spec = run_hbench._contract_spec(fixture)
        record = run_hbench.run_skills_arm(
            fixture,
            contract,
            spec,
            gateway,  # type: ignore[arg-type]
            sources,
            tmp_path / "runs",
            max_turns=1,
            container=container,
            sandbox=available_sandbox(),
            arm=arm,
            preload_capabilities=preload,
        )
        return record, gateway, client

    def test_first_request_of_p_carries_the_preloaded_skill_s_does_not(
        self, tmp_path: Path
    ) -> None:
        """The whole P−S difference, on the wire: both arms are offered the
        identical capability plane (same tools, same protocol), but P's FIRST
        ModelRequest already contains the rendered preloaded skill — S's
        does not, because the model never asks."""
        p_record, p_gateway, p_client = self._skills_arm_run(tmp_path, arm="P", preload=True)
        s_record, s_gateway, s_client = self._skills_arm_run(tmp_path, arm="S", preload=False)

        # The plane is offered IDENTICALLY in both arms.
        assert [t.tool_id for t in p_gateway.requests[0].tools] == [
            t.tool_id for t in s_gateway.requests[0].tools
        ]
        assert "request_capability" in [t.tool_id for t in s_gateway.requests[0].tools]

        # P's first request = S's first request + EXACTLY ONE system message:
        # the rendered preloaded skill (§42 — nothing else differs).
        p_first = p_gateway.requests[0].messages
        s_first = s_gateway.requests[0].messages
        skill_at = [i for i, m in enumerate(p_first) if "Run pytest -x first." in m.content]
        assert len(skill_at) == 1
        i = skill_at[0]
        assert p_first[i].role == "system"
        assert "<<<BEGIN SKILL REFERENCE cap.fx@1" in p_first[i].content
        assert p_first[:i] + p_first[i + 1 :] == s_first
        assert all("Run pytest -x first." not in m.content for m in s_first)

        # The counters: a preload is a loaded skill, NOT a model request.
        assert p_record["arm"] == "P" and s_record["arm"] == "S"
        assert p_record["skills_preloaded"] == ["cap.fx@1"]
        assert p_record["skills_loaded"] == ["cap.fx@1"]
        assert p_record["capability_requests"] == 0
        assert s_record["skills_preloaded"] == []
        assert s_record["skills_loaded"] == []
        assert s_record["capability_requests"] == 0
        assert p_client.searches == 1  # the preload routed once, at run start
        assert s_client.searches == 0  # offered, never used
