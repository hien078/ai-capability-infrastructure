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
    SandboxWorkspace vẫn là non-goal v2 (mục 6). *(Đã thay thế bởi mục 13.)*

## Amendment 2026-10-01 — sandbox, approval/resume, registry skills (quyết định của user)

13. **Sandbox là BẮT BUỘC (thay thế "Docker sandbox backend" trong mục 6 và câu cuối
    mục 12).** Mọi process của agent-run (`run_command` của model, `verification_command`
    của client, phép đo post-hoc của H-bench) chạy trong `BwrapSandbox`
    (`src/aci/runtime/sandbox.py`): `/usr` + interpreter prefixes read-only, workspace
    read-write tại `/workspace` (đường dẫn trung tính), `--unshare-all` (không mạng),
    `--cap-drop ALL`, `--clearenv` + env tối thiểu, `/proc` `/dev` mới, tmpfs `/tmp`,
    giới hạn `prlimit` (`ACI_AGENT_SANDBOX_*`). `$HOME`, repo, `data/` và socket DB
    không bao giờ được bind. **Fail closed:** bwrap không dùng được → lệnh bị từ chối
    (PERMISSION_DENIED; 403 ngay lúc POST nếu có `verification_command`).
    `ACI_AGENT_SANDBOX=none` là opt-out tường minh, có cảnh báo lớn. Còn lại: đường dẫn
    interpreter nhìn thấy được (read-only).
14. **Approval + resume.** REQUIRE_APPROVAL làm run DỪNG (`interrupted_approval`) TRƯỚC
    khi call bị gate chạy; checkpoint dừng (snapshot đủ + phần batch chưa chạy, gắn
    `apr_…` + sha256 của tool/args) được lưu bền (`agent_run_checkpoints`, migration
    0019). `POST /v1/agent-runs/{id}/resume` resume CHÍNH run đó tối đa một lần:
    StateManager khôi phục cùng version (INV-01), budget còn lại (không reset), grants =
    checkpoint ∩ trần hiện tại (chỉ thu hẹp, INV-02), approve = đúng các call đang chờ
    một lần (authority vẫn đánh giá). Nguồn approval: server floor
    `ACI_AGENT_APPROVAL_REQUIRED_TOOLS` ∪ `approval_required_tools` của request (chỉ thêm).
    Clarification cũng checkpoint và resume bằng `{answer}`.
15. **Registry skills trên đường REST.** CapabilityRuntime dùng cùng
    `RouteCapabilitiesService.route()` như `/v1/routes` (eligibility trước); nội dung
    SKILL.md đã verify digest được đưa vào context trong khối "third-party reference"
    có giới hạn kích thước; không gì trên đường authority đọc nó.

## Amendment 2026-10-01 — kết luận registry-skills trên kernel (H-bench S/P + hai trục mới)

Bốn vòng; mọi số lấy trực tiếp từ JSON trong `data/hbench/`, không từ trí nhớ. Vệ bằng
chứng: run có `stop_reason=MODEL_FAILURE` (gateway 5xx — lỗi hạ tầng của gateway dùng
chung, không phải lỗi task) là INVALID với mục đích đo lường, bị loại khỏi mọi tỉ lệ và
được đếm rõ từng vòng.

16. **Đã đo gì (arm, n, model, fixture) — kết quả:**
    - **Arm S — skill được CUNG CẤP** (tool tổng hợp `request_capability`, model tự quyết
      gọi): `hbench-20261001-134549.json` — glm-5.3, 8 fixture §80 (multi/long) × 3 lượt,
      K/N/S n=24/arm, bwrap, max_turns 12, turn-budget note; 72/72 hợp lệ.
    - **Arm P — PRELOAD đầu run** (router §14 chạy trên task objective trước turn 1,
      `preload_capabilities=True`): `hbench-20261001-161454.json` — glm-5.3, cùng 8
      fixture, K/P n=24/arm; 48/48 hợp lệ.
    - **Trục model yếu:** `hbench-axes-flash-KP.json` — glm-5.3-flash, cùng 8 fixture,
      K/P n=24/arm; 48/48 hợp lệ.
    - **Trục domain-knowledge:** `hbench-axes-domain-KP-rerun.json` (vòng của record) —
      glm-5.3, 4 fixture mới (`scripts/domain_tasks.py`; verify cơ khí fail-khi-ship /
      pass-sau-fix-gốc; prompt không nêu tên skill — router phải tự tìm), K/P n=12/arm
      danh nghĩa. Lần chạy đầu `hbench-axes-domain-KP.json` INVALID toàn vòng (12/24
      MODEL_FAILURE, burst 5xx đối xứng hai arm — giữ lại làm artifact hạ tầng); top-up
      `hbench-axes-frontend-topup.json` 5/6 MODEL_FAILURE, chỉ 1 run hợp lệ.

    | vòng | model / fixture | K (hợp lệ) | P hoặc S (hợp lệ) | token_in P/K |
    |---|---|---|---|---|
    | S (offered) | glm-5.3, §80 ×8 | acc 19/24 (0.79) · tests 24/24 | S: acc 17/24 (0.71) · tests 21/24 · **0 request** | 1.12× |
    | P (preload) | glm-5.3, §80 ×8 | acc 17/24 (0.71) · tests 20/24 | acc 12/24 (0.50) · tests 21/24 | 3.05× |
    | P (preload) | flash, §80 ×8 | acc 17/24 (0.71) · tests 22/24 | acc 20/24 (0.83) · tests 22/24 | 2.71× |
    | P (preload) | glm-5.3, domain ×4 | acc 6/12 (0.50) · tests 9/12 | acc 7/10 (0.70) · tests 9/10 — 2/12 P INVALID đã loại | ~1.6× |

    (acc = acceptance verifier-gated; tests = `tests_pass_at_end` post-hoc. Trên cả 222
    run của 6 file JSON: false_success 0.00 và capability_request 0 — model không bao giờ
    tự xin skill, ở bất kỳ tier/fixture nào. Vòng domain theo mẫu số 12 của REPORT-axes.md
    — gồm cả 2 run INVALID — là acc 0.58 / tests 0.75; mọi cách tính mẫu số đều cho kết
    luận không significant.)

