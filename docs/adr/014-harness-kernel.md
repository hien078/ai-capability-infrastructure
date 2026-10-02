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

## Amendment 2026-10-02 — ba thí nghiệm quyết định (E1/E2/E3, phase 2)

Mọi số dưới đây lấy trực tiếp từ JSON vòng của record (`data/hbench/
hbench-20261001-224132.json` cho E1, `data/hbench/hbench-20261002-003148.json` cho E2)
và được reviewer độc lập (job `phase2-conclude`) **tính lại từ hàng run thô** — không
từ trí nhớ, không từ aggregate của runner (aggregate của runner không loại run
INVALID; cả hai vòng đều 0 run INVALID nên hai số trùng nhau). Run `MODEL_FAILURE`
(gateway 5xx) là INVALID với mục đích đo lường: **0/272 (E1), 0/40 (E2)** — không
run nào bị loại, không cần top-up. Kế hoạch tham chiếu: `docs/plans/
aci-improvement-2026-10.md` §2 (E1/E2/E3 + bảng rẽ nhánh).

18. **E1 — kernel (HOW) so với vòng lặp ngây thơ (N), ở cỡ mẫu đủ power: ✗ KHÔNG
    chứng minh được ở tier glm-5.3.** Vòng đủ power đúng thiết kế kế hoạch (phát hiện
    0.75 → 0.88, hai phía α=0.05, power 0.8 → ~136 run/arm): 8 fixture §80 đã verify ×
    17 lượt = **n=136/arm, 272 run**, glm-5.3, sandbox bwrap, turn-budget note,
    max_turns 12, `tests_pass_at_end` làm yardstick chung (gated trong K, post-hoc
    trong N — cùng một câu hỏi):

    | so sánh (K vs N) | K | N | Δ | z | p (hai phía) | CI 95% (Δ) | kết luận |
    |---|---|---|---|---|---|---|---|
    | tests_pass_at_end | 127/136 = 0.934 | 121/136 = 0.890 | +0.044 | +1.283 | 0.1996 | [−0.023, +0.111] | không significant |
    | tỉ lệ chạm limit (LIMIT_TURNS/MAX_TURNS) | 51/136 = 0.375 | 59/136 = 0.434 | −0.059 | −0.988 | 0.3230 | [−0.175, +0.058] | không significant |

    McNemar exact (ghép theo fixture×lượt): tests_pass 13 vs 7 discordant, p=0.263;
    turn-limit 17 vs 25, p=0.280 — đều không significant. **Điểm trên của CI
    (+0.111) LOẠI TRỪ hiệu ứng +0.13 mà kế hoạch đặt ra** — ở tier glm-5.3, dữ liệu
    không nhất quán với hiệu ứng 0.75 → 0.88 ở mức 95%; cạnh "+0.13" n=24 của round 3
    ở tier này là noise như §34 nghi ngờ. Phát hiện hiệu ứng +0.044 quan sát được ở
    power 0.8 cần **~650 run/arm** (~5× vòng này). Per-fixture vẫn cùng chiều (K > N
    ở 3 fixture, hòa 5, thua 0) nhưng aggregate mới là câu trả lời của E1. Những gì
    kernel VẪN đo được là cấu trúc, không phải delta kết quả: false_success 0.00/272
    run (verifier bác 51 proposal non-sát-điểm trong K, 0 false success), at-limit
    evidence tách 42 run fix-đã-land-nhưng-không-claim khỏi 9 run không land gì —
    chi phí +4.2% token input, +13% output, +6.6% wall, số turn ngang nhau.
    Báo cáo: `data/hbench/REPORT-E1.md`.

