# ADR-006 — MCP Skills extension cho skill transport; ít tool ổn định

- Status: Accepted (2026-09-27, khóa từ `plan_v2_revised.md` §§29, 29.1–29.5, 68).
- Scope: MCP adapter (Phase 12).

## Context

MCP cũ mô hình hóa mỗi skill thành một tool → hàng trăm tool không ổn định, body khổng lồ
trong kết quả, và phụ thuộc session state phía server.

## Decision

- Skill thủ tục expose qua **MCP Skills extension** (`skills/list`, `skills/get`,
  `resources/read`) nơi client hỗ trợ; file skill giữ digest-verifiable + version-bound.
- Chỉ giữ tool nhỏ ổn định: `route_capabilities`, `report_outcome` (tùy chọn
  `search_capabilities`). **Cấm một tool cho mỗi skill.**
- Request stateless; state bền chỉ qua `request_id / route_run_id / bundle_id`;
  không phụ thuộc session MCP tồn tại.
- Không nhồi body vào tool result khi client lazy fetch được.

## Consequences

- Core domain không có type MCP (ADR-004); đổi revision MCP = sửa adapter.
- Test protocol §62: negotiation, stateless, skills listing/loading, resource integrity.

## Rejected

- Một MCP tool mỗi skill; state trong session server; MCP bắt buộc cho OpenCode.

## Verification

- Generic MCP test client route + load được production skill mà core không import MCP SDK.
