# Kiến trúc ACI hiện tại — ảnh trạng thái + bản đồ 3 cấp (2026-10-01)

Tài liệu này là **ảnh trạng thái hiện tại** (current-state overview), viết sau khi đối chiếu
mã nguồn và các bản ghi trong `AGENTS.md`. Nó không thay thế:

- `plan_v2_revised.md` — nguồn chân lý **thiết kế** (đã được 14 ADR khóa lại);
- `AGENTS.md` — nguồn chân lý **vận hành** (lệnh, gotcha, kết quả đo);
- `docs/adr/` — 14 ADR Accepted (001–014; ADR-014 có các amendment tới 17).

Kế hoạch hành động và đánh giá đầu tư: `docs/plans/aci-improvement-2026-10.md`.

**Ký hiệu trạng thái:** ✅ đang chạy thật (có test, đã có thứ thật đi qua) · ⏸ dựng xong,
mặc định TẮT (quyết định đo lường) · ❄️ dựng xong, ĐÓNG BĂNG (mounted, không mở rộng, không gỡ)
· 🔲 đề xuất/chưa dựng · ✖ quyết định không làm. Các con số corpus/run/telemetry dưới đây là
**bản ghi theo thời điểm** (AGENTS.md, ngày 2026-10-01), không phải quan sát tươi — khi cần số
hiện tại, query trực tiếp `aci_bench`.

## 1. Định vị — ba mặt phẳng trách nhiệm

Quy tắc kiến trúc chuẩn (harness.md §0.1):

> **ACI Capability Intelligence quyết định WHAT** — capability nào phù hợp.
> **HarnessKernel quyết định HOW** — một objective được ủy quyền thực thi ra sao.
> **Client giữ global DAG** — mục tiêu toàn cục và thứ tự task thuộc về client (OpenCode /
> Antigravity / Goose / CLI), không thuộc ACI.

Câu "**Not** an agent platform" đã lỗi thời từ 2026-09-30: ACI **không phải nền tảng agent
tổng quát**, nhưng **có một mặt phẳng thực thi agent thật** (HarnessKernel, ADR-014) chạy
end-to-end, verifier-gated, sandboxed, durable. Hai mặt phẳng nằm trong một modular monolith
(ADR-011) nhưng tách bằng package + boundary test; kernel lấy capability qua **đúng**
`RouteCapabilitiesService` mà `/v1/routes` dùng — tức kernel là **một client tham chiếu của
Control Plane**, chỉ được đóng gói chung.

## 2. Phân lớp mã

Mã tồn tại không đồng nghĩa đang vận hành — trạng thái thật của từng khối ở §3.

- `src/aci/domain/` — hợp đồng Pydantic thuần (capability, skills, provenance, policy,
  taxonomy, routing, agent, evaluation, acquisition ❄️, **runtime**). Không import
  FastAPI/SQLAlchemy/OpenCode/MCP.
- `src/aci/application/` — use case trên Protocol (route, search, outcome, delegate ❄️,
  evaluate, **run_agent_task**, **payload_sizes**; extract_candidates/refine/… ❄️).
- `src/aci/routing/` — pipeline §14: eligibility → retrieval (pgvector) → rerank → resolve →
  compose.
- `src/aci/adapters/inbound/{rest,mcp,opencode,a2a ❄️}/` — mọi logic protocol/client.
- `src/aci/adapters/outbound/{postgres,pgvector,model_provider,object_store,agent_capabilities}/`.
- `src/aci/providers/{skills,licensing,security,evaluation ❄️}/`,
  `src/aci/control_plane/promotion/`, `src/aci/evaluation/` (SMOKE/DEV/KERNEL_QUERY/HARNESS).
- `src/aci/domain/runtime/` + `src/aci/runtime/` — **HarnessKernel**: StateManager là cơ quan
  trạng thái khả biến duy nhất (INV-01); completion verifier-gated (INV-08); authority con ⊆
  cha (INV-02); delegation mặc định OFF. SYNC, unit-test được với fakes.

## 3. Trạng thái từng khối

