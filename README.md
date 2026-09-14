# feishu-file-transfer

A tiny, dependency-free CLI + agent skill that sends files/images/text to Feishu/Lark chats and downloads message attachments. **Zero-config by default**: it auto-discovers Feishu credentials already present on the machine (ZCode desktop bot, OpenClaw-family agents) and falls back to your own app credentials when needed.

一个零依赖的飞书文件传输工具与 Agent 技能（SKILL.md 格式）：发文件、发图片、发文本、下载消息附件。**默认零配置开箱即用**——自动发现本机已有的飞书凭证（ZCode 桌面端机器人、OpenClaw 系 agent），也支持使用你自己的自建应用。

## Features

- **Zero-config first run**: `init` auto-detects local credentials, verifies them, and can persist them (`--save`)
- Send any file (PDF/Office/video/…) as a Feishu file message
- Send images as image messages (auto-detected, `--as-file` to override)
- Send text messages
- Download attachments from any message the bot can see (original filename preserved)
- `whoami` self-check showing which credential source is in use
- Recipients: `ou_…` user open_id · `oc_…` group chat_id · email (optional scope)
- **Python stdlib only** for everything except the optional ZCode credential decryption (`cryptography`)
- Feishu (open.feishu.cn) and Lark (open.larksuite.com) domains
- Automatic retry on transient network/SSL errors

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

Just copy `scripts/feishu_send_file.py` anywhere — it has no required dependencies.

## Setup

**Zero-config path (try this first):**

```bash
python scripts/feishu_send_file.py init
```

If a ZCode desktop install or an OpenClaw-family agent with Feishu credentials exists on the
machine, the script finds it automatically. ZCode credentials are stored encrypted; they are
decrypted in memory only (never written to disk, never printed) — this needs `pip install cryptography`.

**Bring-your-own-app path:**

1. Create a **self-built app** at [open.feishu.cn](https://open.feishu.cn) (Lark: [open.larksuite.com](https://open.larksuite.com))
2. Add the **Bot** capability
3. Add scopes: `im:message`, `im:message:send_as_bot`, `im:resource`
   (optional, for `--to <email>`: `contact:user.id:readonly`)
4. Publish the app version; copy **App ID / App Secret**

```bash
python scripts/feishu_send_file.py init --app-id cli_xxx --app-secret xxx --to ou_xxx --save
```

Credential resolution order: CLI flags > environment variables > config file > auto-discovery.

- Environment: `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, `FEISHU_DEFAULT_RECEIVE_ID`, `FEISHU_DOMAIN` (`feishu`|`lark`)
- Config file `~/.feishu-file-transfer.json` (override path with `FEISHU_FT_CONFIG`):

```json
{ "app_id": "cli_xxx", "app_secret": "xxx", "default_receive_id": "ou_xxx", "domain": "feishu" }
```

## Usage

```bash
python scripts/feishu_send_file.py init              # 初始化（检测+验证，--save 持久化）
python scripts/feishu_send_file.py whoami            # 查看凭证来源与机器人状态
python scripts/feishu_send_file.py send report.pdf --to ou_xxxxxxxx
python scripts/feishu_send_file.py send photo.png  --to oc_xxxxxxxx
python scripts/feishu_send_file.py text "build finished, artifacts attached"
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

**默认零配置**：`init` 自动发现本机已有的飞书凭证（ZCode 桌面端机器人 / OpenClaw 系 agent），
验证通过即可直接发送；没有可发现的凭证时，用你自己的飞书自建应用（App ID + App Secret）：

- **初始化**：`init`（检测+验证），`init --save` 持久化配置；`init --app-id … --app-secret … --to ou_… --save` 用自带应用初始化
- **发送**：`send <文件>`，收件人支持 `ou_…`（个人 open_id）、`oc_…`（群 chat_id）、邮箱（需
  额外 `contact:user.id:readonly` 权限）；图片自动按图片消息发送
- **文本**：`text "内容"`
- **下载**：`download <message_id>`，自动取消息体内 key 并保留原文件名
- **自检**：`whoami` 显示当前生效的凭证来源、权限与机器人状态
- 凭证优先级：命令行 > 环境变量 > 配置文件 > 自动发现；ZCode 凭证在内存中解密，明文不落盘不打印
- 安装到 Agent 技能目录后，直接对 AI 说"把这个文件发我飞书"即可

## License

MIT
