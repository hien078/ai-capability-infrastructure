"""Harness runtime domain contracts (docs/plans/harness.md, PART II).

ACI Capability Intelligence decides WHAT reusable capability is appropriate;
the HarnessKernel decides HOW one delegated objective is executed; the client
global orchestrator owns the global goal and global Task DAG (§0.1).

This package is pure Pydantic data — no framework imports (boundary tests
enforce this like every other domain package).
"""
