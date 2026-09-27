# ADR-002 — Registry là source of truth duy nhất; tách Version / Release / Binding / Metrics

- Status: Accepted (2026-09-27, khóa từ `plan_v2_revised.md` §§2.4–2.5, 6, 10, 21, 39, 41, 74).
- Scope: mọi lifecycle, persistence, promotion/rollback/revocation, cache, benchmark trong V1.

## Context

Phiên bản gốc (và `plan.md` cũ) trộn identity, nội dung bất biến, trạng thái release, binding protocol,
và metrics dẫn xuất vào một record, kèm một Skill Registry / Agent Registry riêng.
Hệ quả: telemetry làm thay đổi lịch sử, promotion ghi đè nội dung, hai registry mâu thuẫn lifecycle,
thư mục filesystem (`corpus/raw`, …) bị hiểu nhầm là trạng thái authoritative, rollback phải rebuild artifact.

## Decision

- Đúng một Capability Registry authoritative cho mọi kind (skill, service, agent, …).
  Provider kind-specific (SkillProvider, …) chỉ tham chiếu `capability_id + version`, không sở hữu lifecycle riêng.
- Tách bốn lớp bản ghi:
  - `Capability` — identity logic ổn định;
  - `CapabilityVersion` — nội dung bất biến sau publish (định danh bằng `capability_id + version`
    + `content_digest` SHA-256, `UNIQUE(capability_id, version)`);
  - `CapabilityRelease` — con trỏ channel/state mutable (`raw → candidate → canonical → staging → production`;
    `active / disabled / deprecated / revoked`);
  - `CapabilityBinding` — cách version/release được expose qua từng protocol (OpenCode catalog, MCP Skills, REST, A2A…);
  - `CapabilityMetrics` — dẫn xuất từ quan sát, không bao giờ mutate version lịch sử.
- Promotion/rollback/revocation chỉ đổi con trỏ/state release; DB release state là authoritative,
  thư mục filesystem chỉ để con người tiện quan sát. Bundle item FK tới version chính xác + digest pin.

## Consequences

- Rollback không cần rebuild artifact; version thu hồi biến mất khỏi route ngay sau khi có hiệu lực.
- Cache an toàn chỉ cho nội dung bất biến theo khóa version/digest; revocation phải bypass/invalidate cache.
- Mọi recommendation trace được tới version + digest + policy snapshot chính xác.

## Rejected

- Registry riêng cho skill/agent/service với lifecycle mâu thuẫn.
- Một object chứa gộp metrics/status/binding vào version.
- Thư mục filesystem là source of truth cho lifecycle.
- Upstream update tự động thay thế production (mọi thay đổi upstream chỉ tạo ingestion candidate mới).

## Verification

- `CapabilityVersion` frozen trong `src/aci/domain/capability/models.py`; `CapabilityRelease` mutable;
  `CapabilityMetrics` là frozen snapshot dẫn xuất, không field metric nào nằm trên `CapabilityVersion`.
- `tests/unit/test_contracts.py::test_version_is_immutable`,
  `test_release_promotion_does_not_mutate_version`, `test_metrics_are_frozen_snapshots_separate_from_version`,
  `test_version_carries_faceted_taxonomy`.
- `tests/unit/test_architecture_boundaries.py::test_registry_separation_version_release_binding`.
- Migrations từ ngày đầu phải enforce `UNIQUE(capability_id, version)` và FK bundle→version (plan §41).
