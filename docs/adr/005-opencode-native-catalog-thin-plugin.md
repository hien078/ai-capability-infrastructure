# ADR-005 — OpenCode native skill catalog + thin router plugin

- Status: Accepted (2026-09-27, khóa từ `plan_v2_revised.md` §§28, 28.1–28.4, 68).
- Scope: tích hợp OpenCode (Phase 10–11).

## Context

Cách tích hợp cũ (plan.md) đẩy toàn bộ body skill qua MCP tool response, làm phình context
và tạo bản sao skill cục bộ phải maintain tay. OpenCode V2 có catalog HTTP + lazy load riêng.

## Decision

- OpenCode là client đặc biệt, không phải trung tâm hệ thống: tích hợp qua **thin plugin**
  `prompt → TaskContext tối thiểu → POST /v1/routes → inject 0–5 skill ID → native lazy load
  từ `GET /opencode/skills/index.json` + `GET /opencode/skills/{id}/{file}` → thực thi local
  → POST /v1/outcomes`.
- Platform giữ trí tuệ (eligibility/retrieval/rerank/composer/version pin);
  OpenCode giữ host behavior (resolution, context, tools, permissions, loop).
- Catalog HTTP là **projection** từ registry/release state, không phải source of truth thứ hai.
- `autoinvoke:false` mặc định (progressive disclosure); fail-open mặc định,
  fail-closed chỉ cho flow compliance.

## Consequences

- Không có bản sao skill cục bộ phải sync tay; chỉ skill được chọn mới vào context.
- Routing outage không treo prompt admission (fail-open + trace riêng).

## Rejected

- Nhồi body skill vào MCP tool result cho OpenCode.
- Catalog song song có lifecycle riêng.

## Verification

- Catalog index derive 100% từ `capability_releases` + `capability_bindings` (Phase 10).
- Plugin không chứa retrieval/rerank logic; test protocol §62 (catalog format, lazy load,
  fail-open/closed).
