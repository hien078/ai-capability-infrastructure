# ADR-013 — Automation đến staging + canary; human giữ production

- Status: Accepted (2026-09-29, từ `docs/plans/auto_acquisition.md`, `auto2_harvesting.md`,
  `crawl_adaptive_acquisition.md` — user-authored; user decision 2026-09-29).
- Scope: automated acquisition lifecycle, canary releases, eligibility routing.
- Refines: ADR-012 (giữ nguyên bất biến cốt lõi — xem Decision).

## Context

V4 real-client era cần vòng thêm skill tự động (weekly cycle) để corpus sống theo nhu cầu
thật, nhưng ADR-012 cấm `upstream update → automatic production replacement` vì một
upstream bị compromise sẽ ghi đè production. Câu hỏi: tự động hóa đến đâu mà không phá
bất biến đó? Câu trả lời nằm ở chỗ **staging không phải exposure** — routing, catalog,
MCP đều chỉ đọc production — nên automation có thể dừng ở staging mà vẫn hữu ích;
và canary cho phép đo một version MỚI trên traffic thật trước khi nhận full share.

## Decision

1. **Hai trust tier** (`config/sources.yaml`):
   - **known** (nguồn đã content-review: anthropics/superpowers/…): crawler →
     auto source+fetch approve → fetcher → quarantine → gates → risk LOW →
     **auto-staging**. Không chạm người cho tới promotion.
   - **unreviewed** (scout tìm, repo lạ): MỌI cổng human (`capctl source approve`
     + fetch approve + promotion approve). Không tự động gì.
2. **Automation dừng ở staging.** Staging invisible với runtime (routing/catalog/MCP
   đọc production only) — auto-staging không phải exposure. **Cổng human duy nhất
   còn lại (cả hai tier): staging → production qua `capctl promotion approve` với
   §24 evidence bundle trước mắt người.** ADR-012 giữ nguyên: production chỉ
   reachable qua human approve.
3. **Canary promotion chạy ĐẦY ĐỦ gates** — `promote_canary()` gọi lại
   `prerequisites()` (provenance + license + security) như `promote()`; không có
   đường bypass. Canary chỉ khác ở `status='canary'` + `canary_percent` (0–100,
   ngoài range → `POLICY_DENIED`).
4. **Deterministic canary split**: `hash(request_id, capability_id) % 100 <
   canary_percent` — cùng request luôn cùng quyết định; population canary tái lập
   được để so telemetry. **Fail-closed**: canary thiếu `canary_percent` → excluded
   khỏi eligibility (không bao giờ route full share một release hỏng).
5. **Monitor chỉ rollback, không graduate**: so verified outcomes (test_harness +
   static_analysis) của canary với incumbent baseline; regression → flip
   `status='disabled'` — **pointer move only**, artifact giữ lại cho audit (§30),
   không bao giờ overwrite `approved_by` (người promote stays on record; rollback
   chính là audit event). **Graduation sang active là human** (`capctl`).
6. **Rollback policy** (`canary_monitor.py`): tối thiểu 3 verified outcomes mới quyết
   (`insufficient` dưới ngưỡng); có baseline → relative (fail-rate canary >
   incumbent + 20%); không baseline → absolute (fail-rate > 50%). Version không
   evidence không đóng góp gì — margin không thể pass một cách vacuous.

## Consequences

- Weekly cycle chạy tự động với đúng MỘT human touchpoint (promotion approve) —
  corpus lớn theo demand mà không mở lỗng governance.
- Canary cho phép đo version mới trên traffic thật TRƯỚC full exposure; rollback
  tự động trong vài phút thay vì chờ human thấy telemetry xấu.
- Staging giờ là trạng thái "đã qua gates, chờ human" — ý nghĩa thay đổi từ
  "pre-production thử nghiệm" sang "hàng đợi promotion"; cần giữ routing/catalog/MCP
  đọc production only mãi mãi (đã test trong §61 exposure boundaries).
- Monitor là automation thứ hai chạm release state (sau promotion) — mọi thao tác
  của nó phải pointer-only và auditable.

## Rejected

- Full auto-promotion sang production (phạm ADR-012).
- Random/probabilistic canary split (population không tái lập được → telemetry
  không so được).
- Monitor tự graduate canary sang active (một metric tốt không phải bằng chứng
  exposure an toàn — graduation là phán quyết risk, giữ human).
- Canary bỏ qua gates "vì đã qua staging rồi" (staging gates ≠ production gates;
  §24 evidence bundle phải nằm trước mắt human ở đúng cổng promotion).

## Verification

- `tests/unit/test_canary.py` — deterministic split, fail-closed thiếu percent.
- `tests/unit/test_canary_promotion.py` — `promote_canary` thiếu gate → `POLICY_DENIED`;
  range 0–100 enforced.
- `tests/integration/test_canary_monitor.py` — rollback decision table (insufficient /
  relative / absolute), pointer-only update, `approved_by` không đổi.
- `tests/unit/test_eligibility.py` + `test_eligibility_flow.py` — canary share routing
  trong eligibility (ADR-009: reranker không rescue excluded content).
- Live: `alembic upgrade head` → `0014_canary`; cả hai DB ở head; `alembic check` sạch.
