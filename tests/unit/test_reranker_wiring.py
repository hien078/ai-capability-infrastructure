"""Reranker selection wiring (docs/plans/jev-reranker.md §2.3, spec task 4).

``ACI_RERANKER`` default stays ``heuristic`` (the §4 promotion gate is
pending); ``jev`` FAILS CLOSED at startup when the judge endpoint is not
fully configured — never a silent fallback to the heuristic (it is the
measured source of harm). The SAME reranker instance must back both the
route service and the benchmark harness so DEV_CASES measures what
production runs.
"""

import pytest

from aci.adapters.inbound.rest.wiring import Container, _build_reranker
from aci.config import Settings
from aci.routing.rerankers.heuristic import HeuristicReranker
from aci.routing.rerankers.jev import JevReranker

UNREACHABLE_DB = "postgresql+psycopg://aci:aci@127.0.0.1:9/aci"


def test_default_reranker_is_the_heuristic() -> None:
    reranker = _build_reranker(Settings())
    assert isinstance(reranker, HeuristicReranker)


def test_unknown_reranker_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown ACI_RERANKER"):
        _build_reranker(Settings(reranker="bogus"))


def test_jev_without_config_fails_closed_naming_the_settings() -> None:
    with pytest.raises(ValueError) as excinfo:
        _build_reranker(Settings(reranker="jev"))
    message = str(excinfo.value)
    assert "ACI_JEV_BASE_URL" in message
    assert "ACI_JEV_API_KEY" in message


def test_jev_with_base_url_but_no_key_names_the_key() -> None:
    with pytest.raises(ValueError, match="ACI_JEV_API_KEY"):
        _build_reranker(Settings(reranker="jev", jev_base_url="http://judge.local/v1"))


def test_jev_with_no_model_names_the_model() -> None:
    with pytest.raises(ValueError, match="ACI_JEV_MODEL"):
        _build_reranker(
            Settings(
                reranker="jev",
                jev_base_url="http://judge.local/v1",
                jev_api_key="sk-x",
                jev_model="",
            )
        )


def test_jev_unknown_on_failure_is_rejected() -> None:
    with pytest.raises(ValueError, match="ACI_JEV_ON_FAILURE"):
        _build_reranker(
            Settings(
                reranker="jev",
                jev_base_url="http://judge.local/v1",
                jev_api_key="sk-x",
                jev_on_failure="bogus",
            )
        )


def test_jev_wires_the_judge_with_settings() -> None:
    reranker = _build_reranker(
        Settings(
            reranker="jev",
            jev_base_url="http://judge.local/v1",
            jev_api_key="sk-x",
            jev_model="OneNexus/glm-5.3",
            jev_reasoning_effort="low",
            jev_timeout_seconds=8.0,
            jev_candidates=12,
            jev_max_select=2,
        )
    )
    assert isinstance(reranker, JevReranker)
    # Fail-safe default: abstain, no fallback reranker.
    verdict_reranker = reranker
    assert verdict_reranker._on_failure == "abstain"
    assert verdict_reranker._fallback is None


def test_jev_on_failure_heuristic_wires_the_fallback() -> None:
    reranker = _build_reranker(
        Settings(
            reranker="jev",
            jev_base_url="http://judge.local/v1",
            jev_api_key="sk-x",
            jev_on_failure="heuristic",
        )
    )
    assert isinstance(reranker, JevReranker)
    assert reranker._on_failure == "heuristic"
    assert isinstance(reranker._fallback, HeuristicReranker)


def test_container_uses_the_same_reranker_for_routes_and_benchmark(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    """§2.3: BOTH RouteCapabilitiesService and BenchmarkHarness measure the
    same reranker — DEV_CASES must not silently run a different router.

    Building the Container needs no live DB (engine is lazy — same pattern
    as tests/unit/test_mcp_http_lifespan.py).
    """
    container = Container(
        Settings(
            reranker="jev",
            jev_base_url="http://judge.local/v1",
            jev_api_key="sk-x",
            database_url=UNREACHABLE_DB,
            object_store_root=str(tmp_path / "objects"),
        )
    )
    route_reranker = container.route_service._reranker
    bench_reranker = container.benchmark_harness._reranker
    assert isinstance(route_reranker, JevReranker)
    assert route_reranker is bench_reranker


def test_container_default_settings_wire_the_heuristic(tmp_path) -> None:  # type: ignore[no-untyped-def]
    container = Container(
        Settings(
            database_url=UNREACHABLE_DB,
            object_store_root=str(tmp_path / "objects"),
        )
    )
    assert isinstance(container.route_service._reranker, HeuristicReranker)
    assert isinstance(container.benchmark_harness._reranker, HeuristicReranker)