| Mức | Khối | Ghi chú |
|---|---|---|
| ✅ Đang chạy thật (WHAT) | Routing pipeline + REST `/v1/*` + OpenCode catalog/plugin + MCP adapter + outcomes + V4-1 Evaluation | 3 client thật đã kết nối: **OpenCode, Antigravity, Goose**. Lưu lượng hữu cơ (ngoài đo lường) gần như bằng 0 theo telemetry — xem §6 |
| ✅ Đang chạy thật (HOW) | HarnessKernel + `POST /v1/agent-runs` (+ resume, approval pause) | Unfrozen 2026-09-30 **theo quyết định người dùng (option A)**, không phải vì điều kiện unfreeze đạt. Durable runs (0016), revision state (0018), pause checkpoints (0019), bwrap sandbox (amendment 13), approval (amendment 14), capability evidence join (đợt này) |
| ❄️ Dựng xong — đóng băng | A2A gateway, OpenAICompatExecutor, aci-coder, task persistence; acquisition era (judges, refinery, crawler, canary) | Vẫn mounted; không mở rộng wire A2A; acquisition chỉ xét mở lại khi có use case cụ thể |
| ⏸ Dựng xong — mặc định TẮT | `ACI_AGENT_CAPABILITY_PRELOAD` (OFF); semantic embedder `ACI_EMBEDDER=fastembed` (chỉ bật trên process server vận hành; mặc định hashing cho test/CI offline) | Preload OFF là quyết định đo lường **trong giới hạn đã đo** (một họ model glm-5.3/flash, fixture sửa lỗi + 4 fixture domain): không tăng tests_pass, ~3× token. Không phải kết luận "preload vô giá trị ở mọi nơi" |
| 🔲 Đề xuất / chưa dựng | Reranker v4sw (bị từ chối per §34), C3/C4 tách claude-api (hoãn), relations trong `aci_bench` (cleanup đã rollback), quota/deadline, Service Provider, SDK projection, shadow routing, async semantics, V4 services 2–6, V5, resume từ periodic checkpoint | Giữ **thấy được là chưa làm** |

## 4. Sự thật đã kiểm chứng tại chỗ (2026-10-01)

- **14 ADR** Accepted (001–014); **migrations 0001–0019**.
- Reranker **v3** (`heuristic.py`, `VERSION = "3"`, min-max calibration per-signal).
- Composer **v2** — budget theo **kích thước SKILL.md thật** (`application/payload_sizes.py`),
  `DEFAULT_MAX_CONTEXT_TOKENS = 8000` (ADR-008 amended).
- Kernel capability selection: score margin **0.15**, tối đa **3** item, tổng token =
  `DEFAULT_MAX_CONTEXT_TOKENS` (**8000**) — một núm, không phải hai trần.
- Production promotion: **chỉ con người cho phép** (`scripts/capctl.py promotion approve`).
  Automation được đưa tới staging/canary và có thể giám sát/rollback tự động — nhưng **không
  bao giờ** tự promote lên production (ADR-012/013).
- Corpus cleanup C1+C2: theo bản ghi AGENTS.md (commit `f796641`), **đã áp dụng rồi rollback
  cùng ngày** bởi người dùng — `aci_bench` về **0 relations**, 37 production release không đổi.
- H-bench (K vs N): theo bản ghi AGENTS.md, edge định hướng **+0.13** ở cả hai tier đã đo, **không
  significant** tại n=24 (§34); false_success = 0 ở mọi round; không cơ chế đơn lẻ nào mang hết
  edge (ablation).
- Skills-on-kernel: đóng lại theo ADR-014 amendment 16/17 — `request_capability` vẫn offer,
  preload OFF; chỉ mở lại theo trục mới (model yếu thật / tri thức phi công khai / corpus qua
  human gate / fixture verify reach-in-budget).

## 5. Hai database — vệ sinh đo lường

Một container, hai DB: `aci` = dev/test (tích lũy fixture `uid()`, **không** là thước corpus);
`aci_bench` = vận hành (corpus thật + telemetry). Ops scripts mặc định `aci_bench`; app/tests
mặc định `aci`. Không bao giờ trỏ pytest vào `aci_bench`.

## 6. Bản đồ 3 cấp

### Level 1 — toàn hệ thống

```text
┌──────────────────────────────────────────────────────────────────────┐
│ AI CLIENTS: OpenCode ✅ | Antigravity ✅ | Goose ✅ | CLI/scripts ✅  │
└──────────────────────────────┬───────────────────────────────────────┘
┌──────────────────────────────▼───────────────────────────────────────┐
│ ADAPTER / ACCESS: REST ✅ | OpenCode catalog+plugin ✅ | MCP ✅ | A2A ❄️│
│ bearer tokens ✅ (mặc định rỗng = chỉ localhost) | quota/deadline 🔲   │
└───────────────┬────────────────────────────────────┬─────────────────┘
          WHAT  ▼                              HOW   ▼ (tùy chọn)
┌───────────────────────────────────┐  ┌───────────────────────────────┐
│ CONTROL PLANE ✅ registry·versions │  │ HARNESSKERNEL ✅ /v1/agent-runs│
│ releases·bindings·provenance·policy│◄─┤ verifier-gated · bwrap · approval│
│ INTELLIGENCE ✅ elig→retr→rerank→  │  │ resume · durable · recovery     │
│ resolve→compose→pin                │  │ skills: offered ✅ preload ⏸    │
└───────────────┬───────────────────┘  └───────────────────────────────┘
                ▼
  EXECUTION SEMANTICS: skill client-orchestrated ✅ | tool remote ✅ |
                       agent delegated ❄️ | service 🔲 | async 🔲
                ▼
  STORAGE ✅: PostgreSQL 16 + pgvector | object store sha256 | aci / aci_bench

  Xuyên suốt: Supply chain & governance ✅ · Observability & evidence ✅ ·
              Measurement ✅ · Acquisition tự động ❄️ · Adaptive (V5) 🔲
```

### Level 2 — module