17. **Quyết định (chỉ từ số đo):**
    - **`ACI_AGENT_CAPABILITY_PRELOAD` giữ OFF** (mặc định hiện tại, không đổi). Trên
      glm-5.3 preload KHÔNG cho giá trị kết quả: acc giảm directional 0.71 → 0.50 (z≈1.48,
      không significant), tests không đổi (20/24 vs 21/24, z≈−0.41), chi phí 3.05× token
      (23.6k → 71.8k/run) + 1.61× wall. 9/12 run LIMIT_TURNS của P có fix đã land nhưng
      không propose (K: 3/7) — preload làm model kém PROPOSE hơn, không kém FIX hơn. Vòng
      acc thắng duy nhất của P (long-order-pipeline) là fixture preload RỖNG (rank-1
      `claude-api` ~21.5k token vượt budget 8k → bundle 0 item); vòng thua tệ nhất
      (long-notify-fanout 0/3 vs 3/3) lại là fixture preload domain-relevant nhất.
    - **Nơi skill CÓ tác dụng — process, trên model yếu, directional:** flash acc P 0.83
      vs K 0.71 (+0.12; z≈1.0 tính từ số đếm JSON, REPORT-axes.md ghi ≈0.86 — cùng kết luận
      không significant), tests_pass BẰNG NHAU 22/24 — fix land như nhau; cái chuyển là
      completion discipline (LIMIT_TURNS 7→4, verification_fails 7→4), cơ chế khả dĩ nhất
      là `verification-before-completion` (preload trong 21/24 run P — mọi bundle không
      rỗng). Đúng chẩn đoán §80: giá trị nằm ở process quality, không phải binary
      acceptance; giá 2.71× token.
    - **Trục domain-knowledge: KHÔNG có giá trị knowledge — và vòng nói được TẠI SAO.**
      Intended skill được preload rank-1 trong 12/12 run P (cơ khí routing/preload hoàn
      hảo) nhưng không mang gì model còn thiếu: brand-palette — 3/3 run K NAKED có tests
      xanh (digest sha256 trong test xác nhận đúng palette chính thức) mà không có skill
      trong context — tri thức công khai đã nằm trong training data, cổng knowledge không
      giữ được; delta acc duy nhất của fixture (P 3/3 vs K 0/3) lại là completion
      discipline (K viết đúng giá trị rồi nhưng không propose trước limit). Injection + mcp:
      fix suy ra được từ test (K 3/3 cả hai; P theo workflow của skill còn tốn turn —
      injection P 1/3). Frontend-tells: vượt budget 12 turn ở cả hai arm — TENTATIVE, chỉ
      ~5 run hợp lệ qua các vòng (3 K + 2 P, tất cả tests đỏ tại limit; top-up 5/6 INVALID).
    - **`request_capability` giữ nguyên offered** (không đổi code): 0/222 request — model
      ở tier này không tự xin skill; nhưng cơ chế đã build + pin bằng test, chi phí chỉ là
      protocol text (~+2.8k token/run khi offered), và offered không gây hại đo được
      (S 17/24 vs K 19/24, z≈0.67 — noise).
    - **Corpus chờ human gate:** phát hiện corpus (near-dup CONFLICTS_WITH + `claude-api`
      split) nằm trong `data/corpus-cleanup/PLAN.md`, CHƯA áp dụng — chờ `capctl` của
      human (ADR-012/013). Nếu preload từng được bật lại, các conflict đó sửa cơ khí việc
      co-preload `tdd` + `test-driven-development` đã đo trong 7/8 fixture.
    - **Điều kiện xét lại (bằng chứng mới sẽ đổi quyết định):** (a) model yếu THẬT —
      open-weights nhỏ, không phải flash 2026-era (trục H008 / điều kiện unfreeze
      crawl.md); (b) tri thức KHÔNG công khai — convention nội bộ, runbook riêng
      (brand-palette chứng minh tri thức công khai không bị gate bởi thiết kế fixture
      nào); (c) corpus đã qua human gate (PLAN.md) và router rank đúng hơn trên objective
      thật; (d) fixture mới được verify reach-in-budget bằng naked model TRƯỚC khi chạy
      arm (bài học frontend-tells: verifier chứng minh reachability, không phải
      reachability-in-budget).
    - **§34 caveat (áp dụng toàn bộ):** n nhỏ (3 lượt/case), một họ model (glm-5.3 /
      glm-5.3-flash), fixture do tác giả dựng, vòng domain bị degrade một phần bởi burst
      5xx (12/24 + 2/24 + 5/6 run INVALID — đã loại và đếm), mọi chênh lệch acc KHÔNG
      significant ở các n này — directional only, không suy nhân quả. K tự drift 19→17/24
      giữa hai vòng cùng cài đặt (noise floor) — bảng per-fixture của một vòng không mang
      tính dự đoán.

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
