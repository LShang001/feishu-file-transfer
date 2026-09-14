# feishu-file-transfer

A tiny, dependency-free CLI + agent skill that sends files/images to Feishu/Lark chats and downloads message attachments — using **your own** self-built app credentials.

一个零依赖的飞书文件传输工具与 Agent 技能（SKILL.md 格式）：用你自己的飞书自建应用发文件、发图片、下载消息附件。

## Features

- Send any file (PDF/Office/video/…) as a Feishu file message
- Send images as image messages (auto-detected, `--as-file` to override)
- Download attachments from any message the bot can see (original filename preserved)
- `whoami` self-check for credentials, scopes and bot status
- Recipients: `ou_…` user open_id · `oc_…` group chat_id · email (optional scope)
- **Python stdlib only** — no pip install, works anywhere Python 3.7+ runs
- Feishu (open.feishu.cn) and Lark (open.larksuite.com) domains

## Install

### As an agent skill

```bash
# via the skills CLI (reads SKILL.md at repo root)
npx skills add LShang001/feishu-file-transfer

# or manually clone into your agent skills directory
git clone https://github.com/LShang001/feishu-file-transfer \
  ~/.agents/skills/feishu-file-transfer        # works for ZCode / Codex / Cursor / etc.
```

### Standalone CLI

Just copy `scripts/feishu_send_file.py` anywhere — it has no dependencies.

## Setup (bring your own app)

1. Create a **self-built app** at [open.feishu.cn](https://open.feishu.cn) (Lark: [open.larksuite.com](https://open.larksuite.com))
2. Add the **Bot** capability
3. Add scopes: `im:message`, `im:message:send_as_bot`, `im:resource`
   (optional, for `--to <email>`: `contact:user.id:readonly`)
4. Publish the app version; copy **App ID / App Secret**
5. Configure credentials — environment variables or `~/.feishu-file-transfer.json`:

```json
{ "app_id": "cli_xxx", "app_secret": "xxx", "default_receive_id": "ou_xxx", "domain": "feishu" }
```

## Usage

```bash
python scripts/feishu_send_file.py whoami
python scripts/feishu_send_file.py send report.pdf --to ou_xxxxxxxx
python scripts/feishu_send_file.py send photo.png  --to oc_xxxxxxxx
python scripts/feishu_send_file.py download om_xxxxxxxx --out ./downloads
```

## Notes / known gotchas

- **Download uses the key inside the message body**, not the key returned by upload — Feishu
  re-keys files on send; using the upload key gives `234003 File not in msg` (handled automatically).
- `content` in the send API must be a **JSON string**, not an object.
- `open_id` is **app-scoped**: the same person has different open_ids under different apps.
- Sending to a group requires the bot to be a member; sending to a user requires the user to be
  able to see the bot.
- Limits (per official docs): files ~30MB, images ~10MB.
- Common errors: `99991672` = missing scope or unpublished version; `230002` = bot not in chat;
  `234003` = wrong file key.

## 中文说明

用你自己的飞书自建应用（App ID + App Secret）收发文件：

- **发送**：`send <文件>`，收件人支持 `ou_…`（个人 open_id）、`oc_…`（群 chat_id）、邮箱（需
  额外 `contact:user.id:readonly` 权限）；图片自动按图片消息发送
- **下载**：`download <message_id>`，自动取消息体内 key 并保留原文件名
- **自检**：`whoami` 验证凭证、权限与机器人状态
- 安装到 Agent 技能目录后，直接对 AI 说"把这个文件发我飞书"即可
- 仅用 Python 标准库；凭证只存本机（环境变量或 `~/.feishu-file-transfer.json`），勿提交到仓库

## License

MIT
