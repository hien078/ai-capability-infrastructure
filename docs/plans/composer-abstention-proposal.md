# Composer abstention — pre-registered proposal (DRAFT cho lead review)

> **Trạng thái: DỰ THẢO — chưa chạy, chưa code.** Soạn bởi night session 2026-10-02
> (~06:00) sau paired A/B `skip-oversized` bị chặn bởi tiêu chí pre-registered (c)
> (`data/aci-improvement/m7-routing-ab-result.md`).
> Không dòng code nào đổi, không phép đo heldout nào chạy. Lead duyệt thì mới mở PR +
> A/B đúng §3.1.

## 1. Vấn đề (đo rồi, không phỏng đo)

- **Abstention của composer hôm nay là budget accident, không phải judgment**: rule
  shipped = stop-at-first-oversized; bundle rỗng chỉ xảy ra khi rank-1 vượt budget
  (heldout: 8/30 rỗng, chỉ 2/4 đúng — `heldout-routing-cases-result.md` obs. #1/#3).
- **A/B skip-oversized (2026-10-02) chứng minh lấp-full là sai hướng**: +4 hitB nhưng
  phá abstention (2/4→0/4), misroutes 5→11, +87% token, 2 i>a gains → ADOPT=False.
  Bài học: cơ chế tiếp theo phải **abstain theo judgment** (confident-low → rỗng),
  không phải theo budget.

## 2. Cơ chế đề xuất

`MinimalBundleComposer` thêm option `abstain_floor: float | None = None` (default None =
hành vi shipped, byte-identical). Khi bật: nếu **top rerank score** của selection
< floor → bundle RỖNG (ADR-008: 0-item là success hợp lệ) + trace ghi
`stop_reason="abstain_floor"` (lý do mới, per-item reason giữ nguyên). Không đổi
reranker, không đổi rank order, không đổi budget.

## 3. Floor được dẫn xuất từ DEV (tập được phép tune) — heldout KHÔNG được đụng

Phân bố top rerank score trên DEV_CASES (31 case, replay `current.json` 2026-10-02,
read-only, không chạy gì mới):

- **min top-score của mọi case hit = 0.567** (`dev-comms-001`); p25 = 0.655.
- Case dev duy nhất miss (`dev-claude-api-001`, top 0.800) miss vì rank-1 oversized
  (claude-api ~21.5k > budget 8000), KHÔNG phải vì score thấp.
- **Dev không có case âm tính** → floor không thể validate trên dev; heldout (4 case
  empty-expected) là thẩm phán DUY NHẤT → vì vậy tiêu chí phải pre-register ở §4.

**Floor đề xuất: 0.55** (just dưới min dev hit 0.567 — "dưới mức mà dev chưa từng
hit thì abstain"). Đây là hằng số phán đoán được dẫn xuất từ dev, KHÔNG fit trên
heldout.

## 4. Tiêu chí pre-registered (viết TRƯỚC khi A/B chạy; công cụ: `routing_replay.py`
thêm variant `--composer abstain-floor`, cùng instrument, cùng 90 case)

Adopt `abstain_floor=0.55` làm default **CHỈ NẾU** cả 4 điều kiện giữ:

- (a) **heldout abstention đúng tăng**: abstention_correct trên 4 case empty-expected
  ≥ 3/4 (hiện 2/4 — và 2/4 đó là budget accident);
- (b) **0 hit→miss flip** trên dev + kernel (hit_in_bundle);
- (c) **0 i>a gain** (irrelevant-above-relevant);
- (d) **heldout hitB không giảm** (các case non-empty không được bắt đầu abstain).

Bất kỳ điều kiện nào sai → default giữ `stop` (không floor), variant ở lại sau option,
ghi âm tính vào AGENTS.md như skip-oversized.

## 5. Rủi ro ghi trước (trung thực)

- **Floor có thể không bắn trên heldout empty cases** — nếu top score của chúng > 0.55
  (chưa nhìn, không được nhìn), (a) fail → kết quả âm tính, đúng quy trình. Đó là kết
  quả hợp lệ, không phải thất bại của thí nghiệm.
- Score 0.800 tràn ở dev (nhiều case min-max calibration chạm trần) — floor 0.55 nằm
  dưới vùng trần nên không tương tác với hiệu ứng bão hòa.
- n=90 case, deterministic (không noise lấy mẫu) nhưng annotation author-written,
  routing-side only (§34); một corpus state (37 releases, 0 relations).
- **Không mở nếu lead muốn cơ chế khác** (retrieval-score floor, top-1/top-2 gap,
  per-domain floor) — đây là MỘT đề xuất trong họ cơ chế, không phải thiết kế khóa.

## 6. Chi phí khi duyệt

PR ước tính: composer option + trace reason (~30 dòng) + replay variant (~20 dòng)
+ unit test (floor bắn/không bắn/default-None byte-identical) + integration pin +
A/B 90 case × 2 arm (read-only, ~2 phút máy) + bảng paired A/B trong PR đúng §3.1.
Không migration. Không đổi default nào khác.
