# aci-sync — đẩy skill từ registry vào thư mục skill gốc của client (`scripts/aci_sync.py`)

> Job `p-aci-sync` (2026-10-03). Bối cảnh: rc-bench (ADR-014 amendments
> 30/34) đo được việc OpenCode **tự chọn skill native rất tốt** — với client
> như vậy, ACI không nên chen vào giữa bằng router, mà nên làm **nguồn
> governed** và giữ thư mục skill native của client luôn đồng bộ với
> registry. `aci_sync` là tool kéo (pull) đó: chỉ dùng HTTP tới catalog của
> một server ACI (không chạm DB), ghi ra thư mục skill gốc.

## Cách dùng

```bash
# xem kế hoạch trước (không ghi gì cả):
ACI_API_TOKEN=<bearer> .venv/bin/python scripts/aci_sync.py --client opencode --dry-run

# sync thật (mặc định: ~/.config/opencode/skills/):
ACI_API_TOKEN=<bearer> .venv/bin/python scripts/aci_sync.py --client opencode

# sync vào project (DIR/.opencode/skills/ — OpenCode quét theo project):
.venv/bin/python scripts/aci_sync.py --client opencode --project ~/myproj

# Claude Code (tự động dùng ~/.claude/skills/, hoặc DIR/.claude/skills/):
.venv/bin/python scripts/aci_sync.py --client claude-code

# Goose (~/.agents/skills/, hoặc DIR/.agents/skills/ với --project):
.venv/bin/python scripts/aci_sync.py --client goose --server http://aci.internal:8000

# Antigravity (~/.gemini/config/skills/, hoặc DIR/.agents/skills/ với --project):
.venv/bin/python scripts/aci_sync.py --client antigravity --server http://aci.internal:8000

# một thư mục bất kỳ (custom agent, pipeline):
.venv/bin/python scripts/aci_sync.py --client dir --target /srv/agent/skills
```

- `--server URL` (mặc định `http://127.0.0.1:8000`) — server ACI cần mount
  catalog `/opencode/skills` (mặc định của `aci.main`).
- `ACI_API_TOKEN` (env, **không** truyền qua argv): nếu server bật
  `ACI_API_TOKEN` thì mọi request mang `Authorization: Bearer …`. Token
  không bao giờ được in ra output.
- Filter: `--include GLOB`, `--exclude GLOB` (fnmatch trên tên skill,
  lặp được nhiều lần), `--only-ids a,b,c`. Skill bị filter out thì **không
  cập nhật, không xoá** — chỉ bị bỏ qua.
- `--verify`: tải lại + hash lại mọi skill (kể cả skill "unchanged") để phát
  hiện nội dung local bị sửa. Mặc định tool tin (version, danh sách file)
  khớp lockfile là không đổi (version của registry là immutable, §6).
- `--force`: prune kể cả khi nội dung local đã drift khỏi lockfile.
- Exit code: `0` sạch (kể cả no-op), `1` có lỗi (skill bị reject, HTTP
  fail, lockfile hỏng — phần còn lại của plan vẫn chạy), `2` sai usage.

## Thư mục đích theo client

| `--client`    | Mặc định (global)            | `--project DIR`          |
|---------------|------------------------------|--------------------------|
| `opencode`    | `~/.config/opencode/skills/` | `DIR/.opencode/skills/`  |
| `claude-code` | `~/.claude/skills/`          | `DIR/.claude/skills/`    |
| `goose`       | `~/.agents/skills/`          | `DIR/.agents/skills/`    |
| `antigravity` | `~/.gemini/config/skills/`   | `DIR/.agents/skills/`    |
| `dir`         | bắt buộc `--target DIR`      | (không hỗ trợ)           |

- **Goose 1.52** đọc skill ở `~/.agents/skills/` và `~/.claude/skills/`
  (global) và `.agents/skills/` (project — theo help text đi kèm binary).
  Tool chọn `~/.agents/skills/` để **không dùng chung thư mục/lockfile với
  Claude Code** (`~/.claude/skills/`). Nếu đã sync cả `claude-code` thì Goose
  sẽ thấy skill ở cả hai nơi — trùng tên là bình thường, nội dung như nhau.
- **Antigravity**: customization root global là `~/.gemini/config/`, root
  workspace là `.agents/` ở gốc repo; skill nằm ở `<root>/skills/<name>/SKILL.md`
  (theo tài liệu `agy-customizations` đi kèm Antigravity, kiểm tra 2026-10-05).
- Lưu ý: với `--project`, `goose` và `antigravity` trỏ vào **cùng một thư
  mục** `DIR/.agents/skills/` → cùng một lockfile, cùng một tập skill được
  quản lý; sync một lần là cả hai client đều thấy.

## Bố cục trên đĩa

