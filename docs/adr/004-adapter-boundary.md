# ADR-004 — Adapter boundary: domain độc lập client/protocol; edge mỏng

- Status: Accepted (2026-09-27, khóa từ `plan_v2_revised.md` §§2.1–2.2, 2.8, 13, 25, 28–31, 44, 74, 77).
- Scope: mọi module domain/application và mọi adapter trong V1.

## Context

Lõi domain phải thay được OpenCode, gỡ/bổ sung MCP, nâng protocol mà không viết lại
identity/version/routing. Ngược lại, "Capability Gateway" ôm auth + routing + protocol
sẽ thành god object; nhét type OpenCode/MCP/A2A/FastAPI/SQLAlchemy vào domain sẽ khóa lõi
vào một client và một transport.

## Decision

- Domain/application chỉ phụ thuộc Protocol/interface
  (`CapabilityRepository`, `EligibilityPolicy`, `CandidateRetriever`, `CapabilityReranker`,
  `DependencyResolver`, `BundleComposer`, `OutcomeRecorder`, …),
  không import type OpenCode/MCP/A2A/FastAPI/SQLAlchemy.
- Cấu trúc `Edge → Application Service → Domain Core`. Edge (kể cả "gateway" triển khai) giữ mỏng:
  authenticate, lập `RequestContext`, validate transport input, rate-limit thô,
  dịch representation protocol, gọi application service, dịch ngược.
  Edge KHÔNG chứa retrieval/rerank/promotion/canonicalization/business-rule của client khác.
- Client-specific code nằm ở adapter: `adapters/inbound/opencode/`, `adapters/inbound/mcp/`,
  `adapters/inbound/rest/`, (A2A để tương lai). OpenCode là client đặc biệt: thin plugin
  `prompt → TaskContext tối thiểu → POST /v1/routes → inject 0–5 skill ID → native lazy load
  từ HTTP catalog → thực thi local → POST /v1/outcomes`; OpenCode sở hữu
  filesystem/shell/git/secret/permission/loop — skill không bao giờ cấp quyền host.
- Mô hình protocol: MCP = use/read/invoke, A2A = delegate qua biên agent tự trị
  (V1 không có A2A runtime; không dùng A2A cho gọi hàm nội bộ). SDK chỉ wrap REST/MCP/A2A,
  không phải protocol ngang hàng. MCP stateless, state bền qua
  `request_id / route_run_id / bundle_id`. Thay đổi protocol chỉ sửa adapter.
- Routing chỉ nhận `TaskContext` tối thiểu; cấm gửi `.env`/token/SSH key/credential/source dump.

## Consequences

- Thay OpenCode, gỡ MCP, đổi revision MCP/OpenCode V2 không đòi migrate registry hay viết lại routing
  (Invariants 1–2, 13; kiểm chứng §79).
- Mọi protocol mới thêm được mà không đổi schema registry về cơ bản.

## Rejected

- God-object "Capability Gateway" chứa routing + promotion + canonicalization.
- Domain import SDK/type của OpenCode/MCP/A2A/FastAPI/SQLAlchemy.
- Một MCP tool cho mỗi skill; nhồi body skill vào tool result thay vì lazy fetch.
- A2A cho gọi nội bộ / V1 agent runtime.

## Verification

- `tests/unit/test_contracts.py::test_domain_has_no_protocol_imports`.
- `tests/unit/test_architecture_boundaries.py::test_adapter_boundary_domain_never_imports_adapters`.
- V1 exit: client thứ hai dùng được cùng domain qua REST/MCP mà không sửa core (§54.11–13).
- Trước khi code adapter, re-verify spec OpenCode V2 catalog/hook và MCP revision/Skills extension (§77).
