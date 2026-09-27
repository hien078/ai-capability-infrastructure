# ADR-001 — Capability abstraction: khái niệm chung, ngữ nghĩa theo kind

- Status: Accepted (2026-09-27, khóa từ `plan_v2_revised.md` §§0, 2.3, 6–9, 32, 74; thay thế schema phẳng của `plan.md` gốc).
- Scope: mọi `CapabilityDescriptor`, API, routing, benchmark trong V1.

## Context

Hệ thống cần một abstraction tái sử dụng chung cho client độc lập (OpenCode, Claude Code, Codex, app nội bộ),
nhưng các kind có ngữ nghĩa thực thi khác nhau: skill được resolve/load/apply bởi host, tool được invoke,
resource được read, workflow được instantiate/execute, service được call, agent được delegate.
Một object phẳng duy nhất với hàng chục field nullable, hoặc một động từ `execute()` chung,
sẽ buộc mọi kind phải implement các phương thức không liên quan và làm mờ ranh giới quyền thực thi.

## Decision

- `Capability` chỉ là định danh logic ổn định (`capability_id`, kind, owner_scope).
- Mô tả chung + spec riêng theo kind dưới dạng discriminated union
  (`SkillSpec | ToolSpec | ResourceSpec | WorkflowSpec | ServiceSpec | AgentSpec`, discriminator `kind`).
- Động từ kind-specific bắt buộc: skill→resolve/load/apply, resource→read, tool→invoke,
  workflow→instantiate/execute, service→call, agent→delegate. Cấm API `execute_capability()` chung.
- `evaluator / memory / integration / model` là role của service/tool, không phải top-level kind.
- Phân loại faceted (domain, task_type, technology, concern, …), không ép cây phân cấp duy nhất.

## Consequences

- Thêm kind mới không bắt kind cũ implement method vô nghĩa (Invariant 12).
- Router/composer/benchmark phải xử lý ngữ nghĩa theo kind, không giả định mọi thứ đều "chạy" được.
- `SKILL.md` gợi ý tool nhưng không bao giờ cấp quyền — quyền thuộc về host (xem ADR-004).

## Rejected

- Một schema Capability phẳng với field nullable cho mọi kind.
- Một động từ `execute_capability()` chung cho mọi kind.
- Cây taxonomy cứng duy nhất.

## Verification

- `CapabilitySpec` trong `src/aci/domain/capability/models.py` là union có discriminator `kind` đủ 6 kind.
- `tests/unit/test_architecture_boundaries.py::test_capability_spec_is_discriminated_union_of_six_kinds`.
- `tests/unit/test_architecture_boundaries.py::test_no_generic_execute_verb_in_domain`.