19. **E2 — skill mang tri thức PHI CÔNG KHÔNG có giá trị đo được: ✓ — tín hiệu dương
    ĐẦU TIÊN của skill trong lịch sử dự án** (sau 222 run mà skill tri thức công khai
    không bao giờ nhích tests_pass ở bất kỳ tier nào — mục 16/17). Fixture mới
    `--set private` (`scripts/private_tasks.py`): 2 chuẩn nội bộ HƯ CẤC (Atlas
    error-handling, Meridian release gate), tri thức TỒN TẠI DUY NHẤT trong SKILL.md
    riêng của fixture (không bao giờ là file workspace) và được pin trong test bằng
    sha256 digest — model naked không thể suy ra từ tên test, từ code ship (chứa
    chính sách SAI với từ vựng cố ý sai) hay từ prompt. Tính khả thi fixture được
    kiểm TRƯỚC khi đo (reach-in-budget WITH skill ≥ 2/3: atlas 3/3 sau một vòng đơn
    giản hóa, meridian 2/3). Đo: **K (naked) vs R (cùng kernel + skill riêng của case
    preload từ handler local in-process — không registry, không DB, không router)**,
    n=10/case/arm, glm-5.3, bwrap, verification gated ở cả hai arm:

    | | K (n=20) | R (n=20) | Fisher p (pool) |
    |---|---|---|---|
    | tests_pass_at_end | **0/20 (0.00)** | **17/20 (0.85)** | **2.57e-08** |
    | acceptance (verifier-gated) | 0/20 | 8/20 (0.40) | 0.00328 |
    | LIMIT_TURNS | 20/20 | 12/20 | — |
    | false_success | 0 | 0 | — |

    Từng case đều significant: atlas 0/10 vs 8/10 (p=0.000714), meridian 0/10 vs 9/10
    (p=0.000119) — điều kiện dừng của kế hoạch KHÔNG kích hoạt (nó kích hoạt khi hai
    use case hợp lệ liên tiếp KHÔNG tách được tín hiệu). **Cổng knowledge giữ tuyệt
    đối:** 0/20 run K sửa file nguồn (chúng dành turn tìm cách TRÍCH XUẤT chuẩn —
    `crack.py` brute-force sha256, đi filesystem tìm skill, dump test source — tất
    cả thất bại); 17/17 thành công của R sửa nguồn bằng từ vựng CHỈ có trong skill
    (xác minh 0 marker trong source ship); 0/40 workspace giả test file. Chi phí:
    +23% token input, +140% output. **Giới hạn phạm vi (quan trọng):** giá trị chỉ
    chứng minh cho PRELOAD tri thức riêng — routing/acquisition KHÔNG được đo
    (handler của R được ghim vào case; 0/40 capability request — amendment 16/17
    giữ nguyên trên chính bài test khó nhất của nó: model biết mình thiếu chuẩn,
    đào tìm, và vẫn không xin); acceptance metric nhiễu (atlas p=0.21) —
    `tests_pass_at_end` mới là yardstick mang tính kết luận. Báo cáo:
    `data/hbench/REPORT-E2.md`.

20. **E3 — dùng thật (30 ngày): chỉ ghi baseline ngày 0 — hữu cơ = 0.** Instrument
    `scripts/usage_report.py` (read-only, `default_transaction_read_only=on`, fail
    closed; không migration, không DB write). Tuần 2026-W40 trên `aci_bench`: 288
    route_run = 65 phân loại "organic" + 223 measurement + 0 unknown; agent_runs 5
    (succeeded 5); 46 outcome event trên 44/286 bundle. **Cả 65 run "organic" đều có
    nguồn đo-lường/script/test đã biết** (46 opencode proof-loop §80 28/9; 13
    rest-client test/probe; 3 mcp-client path-check Antigravity/goose; 1 console
    probe; 2 harness-kernel validation run) — hữu cơ thật = 0 ở ngày 0. Giới hạn đo
    (in trong mỗi báo cáo): proof loop/probe đi qua plugin THẬT nên telemetry không
    tách được khỏi organic thật; harness-kernel tách bằng linkage agent_run (một run
    thật mà dòng agent_runs fail-to-persist bị tính nhầm thành MEASUREMENT). Đồng hồ
    30 ngày chạy từ đây; re-run: `scripts/usage_report.py [--json]`.

21. **Nhánh đã đi (bảng §2 của kế hoạch): `E1 ✗ E2 ✓` → "giữ Control Plane + kho
    tri thức nội bộ; đóng băng kernel; ACI về đúng plan_v2 V1–V2".** Không thay đổi
    bất kỳ default/product setting nào: `ACI_AGENT_CAPABILITY_PRELOAD` giữ OFF,
    `request_capability` giữ offered, không dòng kernel nào đổi. E3 còn đồng hồ 30
    ngày và là override còn treo: nếu hữu cơ ≈ 0 đến ngày 30 → theo kế hoạch, không
    xây thêm gì hướng người dùng, chỉ giữ phần nghiên cứu. **§34 caveat (áp dụng
    toàn bộ):** một họ model (glm-5.3), fixture do tác giả dựng (E1: 8 fixture §80 đã
    verify; E2: 2 chuẩn HƯ CẤC — đúng là phi công khai vì mới được hư cấu 2026-10-01,
    nhưng đơn giản hơn runbook thật), n=17/fixture (E1) / n=10/case/arm (E2), một
    gateway; E1 trả lời cho tier glm-5.3 trên 8 fixture này (trục flash +0.13/+0.08
    n=24 chưa đo ở power); E2 trả lời cho preload tri thức riêng trên 2 use case —
    không chuyển sang model yếu hơn, runbook dài hơn, hay corpus multi-skill riêng
    mà không có vòng mới. Kết quả âm tính của E1 được ghi lại như bằng chứng (giữ
    nguyên, không xóa); tín hiệu dương của E2 được ghi với giới hạn phạm vi của nó.

