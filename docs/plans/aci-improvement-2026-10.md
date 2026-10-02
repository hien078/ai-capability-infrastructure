# Kế hoạch cải tiến ACI — 2026-10 (actionable, tiếng Việt)

> **Loại:** kế hoạch công việc thực thi được + đánh giá đầu tư — **không phải** kiến trúc cạnh
> tranh. Thiết kế nền vẫn là `plan_v2_revised.md` + 14 ADR; vận hành vẫn là `AGENTS.md`; ảnh
> trạng thái + bản đồ 3 cấp: `docs/architecture-current.md`.
>
> **Phạm vi ủy quyền (user, 2026-10-01):** cải thiện cục bộ + tài liệu + sửa câu stale. **KHÔNG**
> bật tự động V4/V5/acquisition, không rollout production, không tuyên bố gate đã đạt. Mọi mở lại
> tính năng đều theo giai đoạn và phải có lý do đo được.
>
> **Quy ước:** mọi ngưỡng số trong kế hoạch này (n, tỉ lệ, số case) là **heuristic đề xuất**
> trừ khi ghi rõ là quyết định của người dùng hoặc có tính toán power đi kèm. Chúng không phải
> gate kiến trúc và không phải bằng chứng significance.

## 0. Nguyên tắc xuyên suốt

1. **Evidence standard:** một tính năng chỉ tính "sống" khi có thứ thật chạy qua nó. Frozen ≠ xóa.
2. **Kết quả đo được quyết định thành phần nào sống** (plan_v2 §80): nếu vòng lặp không cải thiện
   task thật một cách đo được → cải thiện hoặc **đơn giản hóa** tầng routing/capability **trước
   khi** thêm kiến trúc.
3. **§34:** kết luận đo là định hướng trừ khi có paired A/B với cỡ mẫu chọn theo effect /
   phương sai / ngân sách, và có kiểm soát.
4. **Production chỉ qua người:** automation tối đa tới staging/canary (kể cả giám sát và
   rollback tự động); **promote lên production luôn cần người cho phép rõ ràng** qua `capctl.py`
   (ADR-012/013).
5. **Giữ việc hoãn thấy được là chưa làm**; không đo lại thí nghiệm đã đóng (§80 n=33,
   skills-on-kernel amendment 16/17) nếu không có trục mới.

## 1. Chiến dịch hiện tại (R0/A/B/C/D)

| Việc | Owner | Phụ thuộc | Nội dung | Trạng thái (2026-10-01) |
|---|---|---|---|---|
| **R0** | resource worker | — | Hàng đợi CLI resumable, resource-bounded (`scripts/aci_worker_queue.py`) + subagent resource-guard (`docs/operations/`) | **Đã commit main** (`b57db24` + `414f591`); bản hardening theo review nằm ở `macbox/m2-queue-hardening` (chưa merge main) |
| **A** | docs worker | — | `docs/architecture-current.md` + bản này + đính chính CLAUDE.md/AGENTS.md/plan_v2 | **Đã commit main** (`7d2f7da`); bản sửa theo `docs-review.md` + gộp bản đồ 3 cấp và đánh giá đầu tư |
| **B** | code worker | R0 | Join bền giữa capability đã nạp trên kernel ↔ route/bundle/version/digest qua run events/checkpoints, kể cả resume; **không quy kết nhân quả** | **Đã commit main** (`5a72206`): `capability.loaded` mở rộng + `capability.exposure`; feedback seam được gọi nhưng REST chưa nối sink |
| **C** | reviewer | A, B, R0 | Review độc lập B + R0 + docs; regression/architecture/security | Đang chạy |
| **D** | integrator | A, B, R0, C | Full checks **một lần** khi mọi writer dừng; commit theo nhóm | Chờ C |

Chấp nhận = review + test, **không bao giờ** là exit code của worker. Báo cáo gián đoạn tài
nguyên ghi vào báo cáo của **chính task đó** (`data/aci-improvement/<task>-result.md`).
Điều kiện dừng hữu hạn: C không còn blocker → D chạy sạch một lần → báo cáo viết xong.

## 2. Giai đoạn tiếp theo — ba thí nghiệm quyết định (timebox đề xuất: 3 tuần)

Mục tiêu duy nhất: **trả lời dứt khoát ACI có giá trị ở đâu.** Không xây tính năng mới trong
giai đoạn này.

