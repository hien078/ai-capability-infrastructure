# ADR-014 — HarnessKernel: service-side agent runtime (harness.md)

- Status: Accepted (2026-09-29, từ `docs/plans/harness.md` — user-provided master plan).
- Scope: `src/aci/domain/runtime/` (contracts) + `src/aci/runtime/` (kernel managers).
- Refines: ADR-011 (modular monolith — kernel là module mới trong monolith, không phải service riêng).

## Context

V2/V3/V4 hiện có: capability plane (routing pipeline) hoàn chỉnh, A2A gateway +
`OpenAICompatExecutor` cho delegated task đơn lượt. `docs/plans/harness.md` (8634 dòng,
user-authored) yêu cầu một **HarnessKernel** service-side: thực thi MỘT objective được
delegate với lifecycle tường minh, budget chặn được, authority tách khỏi enforcement,
verification chặn false-completion, checkpoint/resume. Đây là phần "HOW" —
capability plane giữ phần "WHAT" (§0.1).

## Decision

1. **Hai package mới, tôn trọng layer hiện có:**
   - `src/aci/domain/runtime/` — contracts thuần Pydantic frozen (RunStatus/StopReason,
     FailureEnvelope, GrantEnvelope, ToolSpec, EvidenceBundle/Pack, RuntimeSpec,
     ModelAction union, SubtaskContract, RunResult). Không import gì upward —
     boundary tests rglob tự động cover.
   - `src/aci/runtime/` — engine managers (RunController, StateManager, ContextEngine,
     ToolRuntime, AuthorityManager, GuardrailManager, WorkspaceManager,
     CheckpointCoordinator, RecoveryManager, VerificationManager, DelegationManager,
     CapabilityRuntime, PlanningStrategy). SYNC, không FastAPI/SQLAlchemy —
     unit-test được với fakes.
2. **15 invariants (§4) là review-blocking**, trọng tâm: INV-01 StateManager là
   authority duy nhất (CAS commit theo version); INV-08 completion chỉ qua
   VerificationManager, model claim không bao giờ tự thành công; INV-02/03 child
   authority ⊆ parent, budget carve từ reserve; INV-06 không side-effect nào
   không qua validate → guardrail → authority → envelope.
3. **Delegation OFF mặc định** (§0.3) — client orchestrator sở hữu global Task DAG;
   DelegationRequest trong run fail-closed AUTHORITY_DENIED.
4. **ModelAction union chuẩn hóa** (§47) — model output là untrusted input, normalize
   thành FinalCandidate | ToolCallBatch | CapabilityRequest | …, không bao giờ parse
   natural-language control flow.
5. **Deterministic-first**: mọi manager test được không LLM (FakeModelGateway,
   ScriptedModel); OpenAICompatGateway là adapter thật duy nhất, inject transport.
6. **Không build trong v2** (§2 non-goals): distributed workflow engine, multi-agent
   swarm, model gateway platform, Docker sandbox backend (SandboxWorkspace là stub),
   learned context router, event sourcing đầy đủ.

## Consequences

- Kernel 13 managers + contracts ~20 file, mọi manager thay thế được không viết lại
  phần còn lại (§70: model/workspace/tool/capability router đều swap được).
- REST `/v1/agent-runs` (H11) sẽ mount vào `aci.main` qua `create_app(container=None)`
   như mọi adapter khác — không thay đổi domain.
- Checkpoint schema version 1; migration checkpoint là việc của phase sau khi
  schema thật thay đổi (§54.3).
- Benchmark arena (H-bench) là gate cho mọi harness mechanism change (§Benchmark freeze).

## Verification

- `tests/unit/test_run_controller.py` — deterministic simulation: tool → verify →
  success; verification failure chặn success; turn limit; cancellation.
- `tests/unit/test_state_manager.py` — CAS conflict, illegal transition, terminal immutable.
- `tests/unit/test_delegation.py` — child grant intersection, budget carve, depth limit.
- `tests/unit/test_capability_runtime.py` — digest mismatch không bao giờ load.
- Boundary tests (`test_architecture_boundaries.py`) xanh với package mới.