## Amendment 2026-10-02 (b) — E1b, E2B và tính hợp lệ của phép đo trên macOS

Mọi số dưới đây được lead **tính lại từ hàng run trong JSON** (không từ aggregate của
runner); JSON + báo cáo worker: `data/aci-improvement/mac/` (máy Mac worker; bản sao trên
Linux). Model glm-5.3 / glm-5.3-flash qua gateway local của Mac; run `MODEL_FAILURE` là
INVALID, bị loại và chạy bù.

22. **Môi trường đo trên macOS (Seatbelt) — mọi vòng H-bench trên Mac TRƯỚC các bản sửa
    là INVALID.** Ba lỗi chỉ-macOS làm model gần như không làm được việc: (a) PATH của
    Seatbelt đặt `/usr/bin` trước interpreter → `python3` là stub Xcode không có pytest;
    (b) `ulimit -u` trên macOS đếm MỌI tiến trình của user → mọi fork (`sh -c`, pipe,
    git) fail; (c) workspace nằm trong repo và macOS không có mount namespace → pytest
    lấy `pyproject.toml` của repo làm config cha và crash. Triệu chứng: N verified×flash
    1/8 trên Mac so với 0.83 lưu trữ Linux; sau sửa (`fe9ca8a`, `7af721c`) **7/8**. Lỗi
    thứ tư do E2B tự phát hiện: work root dùng chung (`/tmp/aci-hbench`) đọc được → 3/20
    run K tìm thấy doc của arm F trong workspace anh em (các pass duy nhất của K) — sửa
    `68bb135` (Seatbelt ẩn work root, mỗi run chỉ đọc workspace của nó). Seatbelt cũng
    ẩn nội dung `/Users`, `/Volumes`, temp per-user, home của root và của server
    (`384988b`). Bài học: một sandbox không có mount namespace phải được kiểm chứng bằng
    vòng anchor so với lưu trữ Linux TRƯỚC khi đo.

23. **E1b — tìm fixture có "khoảng trống" cho vòng lặp ngây thơ: ✗ KHÔNG TÌM ĐƯỢC
    (dừng sau phase A theo luật đăng ký trước).** Phase A chỉ chạy arm N (không nhìn K),
    `--repeat 3`, 4 tổ hợp, 78 run, 0 INVALID, 0 false success: verified(12 turn)×flash
    **24/24**, verified×glm-5.3 **22/24**, horizon(16 turn, `--set horizon` — 5 fixture
    dài, prompt chỉ triệu chứng, 2 fixture có 2 bug)×flash **15/15**, horizon×glm-5.3
    **12/15 (0.80)**. Cửa sổ chọn đăng ký trước là [0.25, 0.75]; không tổ hợp nào lọt →
    phase B (K vs N đủ power) KHÔNG chạy; 0.80 "gần đủ" bị từ chối đúng luật. Đọc: trần
    của E1 (mục 18) KHÔNG phải artifact của 8 fixture — ở tier này vòng lặp ngây thơ giải
    80–100% mọi pool fixture repo đang có. Đóng băng kernel (mục 21) giữ nguyên. Ứng viên
    đầu cho một E1b′ tương lai: horizon×glm-5.3 với n lớn hơn, đăng ký lại trước.

24. **E2B — registry + router so với "tài liệu nằm sẵn trong repo": ✓ registry+router
    GIỮ trần và HƠN doc-trong-repo cho tri thức phi công khai.** `--set private`, n=10/
    case/arm, glm-5.3, Seatbelt; registry = bản sao `aci_e2b` (37 skill production thật +
    2 skill riêng ingest qua đường thật, gate thật — license MIT first-party, promote CHỈ
    trong bản sao dùng-một-lần). tests_pass_at_end: **R (pin + preload) 17/20; F (chuẩn là
    file `docs/standards/<skill>.md` trong workspace, không gợi ý) 9/20; Bp (registry +
    router §14 + preload) 19/20 — router chọn đúng skill riêng ở hạng 1 trong 20/20 run,
    narrowing giữ đúng 1 skill; Bq (preload OFF, chỉ `request_capability`) 9/20; K sạch
    0/20** (vòng K+R sạch, không có doc F trên đĩa; R anchor 17/20 tái lập đúng vòng
    chính). Fisher hai phía: F vs R 0.019; Bp vs F 0.0013; Bq vs K 0.0012; F vs K 0.0012;
    Bp vs R 0.61. Cơ chế đo được: F tìm thấy doc ~52% run (tìm thấy → pass 82%, không
    → 0%); Bq: model **gọi `request_capability` 12/20 (lần đầu ≠ 0 trong lịch sử dự án —
    0/222 ở mục 16/17)**, gọi → pass 9/12, không gọi → 0/8. Tamper: 149 workspace, test
    file byte-identical, không run nào giả pass. **Giới hạn (§34):** 2 chuẩn hư cấu, một
    họ model, corpus 39 skill, skill riêng có tên đặc trưng (routing dễ); Bq hỏi có thể
    vì prompt nói rõ "internal standard". **Không đổi default nào** (`ACI_AGENT_CAPABILITY_
    PRELOAD` vẫn OFF) — nhưng đây là bằng chứng đầu tiên cho thấy với kho tri thức riêng,
    preload ON (Bp 19/20) hơn mặc định hiện tại (Bq 9/20); đổi default cần quyết định của
    người dùng + một vòng đăng ký trước trên use case thật.