### E1 — Harness (HOW) có thật sự tốt hơn vòng lặp ngây thơ?

- **Vì sao:** đây là tín hiệu duy nhất cùng chiều qua các round (K vs N ≈ +0.13 ở 2 tier),
  nhưng chưa significant ở n=24.
- **Thiết kế:** H-bench K vs N, một model, fixture `verified`, sandbox bật, khai báo turn-budget
  note. Cỡ mẫu theo power: phát hiện 0.75 → 0.88 (hai phía, α=0.05, power 0.8) cần **≈136
  run/arm**; nếu effect thật nhỏ hơn thì cần nhiều hơn — chọn lại theo phương sai quan sát.
- **Chi phí ước tính:** ~6–7M token input (~24k/run), vài giờ máy ở parallel 4.
- **Kết quả → quyết định:** significant trên tests_pass hoặc tỉ lệ LIMIT_TURNS → HOW có giá trị
  đo được. Không significant ở cỡ mẫu đủ power → harness ngang vòng lặp ngây thơ ở tier này.

> **TRẠNG THÁI (2026-10-02): XONG — E1 = ✗ KHÔNG CHỨNG MINH ĐƯỢC ở tier glm-5.3.** Vòng đủ
> power đúng thiết kế (n=136/arm, 8 fixture §80 × 17 lượt = 272 run,
> `data/hbench/hbench-20261001-224132.json`, 0/272 run INVALID): tests_pass_at_end K
> 127/136 (0.934) vs N 121/136 (0.890) — Δ+0.044, z=+1.28, p=0.1996, CI 95% [−0.023,
> +0.111] (**điểm trên LOẠI TRỪ hiệu ứng +0.13 kế hoạch**); tỉ lệ chạm limit 0.375 vs
> 0.434 (p=0.323); McNemar exact không significant cả hai (13/7 p=0.263; 17/25 p=0.280).
> Per-fixture vẫn cùng chiều (K > N 3 fixture, hòa 5, thua 0) nhưng aggregate mới là câu
> trả lời: **cạnh "+0.13" n=24 của round 3 ở tier này là noise**; phát hiện +0.044 quan
> sát được ở power 0.8 cần ~650 run/arm. Ưu thế đo được của kernel còn lại là cấu trúc
> (false_success 0.00/272, verifier bác 51 proposal non-sát-điểm, at-limit evidence tách
> 42 fix-landed-unclaimed khỏi 9 nothing-landed) với chi phí +4.2% token input. Báo cáo:
> `data/hbench/REPORT-E1.md`; ghi chi tiết: ADR-014 amendment 18.

> **E1b (2026-10-02, Mac): ✗ — KHÔNG TÌM ĐƯỢC FIXTURE CÓ KHOẢNG TRỐNG.** Phase A chỉ arm N,
> 78 run, 0 INVALID: verified×flash 24/24, verified×glm-5.3 22/24, horizon(16 turn)×flash
> 15/15, horizon×glm-5.3 12/15 — không tổ hợp nào trong cửa sổ đăng ký trước [0.25, 0.75]
> → phase B không chạy. Trần của E1 không phải do 8 fixture; đóng băng kernel giữ nguyên.
> Ứng viên E1b′: horizon×glm-5.3, n lớn hơn, đăng ký lại. Ghi chi tiết: ADR-014 amendment 23.

### E2 — Skill có giá trị khi mang tri thức **phi công khai**?

- **Vì sao:** trục còn mở duy nhất của amendment 16/17; domain round cho thấy tri thức công khai
  (brand palette) đã nằm trong model.
- **Thiết kế (2 bước tách bạch):**
  1. **Kiểm tra hợp lệ task:** với **đúng skill/reference trong context**, model giải được trong
     budget (reach-in-budget WITH reference). Đây là kiểm tra tính khả thi của fixture — không
     phải đo lường.
  2. **Đo:** K (không skill) vs K + skill (preload hoặc reference) trên cùng fixture. Kỳ vọng nếu
     giả thuyết đúng: naked thất bại phần lớn, có skill thành công phần lớn.
- **Cỡ mẫu:** chọn theo effect kỳ vọng (một khoảng cách lớn như 20% → 80% cần rất ít run; heuristic
  đề xuất ≥10/case cho mỗi arm).