Catalog của ACI phục vụ entry file dưới tên `<name>.md` (hợp đồng skill-ID
của OpenCode qua HTTP, ADR-005). Thư mục local là **source root** — ở đó
entry phải tên `SKILL.md` (tài liệu OpenCode V2, kiểm tra 2026-10-03:
`~/.config/opencode/skills/<name>/SKILL.md`, `.claude/skills/<name>/SKILL.md`).
`aci_sync` map ngược lại khi ghi:

```
<target>/<name>/SKILL.md            # = catalog <name>.md (entry)
<target>/<name>/references/...      # các file khác giữ nguyên đường dẫn catalog
```

## Lockfile `<target>/.aci-sync.lock.json`

```json
{
  "version": 1,
  "server": "http://aci.internal:8000",
  "synced_at": "2026-10-03T12:00:00+00:00",
  "skills": {
    "debugging": {"version": "1.0.0", "files": {"SKILL.md": "<sha256>", "references/x.md": "<sha256>"}}
  }
}
```

- **Chỉ skill nào nằm trong lockfile mới được update/prune.** Thư mục skill
  của người dùng (không có trong lock) **không bao giờ bị chạm vào**; skill
  server phục vụ trùng tên với thư mục người dùng → bị từ chối (exit 1).
- Skill bị revoke/bỏ khỏi index của server → **prune ở lần sync kế tiếp**
  (xoá cả dir lẫn entry lock). Nếu nội dung local đã drift (người dùng sửa?)
  thì prune bị từ chối kèm cảnh báo, cần `--force`.
- Idempotent: lần chạy thứ hai với cùng index là **no-op tuyệt đối** —
  không tải lại, không ghi lại, lockfile không đổi một byte.
- Đổi `--server` so với lockfile: cảnh báo + ghi đè provenance.

## An toàn (pin trong `tests/security/test_aci_sync_safety.py`)

- Đường dẫn file từ server phải là **relative POSIX, không `..`, không
  absolute, không backslash** — skill vi phạm bị **reject cả skill**, không
  ghi gì ra ngoài target.
- Lockfile **không bao giờ ghi xuyên qua symlink** (temp + `os.replace`);
  dir skill đã thành symlink thì bị **thay thế**, không bị write-through.
- Replace từng skill là **atomic**: ghi vào temp dir trong target rồi
  `rename` đổi chỗ (dir cũ được dời sang `.aci-sync-old-*`, chỉ xoá sau khi
  dir mới vào chỗ; crash giữa chừng để lại rác `.aci-sync-tmp-*`/`.aci-sync-old-*`,
  lần sync sau tự dọn).
- `--dry-run` **không ghi gì cả** — kể cả lockfile.
- Mỗi file tải về bị chặn ở 64 MiB (đọc streaming).

## Catalog cung cấp gì (điều cần biết)

`GET /opencode/skills/index.json` hiện trả
`{"skills": [{"name", "version", "files": [đường_dẫn, …]}]}` — **chỉ tên +
version, KHÔNG có digest từng file**. Vì vậy:

- sha256 trong lockfile là **tool tự tính từ bytes tải về** (phát hiện
  drift/tamper local ở các lần sync sau, `--verify`);
- nếu một index tương lai mang `{"path", "sha256"}` từng file thì tool
  **verify từng byte** trước khi ghi (đã có test).

## Ví dụ cron / launchd (KHÔNG tự cài — chỉ ví dụ)

Cron (Linux, mỗi giờ):

```cron
0 * * * * ACI_API_TOKEN=<bearer> /path/to/.venv/bin/python \
    /path/to/scripts/aci_sync.py --client opencode >> ~/.cache/aci-sync.log 2>&1
```

launchd (macOS) — `~/Library/LaunchAgents/com.hien.aci-sync.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.hien.aci-sync</string>
  <key>ProgramArguments</key><array>
    <string>/path/to/.venv/bin/python</string>
    <string>/path/to/scripts/aci_sync.py</string>
    <string>--client</string><string>opencode</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>ACI_API_TOKEN</key><string>&lt;bearer&gt;</string>
  </dict>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>/tmp/aci-sync.log</string>
  <key>StandardErrorPath</key><string>/tmp/aci-sync.log</string>
</dict></plist>
```

(`launchctl load ~/Library/LaunchAgents/com.hien.aci-sync.plist` — người
dùng tự quyết định có cài hay không; tool không bao giờ tự cài.)

## Gotchas vận hành

- **OpenCode cache catalog**: sau khi sync vào `~/.config/opencode/skills/`
  hay `.opencode/skills/`, cần **restart opencode service** để nó quét lại
  skill mới (bài học 2026-09-28 trong AGENTS.md).
- Goose/Antigravity có thể cache danh sách skill trong session đang chạy —
  mở session mới sau khi sync.
- Tên skill phải khớp rule của client (`^[a-z0-9]+(-[a-z0-9]+)*$`, trùng tên
  dir) thì mới được client phát hiện; tên vi phạm vẫn được sync nhưng có
  cảnh báo.
- Tool không cần DB, không cần quyền gì trên server ngoài catalog (đã có
  `ACI_API_TOKEN` nếu server bật).
