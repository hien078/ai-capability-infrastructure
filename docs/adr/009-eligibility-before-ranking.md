# ADR-009 — Eligibility/security trước ranking; reranker không cứu nội dung không hợp lệ

- Status: Accepted (2026-09-27, khóa từ `plan_v2_revised.md` §§15, 17.1, 25, 68).
- Scope: eligibility (Phase 5), retrieval/rerank (Phase 6–7).

## Context

Nếu policy chạy sau rerank, một skill bị revoke/license-cấm/trust thấp có thể được
LLM reranker "cứu" lên bundle — vi phạm ranh giới an toàn và prompt-injection.

## Decision

- Thứ tự pipeline cố định: **eligibility/policy → facet/metadata filter → retrieval →
  rerank → dependency resolve → compose** (§14). Reranker chỉ thấy nội dung đã eligible.
- Eligibility loại: revoked, sai channel, license cấm, trust tier, client/protocol
  incompatible, tenant/workspace scope, ngôn ngữ/framework không hỗ trợ, deny rule tường minh.
- Embed/rerank **chỉ** trên trusted normalized summaries (name/description/provides/
  facets/derived summary) — không bao giờ body `SKILL.md` thô (§16.1, §17.1).
- Revocation bypass cache; authz không cache vô hạn (§46).

## Consequences

- Invariant 6: ineligible/revoked không thể được rescue bởi LLM reranker.
- Test §61: adversarial reranker metadata, revoked-but-cached skill phải fail đúng.

## Rejected

- Reranker như lớp sửa lỗi an ninh; embed body thô; eligibility sau retrieval.

## Verification

- Phase 5 test: ineligible không bao giờ tới reranker; revoked biến mất khỏi route
  ngay khi có hiệu lực.