- **Dừng (đề xuất):** nếu hai use case hợp lệ liên tiếp không tách được tín hiệu → đóng trục
  "skill intelligence cho kernel", ghi vào AGENTS.md.

> **TRẠNG THÁI (2026-10-02): XONG — E2 = ✓ TÁCH BẮT ĐẦU TIÊN, tín hiệu dương ĐẦU TIÊN của
> skill trong lịch sử dự án — điều kiện dừng KHÔNG kích hoạt.** Fixture `--set private`
> (`scripts/private_tasks.py`): 2 chuẩn nội bộ HƯ CẤC, tri thức tồn tại duy nhất trong
> SKILL.md riêng của fixture và được pin bằng sha256 digest; verify fail-khi-ship /
> pass-sau-fix + reach-in-budget WITH-skill ≥2/3 TRƯỚC khi đo. Đo K (naked) vs R (cùng
> kernel + skill riêng của case PRELOAD từ handler local in-process — không registry, không
> DB, không router), n=10/case/arm, glm-5.3, bwrap (`data/hbench/hbench-20261002-003148.json`,
> 0/40 INVALID): **tests_pass_at_end K 0/20 vs R 17/20 (0.85)** — Fisher pooled 2.57e-08,
> từng case atlas 0.000714 / meridian 0.000119 (đều significant). Cổng knowledge giữ tuyệt
> đối: 0/20 run K sửa nguồn (chỉ tìm cách trích xuất chuẩn), 17/17 thành công của R sửa
> nguồn bằng từ vựng chỉ có trong skill, 0/40 workspace giả test. **Giới hạn phạm vi:** giá
> trị chỉ cho PRELOAD tri thức riêng — routing/acquisition KHÔNG đo (handler ghim vào case;
> 0/40 capability request); acceptance nhiễu (atlas p=0.21) — tests_pass_at_end là yardstick
> kết luận. Báo cáo: `data/hbench/REPORT-E2.md`; ghi chi tiết: ADR-014 amendment 19.

> **E2B (2026-10-02, Mac): ✓ registry + router giữ trần và hơn "doc nằm trong repo".**
> n=10/case/arm, registry = bản sao `aci_e2b`: R (pin) 17/20; **F (file trong repo) 9/20**;
> **Bp (registry + router, preload) 19/20 — hit hạng 1 20/20**; Bq (preload OFF,
> request_capability) 9/20 — model hỏi 12/20; K sạch 0/20. F vs R p=0.019, Bp vs F p=0.0013.
> Nút thắt của doc-trong-repo là việc model tự tìm doc (~52%). Không đổi default; preload
> ON cho kho tri thức riêng là quyết định treo (cần người dùng + vòng đăng ký trước trên
> use case thật). Ghi chi tiết: ADR-014 amendment 24.

> **E2C (2026-10-02, Mac): ✓ lặp lại E2B trên fixture mới.** 7 chuẩn mới (3 nêu tên, 4 gián
> tiếp); K 0/13 (không lộ); chỉ 3/7 qua cổng R ≥2/3 (4 bài quá khó dù có chuẩn — giới hạn
> của bộ fixture). Trên 3 bài hợp lệ: **F 8/24 vs Bp 17/24, p=0.0199**; router hạng 1 ở
> 24/24 run và 7/7 prompt ex-ante kể cả gián tiếp; F tìm thấy doc 0/8 ở bài gián tiếp.
> Ghi chi tiết: ADR-014 amendment 26.

### E3 — Có dùng thật không?

- **Thiết kế:** thử nghiệm 30 ngày với workflow thật, telemetry bật, **không xây gì mới** (đúng
  cam kết freeze 2026-09-29). Đo route_run / agent_run **hữu cơ** (client_type ≠ benchmark,
  probe, harness-kernel test) và outcome.
- **Vì sao:** theo bản ghi telemetry `aci_bench` (2026-10-01), 288 route_run phần lớn đến từ
  đo lường (benchmark-harness 112, harness-kernel 93, probe 19); OpenCode 46 đều trong proof loop
  28/9; agent_runs 5. Lượng dùng hữu cơ gần như bằng 0.
