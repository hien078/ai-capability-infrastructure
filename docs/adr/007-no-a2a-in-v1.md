# ADR-007 — No A2A trong V1; SDK wrap protocol

- Status: Accepted (2026-09-27, khóa từ `plan_v2_revised.md` §§2.2, 30, 31, 68).
- Scope: ranh giới protocol (mọi phase).

## Context

A2A dễ bị lạm dụng cho gọi hàm nội bộ giữa các module "agent" cùng process,
làm nhiễm kiến trúc nội bộ bằng ngữ nghĩa task/message/artifact ngoài hệ thống.

## Decision

- **V1 không có A2A runtime.** A2A chỉ dành cho delegation qua biên agent tự trị
  có địa chỉ riêng (V3+, khi Agent Card/Task/Message/Artifact thật sự cần).
- Không dùng A2A cho gọi nội bộ cùng process.
- SDK (Python/TypeScript) là **client library wrap REST/MCP/A2A**, không phải protocol
  ngang hàng — không cho SDK tạo lifecycle song song với registry.
- Phân biệt `ProceduralSkill` với skill-declaration của agent ngoài (§30.2).

## Consequences

- Thêm protocol mới không đổi schema registry (Invariant 13).
- V3 sẽ map `Capability(kind=agent) → CapabilityBinding(type=a2a) → Agent Card`.

## Rejected

- A2A giữa Planner/Coder nội bộ; SDK là protocol peer.

## Verification

- `grep -r a2a src/aci/domain` rỗng; không có module a2a nào trong V1.
