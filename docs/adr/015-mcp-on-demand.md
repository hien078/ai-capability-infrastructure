# ADR-015 — MCP server là capability được route; client bật theo từng prompt

- Status: Accepted (2026-10-10, user decision "làm 3 việc luôn" sau khi duyệt spec
  `docs/superpowers/specs/2026-10-10-mcp-on-demand-design.md`).
- Scope: MCP server selection per prompt for OpenCode (V2) clients.
- Refines: ADR-008/009 (routing pipeline, trusted text only), ADR-013/014 (ACI không thành
  execution plane cho tool bên thứ ba).

## Context

Đo ngày 2026-10-10: OpenCode trên Arch + Mac bật 6 MCP server; schema `tools/list` của chúng
≈ 10k (Mac) – 14k (Arch) token đi kèm MỖI lần gọi model, trong khi chỉ 0,9% (Mac) / 5,1% (Arch)
số phiên từng gọi bất kỳ tool MCP nào; `memory` và `duckdb` chưa từng được dùng. Người dùng muốn
"cho plugin lên ACI, cần thì mới dùng" để không phí context/token.

## Decision

1. **Một registry:** mỗi MCP server là MỘT capability kind `tool` (dùng `ToolSpec` sẵn có), id
   `mcp:<name>`, đi qua lifecycle release bình thường (production do người promote bằng capctl).
2. **Văn bản tin cậy:** mô tả routing của `mcp:*` do người duy trì ACI viết. Mô tả tool do MCP
   server tự khai báo là văn bản bên thứ ba và KHÔNG BAO GIỜ tới judge.
3. **Bộ chọn riêng:** `ToolServerSelector` (judge riêng, prompt + version riêng) chạy SONG SONG
   với pipeline skill; không đụng prompt JEV v7. Kết quả `tool_servers {decision, selected, …}`
   trong response `/v1/routes` (trường tùy chọn, tương thích ngược).
4. **Fail-open:** mọi lỗi / timeout / độ tin cậy thấp / selector tắt → `decision="all"` (giữ
   nguyên hành vi hiện tại). Chỉ chọn trong tập server client khai báo đã cài ∩ registry eligible.
5. **Client thực thi cục bộ:** plugin OpenCode V2 bỏ tool của server không được chọn trong hook
   `context` (trước mỗi lần gọi model) và đăng ký tool `aci_enable_mcp` để model bật lại server
   ngay trong lượt. Credential MCP ở lại client.
6. **ACI không bao giờ chạy, proxy hay giữ credential của tool MCP.**
7. **Mặc định tắt** (`ACI_TOOL_SELECTOR=off`, plugin `toolSelection: false`); bật production là
   quyết định của người dùng sau gate §7 của spec.

## Consequences

- Chỉ tiết kiệm context/token, KHÔNG tiết kiệm RAM (tiến trình MCP vẫn chạy).
- Phụ thuộc hành vi runtime của hook `context` trong OpenCode V2 — xác minh ở bước 0 (spike
  go/no-go) trước mọi code khác.
- Thêm một lệnh judge mỗi prompt (song song, giới hạn bởi in-flight limiter).
- Dữ liệu "cần MCP" hiếm (1–5% phiên) → đo nhiễu; gate cần ≥ 15 phiên dương tính held-out.

## Rejected

- **Gộp vào JEV** (một judge chọn cả skill và MCP): phá cấu hình đóng băng v7 của gate v4 và
  ghép lỗi hai bài toán.
- **Luật từ khóa trong plugin:** giòn, đúng kiểu đã thất bại ở router heuristic.
- **ACI proxy/chạy tool MCP phía server:** credential + execution plane mới, trái ADR-013/014.
- **Chọn theo từng tool:** ~90 lựa chọn, router hiện tại chưa đủ chính xác; để sau nếu số liệu cần.

## Verification

- Bước 0 spike: token đầu vào (9router `usageHistory`) giảm ≈ kích thước schema khi plugin bỏ tool;
  tool do plugin thêm gọi được.
- Unit/security tests: selector chỉ chọn subset; judge không thấy mô tả do MCP cung cấp; mọi lỗi →
  `"all"`; skill bundle không bao giờ chứa `mcp:*`.
- Gate spec §7 (held-out): tắt nhầm ≤ 10%, tiết kiệm ≥ 5.000 token/prompt, p95 route tăng ≤ 1 s,
  100% lỗi → `"all"`, security xanh.