- **Instrument (2026-10-01):** `scripts/usage_report.py` — báo cáo read-only theo tuần ISO trên
  `aci_bench` (route_run ORGANIC vs MEASUREMENT, harness-kernel tách bằng linkage với agent_run;
  outcome/bundle; agent_run theo status); baseline ngày 0 + giới hạn đo:
  `data/aci-improvement/phase2/e3-usage-report-result.md`.

> **TRẠNG THÁI (2026-10-02): BASELINE NGÀY 0 ĐÃ GHI — hữu cơ = 0; đồng hồ 30 ngày đang chạy.**
> Tuần 2026-W40 trên `aci_bench`: 288 route_run = 65 phân loại "organic" + 223 measurement + 0
> unknown; agent_runs 5 (succeeded 5); 46 outcome event trên 44/286 bundle. **Cả 65 run
> "organic" đều có nguồn đo-lường/script/test đã biết** (46 opencode proof-loop §80, 13
> rest-client test/probe, 3 mcp-client path-check, 1 console probe, 2 harness-kernel
> validation) — hữu cơ thật = 0 ở ngày 0. Re-run hằng tuần: `scripts/usage_report.py [--json]`.
> Ghi chi tiết: ADR-014 amendment 20.

### Rẽ nhánh sau E1/E2/E3

```text
E1 ✓ E2 ✓ → đầu tư tiếp: held-out (2.2) → sửa ranking (v4sw…) → feedback (2.3) → acquisition
            có mục tiêu (2.6). ACI = WHAT + HOW đầy đủ.
E1 ✓ E2 ✗ → thu gọn: HarnessKernel là sản phẩm chính; Control Plane chỉ còn catalog +
            governance cho client; đóng băng intelligence nâng cao.
E1 ✗ E2 ✓ → giữ Control Plane + kho tri thức nội bộ; đóng băng kernel; ACI về đúng plan_v2 V1–V2.
E1 ✗ E2 ✗ → đơn giản hóa tối đa: catalog skill có quản trị (registry + gates + OpenCode catalog);
            đóng băng phần còn lại; viết tổng kết bài học.
E3 ✗      → bất kể E1/E2: không xây thêm gì hướng người dùng; chỉ giữ phần nghiên cứu.
```

> **NHÁNH ĐÃ ĐI (2026-10-02): `E1 ✗ E2 ✓`** — "giữ Control Plane + kho tri thức nội bộ;
> đóng băng kernel; ACI về đúng plan_v2 V1–V2". Không thay đổi bất kỳ default/product
> setting nào (`ACI_AGENT_CAPABILITY_PRELOAD` giữ OFF, `request_capability` giữ offered,
> không dòng kernel nào đổi). E3 còn đồng hồ 30 ngày (ngày 0: hữu cơ = 0) và là override
> còn treo: nếu hữu cơ ≈ 0 đến ngày 30 → theo bảng, không xây thêm gì hướng người dùng,
> chỉ giữ phần nghiên cứu. Ghi chi tiết: ADR-014 amendment 21 + AGENTS.md 2026-10-02.

## 3. Hạng mục có thể mở (chỉ sau giai đoạn 2, theo nhánh rẽ)

### 3.1 Đánh giá routing held-out TRƯỚC khi đổi ranking

- `DEV_CASES` / `KERNEL_QUERY_CASES` đã dùng để chẩn đoán và tune → **không thể** thành held-out
  bằng cách đổi nhãn. Cần **tập mới, rời rạc, chú thích độc lập** với người thay đổi router; các
  tập cũ giữ vai trò regression/dev.
- Mọi PR đổi reranker/embedder/composer kèm bảng paired A/B trên held-out + dev.

> **TRẠNG THÁI (2026-10-02, đêm): A/B PAIRED ĐÃ CHẠY — `skip-oversized` BỊ CHẶN, default giữ
> `stop`.** Instrument committed `scripts/routing_replay.py` (m7, main 04:26) + 90 case
> (heldout 30 + dev 31 + kernel 29), fastembed, `aci_bench` read-only, deterministic, tiêu
> chí pre-registered in instrument. (a) heldout hitB 17→21 ✓; (b) 0 hit→miss ✓; (c) 2 case
> thu được i>a ✗ → **ADOPT = False** — skip lấp đầy mọi bundle (+87% token), phá abstention
> (2/4→0/4), misroutes 5→11. NaN-fix byte-identity: replay pre-fix `3696a7c` vs HEAD giống
> nhau từng byte 90/90 case. Chi tiết: `data/aci-improvement/m7-routing-ab-result.md`;
> JSON `data/routing-replay/*.json`. Bài học cho đề xuất composer tiếp theo: abstain theo
> judgment (confidence floor), không theo budget accident.

