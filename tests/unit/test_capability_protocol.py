"""capability_request must be OFFERED to the model when a capability plane is
wired (a real run with glm-5.3 on 2026-10-01 never requested a skill: the
action protocol did not mention the action at all), and must NOT be offered
when the handler can never load anything (no dead action; H-bench keeps the
arm-K and arm-N protocols identical)."""

import sys
from pathlib import Path
from typing import Any

from aci.adapters.inbound.rest.agent_run_wiring import _NullCapabilityRuntime
from aci.domain.runtime.actions import FinalCandidate
from aci.runtime.context_engine import ContextBudget, ContextEngine
from aci.runtime.event_bus import EventBus
from aci.runtime.model_gateway import FakeModelGateway, ModelRequest
from aci.runtime.recovery import RecoveryManager
from aci.runtime.run_controller import HarnessKernel
from aci.runtime.state_manager import StateManager
from aci.runtime.verification import VerificationManager
from tests.unit.test_turn_budget_signal import _contract, _NoTools, _spec

MARKER = '"type": "capability_request"'


class _RealishHandler:
    """Stands in for CapabilityRuntime: no ``advertised`` attribute."""

    def handle_request(self, request: object, snapshot: object) -> list[object]:
        return []


def _first_request(handler: Any) -> ModelRequest:
    model = FakeModelGateway([FinalCandidate(summary="done")])
    kernel = HarnessKernel(
        state=StateManager(),
        model_gateway=model,
        tool_executor=_NoTools(),
        context_engine=ContextEngine(ContextBudget(total_tokens=60_000)),
        verifier=VerificationManager([]),
        recovery=RecoveryManager(),
        capability_runtime=handler,
        event_bus=EventBus(),
        sleep=lambda _s: None,
    )
    kernel.run(_contract(), _spec(), max_turns=1)
    return model.requests[0]


def _system_text(request: ModelRequest) -> str:
    return "\n".join(m.content for m in request.messages if m.role == "system")


def test_capability_request_is_offered_when_a_plane_is_wired() -> None:
    text = _system_text(_first_request(_RealishHandler()))
    assert MARKER in text
    assert "never grants extra tools or permissions" in text


def test_capability_request_is_not_offered_without_a_plane() -> None:
    assert MARKER not in _system_text(_first_request(_NullCapabilityRuntime()))


def test_hbench_null_handler_does_not_offer_it() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import run_hbench

    assert MARKER not in _system_text(_first_request(run_hbench._NullHandler()))
