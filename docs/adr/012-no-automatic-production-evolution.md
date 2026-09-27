# ADR-012 — No automatic production evolution; promotion qua gates

- Status: Accepted (2026-09-27, khóa từ `plan_v2_revised.md` §§24, 37, 37.1, 38, 58, 68).
- Scope: control plane promotion (Phase 4+), mọi phase sau.

## Context

Upstream update tự thay production, hoặc telemetry tự sửa skill, phá vỡ tính bất biến
và provenance — một upstream bị compromise sẽ ghi đè production ngay.

## Decision

- **Không bao giờ** `upstream update → automatic production replacement`;
  thay đổi upstream chỉ tạo ingestion candidate mới (§38).
- Vòng an toàn duy nhất: `telemetry → proposal → benchmark → security/policy review →
  promotion` (§58). Không bao giờ `telemetry → tự sửa production`.
- Promotion sang **production** bắt buộc qua gates: provenance chain tồn tại,
  license assessment cho phép redistribution (**unknown license chặn mặc định**, §24),
  security assessment passed (§52 Phase 4 acceptance).
- Rollback/revoke = đổi release pointer/state, không rebuild artifact (§37.1).
- License policy là eligibility control-plane, không phải phán quyết LLM.

## Consequences

- Unreviewed raw skill không thể lên production; mọi production version truy được
  nguồn gốc + digest.
- Benchmark gate (§37) sẽ thêm ở Phase 14 trước khi mở rộng corpus.

## Rejected

- Auto-promote từ telemetry; folder-move làm lifecycle; license chỉ là text field.

## Verification

- Phase 4 test: promote production thiếu license/security/provenance → `POLICY_DENIED`;
  unknown license → blocked; rollback chỉ đổi pointer.