### 3.2 Feedback completeness (sau B)

- Nối `CapabilityFeedback` sink trên REST; đo tỉ lệ run có chuỗi evidence đầy đủ
  route → bundle → activation → verdict (quan sát, không nhân quả). Nếu run events đủ làm cầu,
  không mở migration mới.

> **TRẠNG THÁI (2026-10-02, đêm): XONG HẲN — đo + wiring.** Baseline (instrument m8,
> `764611d`): agent-run chain complete 2/2 plane-touching; bundle outcome coverage
> **0.1502** (44/293; opencode 38, rest-client 4, mcp-client 2). Wiring (cùng đêm, red-test
> first): `RegistryCapabilityFeedback` sink (`adapters/outbound/agent_capabilities.py`) —
> seam terminal-state của kernel giờ nối §33 qua `ReportOutcomeService` trên MỌI agent run
> REST; MỘT event per routed bundle, verdict map trung thực (`run_success` → test_harness
> success/high [INV-08] + agent_self_report; VERIFICATION_FAILED → test_harness
> failure/high; failure khác → CHỈ self-report; limit/cancel → unknown/low), envelope
> không fabricate (build/lint/tokens NULL — pin bằng test). Full suite 1824 passed / 0
> failed. V5 (§3.4) vẫn hoãn cho tới traffic hữu cơ. Chi tiết:
> `data/aci-improvement/{m8-evidence-completeness,m8-wiring}-result.md`.

### 3.3 V4 service theo nhu cầu thật

- Không dựng service nào trước. Chỉ mở khi một task thật của client thật không giải được bằng
  REST/MCP hiện có, ghi task đó làm bằng chứng (mẫu V4-1 Evaluation).

### 3.4 V5 offline proposals

- Hoãn tới khi feedback completeness đo được ở mức cao (heuristic đề xuất ~80% run) và corpus phủ
  ≥ 2 họ task thật. Không tuyên bố V5 trong tài liệu trước đó.

### 3.5 Acquisition có chủ đích

- crawl.md giữ FROZEN tới khi có use case: model thất bại naked và một skill ngoài corpus cứu
  được. Khi có, chỉ mở **targeted scout** cho gap đó; mọi candidate qua `capctl.py`.

### 3.6 Việc nhỏ làm ngay

- Checklist "câu lỗi thời" trong review (số ADR, số client, sandbox, cleanup, token cap).
- `claude-api` (~21.5k token) không route được dưới 8000: **không** nâng default; vấn đề kích
  thước corpus, chỉ xử lý (C3/C4) nếu 3.1 mở và kết quả bảo vệ việc split.

## 4. Giới hạn đo lường & điều kiện dừng toàn cục

- **Không đo** trên: pytest trỏ `aci_bench`, truncate DB, benchmark model thật không có resource
  gate, re-run §80/skills-on-kernel không trục mới.
- Mọi round đo mới khai báo: n, model, fixture set, yardstick, trạng thái sandbox, và có so sánh
  được với round trước không (turn-budget note làm round ≤3 không so trực tiếp được). Run
  `MODEL_FAILURE` (gateway 5xx) là **INVALID** — loại khỏi tỉ lệ và đếm rõ.
- **Dừng toàn cục:** khi mọi hạng mục ở §2–§3 đã (a) hoàn thành hoặc (b) đóng lại kèm lý do trong
  AGENTS.md — tài liệu này khi đó chỉ còn giá trị lịch sử.

## 5. Quy tắc tài nguyên (kế thừa từ chiến dịch)

- Queue cha tối đa 2 worker; mỗi worker tối đa MỘT subagent read-only; không đệ quy, không spawn
  CLI ngoài; con dùng chung cgroup.
- Áp lực tài nguyên hoặc yêu cầu dừng → lưu edit, ghi tiến độ vào báo cáo của chính task, thoát;
  phiên sau resume.

## 6. Đánh giá công tâm — có đáng đầu tư như một mục tiêu tự thân?

*Coi ACI là mục tiêu tự thân, không phục vụ ai cụ thể; chỉ dựa trên bằng chứng trong repo/DB.*

