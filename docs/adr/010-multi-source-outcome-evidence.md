# ADR-010 — Outcome evidence đa nguồn; unknown giữ nguyên unknown

- Status: Accepted (2026-09-27, khóa từ `plan_v2_revised.md` §§33, 33.1, 60.9, 68).
- Scope: outcome ingestion (Phase 13).

## Context

`{"success": true}` tự báo làm méo mọi phân tích: agent tự khen, test harness thật,
và human review bị nén thành một boolean trước khi lưu.

## Decision

- Verdict lưu **tách theo nguồn** với confidence: `agent_self_report, client_report,
  test_harness, static_analysis, external_evaluator, human_review, production_signal`.
- Không gộp thành một boolean trước khi lưu; `unknown` giữ nguyên `unknown`
  (không biến thiếu evidence thành success).
- Outcome join được về `route_run_id → bundle_id → exact version + digest`.
- Evidence không trực tiếp promote version (ADR-003); vòng an toàn duy nhất:
  telemetry → proposal → benchmark → review → promotion.

## Consequences

- Phân tích sau này tính được verified-success-rate thật, tách khỏi self-report.
- Telemetry redact secret/code (§25.4, §66).

## Rejected

- Một boolean tổng hợp; unknown → success mặc định.

## Verification

- `OutcomeEvidence` yêu cầu ≥1 verdict có nguồn (unit, đã có);
  Phase 13 test: self-report và test-harness lưu tách dòng.
