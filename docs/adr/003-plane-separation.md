# ADR-003 — Phân tách Data / Control / Observability planes

- Status: Accepted (2026-09-27, khóa từ `plan_v2_revised.md` §§0, 3–4, 33–37, 63–64, 74).
- Scope: request flow, promotion pipeline, telemetry trong V1.

## Context

Nếu control plane (ingestion, review, promotion) nằm đồng bộ trong mỗi route request,
hoặc observability trực tiếp promote version, hệ thống sẽ chậm, khó推理,
và telemetry nhiễu có thể tự ý mutate production.

## Decision

- **Data Plane** phục vụ request live (search, route, resolve, read artifact): read-mostly,
  latency thấp, policy enforcement định trị, output version-pinned, context bounded,
  không mutate nội dung, mọi quyết định trace được. Thứ tự cố định:
  eligibility → retrieval → rerank → dependency resolve → composer → validation → pin.
- **Control Plane** là nơi duy nhất thay đổi cái gì được phép ở production
  (source registry → ingestion → quarantine → normalize → security/license →
  benchmark → review → release/promote; rollback/revoke/deprecate).
  Control plane KHÔNG phải hop đồng bộ trong route request.
- **Observability Plane** nhận traces, candidate sets, bundle, execution events, verdicts đa nguồn,
  cost/latency — chỉ tạo evidence. Evidence không trực tiếp promote version;
  vòng an toàn duy nhất là telemetry → proposal → benchmark → review → promotion.
  Không bao giờ `telemetry → tự sửa production`.

## Consequences

- Router outage / vector index / LLM reranker down có fallback và degraded-mode trace riêng,
  không treo prompt admission (fail-open mặc định; fail-closed chỉ cho flow compliance).
- `verdict = unknown` khi thiếu evidence — không biến "không có evidence" thành success.
- Benchmark đánh giá outcome end-to-end có đối chứng (A–E), không chỉ điểm router.

## Rejected

- Control plane đồng bộ trong data-plane request path.
- Observability tự promote/mutate production.
- Gộp success nhiều nguồn thành một boolean trước khi lưu.

## Verification

- Mọi stage routing phát trace (`route.*`, `capability.*`, `outcome.*`, `release.*` — plan §63).
- `tests/unit/test_architecture_boundaries.py::test_plane_separation_no_control_import_in_route_contracts`.
- V1 exit criteria §§54/79: route trace ghi đủ candidates/bundle/version/router-version;
  outcome phân biệt self-report với verified evidence.