### 6.1 Điểm mạnh thật

- **Chất lượng kỹ thuật cao** so với tuổi đời: phân lớp có boundary test, invariant khóa bằng
  test, sandbox fail-closed, ranh giới chống prompt-injection, cổng con người, CI có security job,
  migration sạch.
- **Văn hóa đo lường trung thực:** kết quả âm tính được ghi lại (§80 không delta; skills-on-kernel
  không tăng tests_pass; cleanup rollback). Tài sản này đáng giá hơn phần lớn số dòng code.
- **Một tín hiệu thật:** harness (verifier-gated completion, kỷ luật dừng) hơn vòng lặp ngây thơ
  cùng chiều qua các round.

### 6.2 Điểm không đứng vững

1. **Luận điểm cốt lõi chưa được chứng minh:** "chọn đúng capability giúp agent tốt hơn" đã thử
   bốn cách (§80 với OpenCode; offered, preload, model yếu, domain trên kernel — 222 run) và chưa
   lần nào tăng tests_pass ở tier đã đo. Model không tự xin skill; tri thức công khai đã có sẵn.
2. **Đi ngược kỷ luật của chính plan:** §80 khuyên đơn giản hóa nếu không cải thiện được, nhưng
   V2/V3/HarnessKernel tiếp tục được xây sau kết quả âm tính; kernel mở lại "theo quyết định,
   không phải vì điều kiện unfreeze đạt" (AGENTS.md); cam kết 30 ngày "không xây gì mới" bị bỏ qua.
3. **Độ phức tạp vượt giá trị đã đo:** dự án ~5 ngày tuổi (commit đầu 2026-09-27) đã có ~25k dòng
   src, ~30k dòng test, ~9k dòng script, ~23k dòng tài liệu, 19 migration, 14 ADR; lượng dùng hữu
   cơ gần bằng 0; tài liệu stale lặp lại (số ADR, số client, sandbox non-goal, trạng thái cleanup,
   token cap) — dấu hiệu chi phí bảo trì đã vượt khả năng theo dõi.
4. **Khác biệt mỏng:** client lớn (OpenCode, Claude Code…) đã có cơ chế skill gốc với progressive
   disclosure; ở quy mô vài chục skill, router trung tâm chưa thêm giá trị đo được. Router có lợi
   thế rõ khi catalog rất lớn hoặc cần governance cấp tổ chức — cả hai chưa tồn tại. Lợi thế đo
   được của kernel (verifier-gated completion) có thể đạt bằng hook/plugin trong client sẵn có.

### 6.3 Chấm điểm (0–5, đánh giá của lead, không phải số đo)

| Tiêu chí | Điểm |
|---|---|
| Chất lượng kỹ thuật | 4.5 |
| Bằng chứng giá trị cho WHAT (capability intelligence) | 1 |
| Bằng chứng giá trị cho HOW (harness) | 2.5 — cùng chiều, chưa significant; E1 chốt được với chi phí thấp |
| Khác biệt so với giải pháp sẵn có | 1.5 |
| Tỉ lệ giá trị / độ phức tạp | 1 |
| Giá trị học tập / nghiên cứu | 4 |

### 6.4 Kết luận

- **Như một hạ tầng capability tổng quát theo plan (V3 → V4 → V5): hiện không đáng đầu tư thêm
  theo chiều rộng.** Luận điểm cốt lõi chưa đứng; độ phức tạp đã vượt giá trị; chính plan_v2 §80
  khuyên đơn giản hóa trong tình huống này.
- **Như một câu hỏi nghiên cứu thu hẹp — đáng**, với điều kiện: (1) đóng băng chiều rộng ngay;
  (2) chạy E1 + E2 (rẻ, quyết định, có điều kiện dừng); (3) chạy E3 song song; (4) chấp nhận nhánh
  rẽ ở §2, kể cả nhánh thu gọn — đó đúng là tinh thần "kết quả đo được quyết định thành phần nào
  sống" mà dự án tự đặt ra.

> ACI đã được **xây tốt**, nhưng chưa được **chứng minh là đáng xây**. Bước giá trị nhất tiếp theo
> không phải là code, mà là ba thí nghiệm quyết định — và sẵn sàng thu nhỏ nếu chúng âm tính.
