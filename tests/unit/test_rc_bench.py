"""Unit tests for scripts/rc_bench.py's bench-side isolation wiring (job
xl-fix, 2026-10-03). rc_bench now goes through the ONE shared sandbox helper
(``bench_sandbox``): the sandbox has NO network (``--unshare-net``) and each
arm gets only its unix-socket bridges — the model gateway for every arm, the
ACI REST server for OC-A only, the ACI MCP server for G-A only. These tests
pin the endpoint computation and the wiring; the real bwrap/socat check runs
on the Linux host (the lead's).

The red state: rc_bench._bwrap had no ``--unshare-net`` and no bridges — a
run could reach ANY localhost service (rc-bench v2 caught one
port-scanning its way to the ACI server and downloading the skill).
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import rc_bench as rb  # noqa: E402

GW = ("localhost", 20128)


def test_gateway_endpoint_parses_host_and_port() -> None:
    assert rb._gateway_endpoint("http://localhost:20128/v1") == ("localhost", 20128)
    assert rb._gateway_endpoint("http://127.0.0.1:9999") == ("127.0.0.1", 9999)


def test_every_arm_gets_the_gateway_bridge() -> None:
    for arm in rb.ARMS:
        assert rb._arm_endpoints(arm, GW)[0] == ("localhost", 20128, "gateway"), arm


def test_only_oc_a_gets_the_rest_bridge() -> None:
    """The ACI REST server (:8010) is bridged in for OC-A ONLY — its plugin
    routes through it. Every other arm must not even see the port."""
    assert rb._arm_endpoints("OC-A", GW) == [
        ("localhost", 20128, "gateway"),
        ("127.0.0.1", 8010, "rest"),
    ]
    for arm in ("OC-N", "OC-S", "G-N"):
        assert rb._arm_endpoints(arm, GW) == [("localhost", 20128, "gateway")], arm


def test_only_g_a_gets_the_mcp_bridge() -> None:
    assert rb._arm_endpoints("G-A", GW) == [
        ("localhost", 20128, "gateway"),
        ("127.0.0.1", 8011, "mcp"),
    ]


def test_run_one_goes_through_the_shared_sandbox() -> None:
    """The wiring pin: run_one calls bench_sandbox.run_sandboxed with the
    arm's endpoints and the wall cap (network isolation + whole-group
    SIGKILL on timeout — never a bare subprocess.run)."""
    source = (SCRIPTS / "rc_bench.py").read_text(encoding="utf-8")
    assert "run_sandboxed(" in source
    assert "endpoints=_arm_endpoints(arm, _gateway_endpoint(base_url))" in source
    assert "timeout=WALL_CAP" in source
    # the old inline bwrap is gone — ONE shared helper, not a local copy
    assert "def _bwrap" not in source
    assert '"--unshare-net"' not in source  # it lives in bench_sandbox, tested there


def test_forbidden_scan_still_covers_the_unbridged_ports() -> None:
    """Belt and suspenders: even with --unshare-net, transcripts are still
    scanned for attempts at the DB / docker / sibling paths."""
    for needle in (":8010", ":8011", "5432", "psql", "docker exec", ".snapshots/"):
        assert needle in rb.FORBIDDEN, needle
