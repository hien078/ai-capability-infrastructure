"""Evaluation layer (plan §42, §52 Phase 14): benchmark harness + metrics.

Flat for V1 — models, harness, metrics, and the smoke case set live here;
the §42 subdirs (benchmarks/harness/evaluators/reports) arrive when the
layer grows. Imports application/routing components but never adapters
(ADR-004): persistence goes through the ``BenchmarkStore`` protocol.
"""
