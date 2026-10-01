# ADR-008 — Zero-item bundle hợp lệ; composer tối thiểu

- Status: Accepted (2026-09-27, khóa từ `plan_v2_revised.md` §§2.7, 19, 60.8, 68).
- Scope: composer (Phase 8).

## Context

Ép router luôn trả 1–5 skill khi confidence kém tạo nhiễu context và hành vi sai
tệ hơn không trả gì.

## Decision

- Bundle hợp lệ có **0..N items, thường N ≤ 5**; empty bundle là kết quả route thành công.
- Composer tối ưu **minimal sufficient context**: `max(expected utility − context cost −
  conflict risk − latency cost)`; một skill mạnh có thể tốt hơn năm skill vừa phải.
- Budget bắt buộc: `max_items ≤ 5`, `max_context_tokens` (mặc định 6000),
  version + digest pin từng item, validation trước khi trả.
  - Amendment 2026-10-01: composer v2 tính cost theo kích thước thật của entry
    file (artifact manifest `size_bytes`, chỉ metadata) thay vì độ dài routing
    summary — budget trước đó không bao giờ bind. Mặc định nâng lên **8000**
    (`DEFAULT_MAX_CONTEXT_TOKENS`, một chỗ duy nhất): DEV_CASES paired replay
    6000 → hit 28/31, 8000 → 30/31; 8000 vượt p90 kích thước một skill (~6.7k).
    Budget vẫn strict — không ép item hạng 1 vượt budget (xem Rejected).
- Quan hệ V1 chỉ `REQUIRES / CONFLICTS_WITH / CHECKS` (§18); không graph DB.

## Consequences

- Test phải cover empty-bundle là success, không conflict, dependency hợp lệ,
  budget được enforce (§52 Phase 8 acceptance).

## Rejected

- Luôn trả ≥1 skill; max-count thay vì min-sufficient; graph database trong V1.

## Verification

- `test_bundle_allows_zero_items_and_rejects_six` (unit, đã có);
  composer test Phase 8 sẽ khóa hành vi này end-to-end.