25. **Router N+1 (không phải kernel, ghi ở đây vì E2B lộ ra).** `DefaultDependencyResolver`
    kiểm conflict theo cặp → 930/943 câu SQL mỗi `/v1/routes`. Memo theo từng resolve
    (`ca01d10`): 90 case DEV+KERNEL+HELDOUT → bundle GIỐNG HỆT (id, version, role), câu SQL
    84,859 → 3,859, wall 116 s → 10 s; route từ host cách DB 3 ms: 16 s → ~1 s (trước đó
    plugin OpenCode timeout 2 s và fail-open thầm lặng mọi prompt).

26. **E2C — nhân rộng E2B trên 7 fixture tri thức riêng MỚI (F vs Bp): ✓ lặp lại, trong
    phạm vi bài model giải được.** 7 chuẩn hư cấu do 4 builder song song viết (tiền tệ &
    kỳ tài chính, bucket rollout feature flag, che PII trong log, header deprecation API,
    ID có ký tự kiểm tra, retry/DLQ của queue, thứ tự ưu tiên cấu hình); 3 prompt nêu
    tên chuẩn, 4 prompt gián tiếp. Luật hợp lệ đăng ký trước: K n=2 phải 0/2 (giữ: 0/13
    run hợp lệ — không bài nào lộ chuẩn); R n=3 phải ≥2/3 → **chỉ 3/7 bài đạt**
    (drawbridge 3/3, palisade 3/3, vellum 2/3; 4 bài bị loại: ở 7/9 run R thất bại model
    không sửa nguồn trong 12 turn dù chuẩn đã ở ngữ cảnh — bài quá khó, không phải lỗi
    giao tri thức). Vòng chính n=8/case/arm trên 3 bài: **F 8/24 (0.33) vs Bp 17/24
    (0.71), Fisher p=0.0199 — primary đăng ký trước: LẶP LẠI E2B.** Từng bài: drawbridge
    (gián tiếp) F 0/8 vs Bp 4/8; palisade (nêu tên) 2/8 vs 6/8; vellum (nêu tên) 6/8 vs
    7/8. Router: hạng 1 ở 7/7 prompt ex-ante (cả 4 gián tiếp) và 24/24 run Bp, không nạp
    skill nhiễu nào. Cơ chế giống E2B: F tìm thấy doc 9/24 (gián tiếp 0/8) → khi tìm thấy
    pass 8/9, không thấy 0/15. **Đọc:** nút thắt của doc-trong-repo là việc model tự tìm
    doc, và nó sụp khi ticket không nêu tên chuẩn; router không phụ thuộc cách viết prompt
    trên corpus 44 skill này. §34: 3 bài hợp lệ, một họ model, gateway 5xx (18 run
    MODEL_FAILURE loại + chạy bù). Không đổi default nào.

27. **Lỗi kernel sửa trong đợt OpenCode tự chọn việc (bug/security — không mở rộng).**
    `StateManager.commit/_mutate` không nguyên tử (INV-01): event lỗi giữa danh sách để
    lại record nửa vời, commit lại thì áp dụng hai lần → nay áp vào bản nháp rồi mới thay
    (`7e9a256`, 3 test đỏ-trước). Seatbelt ghi file profile theo symlink do model cài
    trong workspace (server ghi đè file ngoài sandbox) → `O_NOFOLLOW`, từ chối
    (`1ce060d`). **Còn treo, cần một vòng H-bench trước khi sửa** (đổi bằng chứng verifier
    nhìn thấy): guardrail post-tool BLOCK làm mất `side_effects` của lệnh (`tool_runtime`);
    docstring `verification.py` hứa INCONCLUSIVE nhưng `verify()` chỉ ra PASS/FAIL (lệnh
    verify timeout bị ghi là FAIL).

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