| # | Module | Thực tế |
|---|---|---|
| 1 | Access & request context | ✅ principal/scope/client_type; quota/deadline 🔲 |
| 2 | Admission / eligibility (ADR-009) | ✅ release → kind → trust → license → scope → compatibility, **trước** retrieval |
| 3 | Capability intelligence | ✅ retrieval pgvector; rerank heuristic v3 (đếm cả từ chức năng — chẩn đoán đã có, v4sw bị từ chối); resolve requires/conflicts; compose v2 (SKILL.md thật, 8000); pin id@version+digest+router versions. Hiểu task có cấu trúc: ⚠️ một phần (text + facet) |
| 4 | Catalog core | ✅ Capability · Version (bất biến) · Release (con trỏ) · Binding · Spec union skill/tool/resource/workflow; corpus vận hành 36 skill + `aci-coder` |
| 5 | Supply chain & governance | ✅ ingest → quarantine → gates G1–G8 → staging → production (**người**); ❄️ acquisition tự động |
| 6 | Providers & semantics | ✅ skill, tool; ❄️ agent (AgentProfile/A2A); 🔲 service, async |
| 7 | Projection / adapters | ✅ OpenCode catalog, MCP, REST; ❄️ A2A; 🔲 SDK |
| 8 | HarnessKernel | ✅ xem Level 3 luồng B |
| 9 | Observability & evidence | ✅ route_runs → bundles → outcomes; agent_runs + agent_run_events (+ capability.loaded / capability.exposure: **quan sát, không nhân quả**) |
| 10 | Measurement | ✅ DEV(31)/SMOKE/KERNEL_QUERY(29)/HARNESS cases, H-bench (K/N/S/P, set verified/domain), §80 loop; 🔲 held-out độc lập, shadow routing; ✖ telemetry tự sửa production |

**Ranh giới chống prompt-injection (ADR-008/009):** chỉ embed/rerank **tóm tắt chuẩn hóa tin
cậy**; thân SKILL.md bên thứ ba không bao giờ tới router; tầng kích thước chỉ đọc `size_bytes`.

**JEV reranker (2026-10-07):** ⏸ `JevReranker` (LLM judge, biết bỏ chọn) + `OpenAICompatSkillJudge`
đã dựng xong sau `ACI_RERANKER=heuristic|jev` — mặc định **heuristic**, `jev` fail-closed khi thiếu
`ACI_JEV_*`; cổng §4 (`scripts/jev_eval.py`, đo trên home-sever) chưa chạy → chưa bật đâu cả.

### Level 3 — hai luồng chạy thật

**Luồng A — Routing (WHAT)**, ví dụ OpenCode hỏi skill cho một task:

```text
client → /v1/routes → RouteCapabilitiesCommand → context (principal/scope)
→ eligibility (36 skill production) → retrieval (fastembed trên server vận hành)
→ rerank v3 → resolve (hiện 0 relation) → compose (≤5 item, ≤8000 token SKILL.md thật;
0 item hợp lệ) → pin → route_runs rồi bundles → client tự nạp skill và tự làm việc
→ outcome (tùy chọn) → benchmark DEV_CASES → thay đổi production chỉ qua người
```

**Luồng B — Agent run (HOW):**

```text
POST /v1/agent-runs {objective, workspace, verification_command, approval_required_tools?}
→ copy workspace; grants = request ∩ trần server (INV-02)
→ vòng lặp: model → action → authority/guardrail → bwrap sandbox → observation (CAS, INV-01)
   · request_capability → luồng A (trim ≤3, margin 0.15) → SKILL.md đã verify digest vào context
   · tool cần duyệt → INTERRUPTED_APPROVAL → checkpoint bền → resume đúng 1 lần
   · lỗi → RecoveryManager (retry chỉ tool idempotent)
→ final_candidate → verifier dựa trên evidence (INV-08) → SUCCEEDED | phản hồi | FAILED
→ RunResult + events (kể cả capability.exposure ở kết thúc thật) lưu bền
```

## 7. Đính chính các câu gây hiểu lầm

| Câu cũ | Đúng hiện tại |
|---|---|
| "Not an agent platform" | Không phải nền tảng agent **tổng quát**; có mặt phẳng thực thi thật (HarnessKernel) |
| "13 accepted ADRs" | **14** (001–014) |
| "two real clients (OpenCode + Antigravity)" | **Ba**: + Goose |
| "SandboxWorkspace is a non-goal" | Thay bởi amendment 13: **bwrap sandbox live**, fail-closed |
| "corpus cleanup awaiting the human gate" | Đã áp dụng rồi **rollback** cùng ngày; 0 relations |
| "composer budgets by routing summary" | Composer v2 budget theo **SKILL.md thật**, mặc định 8000 |
| "kernel skill token cap 10000" | = `DEFAULT_MAX_CONTEXT_TOKENS` = **8000** |
| "ACI không trực tiếp sửa code" (bản đồ V3 cũ) | Đúng cho luồng A; luồng B (HarnessKernel) **có** sửa code — trong sandbox, verifier-gated |
