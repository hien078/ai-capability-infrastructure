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

## Amendment 2026-09-30 — run-path hardening (sau đánh giá độc lập)

Đánh giá trên đường chạy tích hợp (không phải từng manager) cho thấy 8 invariant
bị vi phạm: verifier chỉ đọc self-report của model, không có authority preflight,
ledger không đếm turn/tool call, tool id lạ làm crash run, history nằm ngoài
StateManager, RecoveryManager quyết định rồi bị bỏ qua, CAS chỉ danh nghĩa. Các
quyết định bổ sung (mỗi mục có test trong `tests/security/test_harness_invariants.py`
hoặc test đơn vị tương ứng):

7. **Authority preflight là bước bắt buộc của ToolRuntime (§58).** `ToolSpec.
   authority_requirements` có kiểu `ToolAuthority` (tham số nào là đường dẫn đọc/ghi,
   lệnh, host); `derive_requirement()` sinh `AuthorityRequirement` cho từng call và
   `PolicyEvaluator` so với grant của envelope. Tool có side effect mà không khai báo →
   DENY (fail closed). Mọi lỗi (tool lạ, envelope hết hạn, từ chối) là observation cho
   model, không bao giờ là exception thoát khỏi `run()`.
8. **Bằng chứng do harness quan sát là nguồn duy nhất của verification (INV-08).**
   Tool path ghi `changed_resources` + `observed_evidence` (đọc file, ghi file, exit
   code lệnh) vào StateManager; verifier của cả 9 profile chỉ pass khi claim/artifact/
   change của model khớp bằng chứng đó (`claims_grounded`, `artifacts_observed`,
   `claimed_changes_observed`, `command_passed_after_last_change`). Client có thể
   đưa `verification_command`; verifier tự chạy lệnh đó trong workspace và exit code
   quyết định — model không bao giờ tự tuyên bố "tests pass".
9. **Transcript là state (INV-01/13).** `RuntimeStateSnapshot.transcript` giữ hội thoại;
   kernel lắp lại mọi request từ state trong ngân sách context (`select_transcript`
   giữ nguyên nhóm assistant-tool-call + kết quả, cắt nhóm cũ nhất; tóm tắt tiến độ do
   harness quan sát được ghim ở system). Checkpoint vì thế chứa đủ để resume.
10. **Recovery được áp dụng thật (§18).** MODEL_UNAVAILABLE/RATE_LIMITED → retry có
    backoff; MALFORMED → repair turn; verification FAIL → RECOVERING → model nhận
    repair hints và thử lại (tối đa theo `max_same_failure_retries`); lỗi lập trình/
    cấu hình → FATAL, không loop. Mọi exception bất ngờ kết thúc run ở FAILED/
    FATAL_ERROR, chỉ lộ tên loại lỗi.
11. **CAS thật:** mọi mutator của StateManager tăng version; commit của một tool batch
    dùng version chụp TRƯỚC khi chạy tool — writer xen vào → `StateCommitConflict`,
    không bao giờ ghi đè im lặng.
12. **REST có workspace thật nhưng server giữ trần authority (INV-02).** Client chỉ
    được gọi tên workspace dưới `ACI_AGENT_WORKSPACE_ROOT`; mỗi run làm việc trên bản
    copy riêng dưới `ACI_AGENT_RUNS_ROOT`; `ACI_AGENT_PROCESS_PREFIXES` là trần lệnh
    cho cả model lẫn verifier (rỗng = không có `run_command`). Process con nhận env
    tối thiểu (không bao giờ thấy API key của server). **Không có sandbox**: bật
    process prefixes nghĩa là code trong workspace chạy dưới user của server —
    SandboxWorkspace vẫn là non-goal v2 (mục 6).

## Verification

- `tests/security/test_harness_invariants.py` — INV-04/06/07/08 + §7.6 trên đường
  chạy tích hợp (mỗi test từng là strict xfail cho tới khi bản sửa hạ cánh).
- `tests/unit/test_run_controller.py` — deterministic simulation: tool → verify →
  success; verification failure → repair turn → FAILED sau khi hết recovery; turn
  limit; cancellation; transcript/context budget; recovery matrix; CAS conflict.
- `tests/unit/test_state_manager.py` — CAS conflict, illegal transition, terminal immutable.
- `tests/unit/test_delegation.py` — child grant intersection, budget carve, depth limit.
- `tests/unit/test_capability_runtime.py` — digest mismatch không bao giờ load.
- Boundary tests (`test_architecture_boundaries.py`) xanh với package mới.
