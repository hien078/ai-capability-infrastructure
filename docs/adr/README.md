# ADRs — locked (Accepted 2026-09-27, nguồn: `plan_v2_revised.md`)

- ADR-001 — Capability abstraction (khái niệm chung, ngữ nghĩa theo kind) →
  `001-capability-abstraction-kind-specific-semantics.md` (plan §§0, 2.3, 6–9, 32).
- ADR-002 — Registry là source of truth duy nhất; tách Version / Release / Binding / Metrics →
  `002-one-canonical-registry.md` (plan §§2.4–2.5, 6, 10, 21, 39, 41).
- ADR-003 — Phân tách Data / Control / Observability planes →
  `003-plane-separation.md` (plan §§0, 3–4, 33–37, 63–64).
- ADR-004 — Adapter boundary: domain độc lập client/protocol; edge mỏng →
  `004-adapter-boundary.md` (plan §§2.1–2.2, 2.8, 13, 25, 28–31, 44).
- ADR-005 — OpenCode native catalog + thin router plugin →
  `005-opencode-native-catalog-thin-plugin.md` (plan §28).
- ADR-006 — MCP Skills extension cho transport; ít tool ổn định →
  `006-mcp-skills-extension.md` (plan §29).
- ADR-007 — No A2A trong V1; SDK wrap protocol →
  `007-no-a2a-in-v1.md` (plan §§2.2, 30, 31).
- ADR-008 — Zero-item bundle hợp lệ; composer tối thiểu →
  `008-zero-item-bundle.md` (plan §§2.7, 19, 60.8).
- ADR-009 — Eligibility/security trước ranking →
  `009-eligibility-before-ranking.md` (plan §§15, 17.1, 25).
- ADR-010 — Outcome evidence đa nguồn →
  `010-multi-source-outcome-evidence.md` (plan §§33, 33.1, 60.9).
- ADR-011 — Modular monolith first →
  `011-modular-monolith.md` (plan §2.9).
- ADR-012 — No automatic production evolution; promotion qua gates →
  `012-no-automatic-production-evolution.md` (plan §§24, 37–38, 58).

Toàn bộ 12 ADR đã Accepted. Mọi ADR có Context/Decision/Consequences/Rejected/Verification
và test tương ứng trong `tests/`.
