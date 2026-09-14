---
name: feishu-file-transfer
description: >
  用你自己的飞书自建应用收发文件——把本地文件/图片发送到飞书会话（支持 open_id、chat_id、
  邮箱三种收件人），从飞书消息下载附件，并自检应用配置。零第三方依赖（Python 标准库）。
  触发：发文件到飞书、传文件给我、把报告/PDF 发飞书、下载飞书消息里的文件、飞书机器人发消息、
  send file to Feishu、download Feishu attachment、Lark bot send file。
  不适用于：读写飞书云文档/表格/wiki（需另申请对应权限）。
  Requires a self-built Feishu/Lark app (App ID + App Secret) with scopes
  im:message / im:message:send_as_bot / im:resource.
version: 1.0.0
license: MIT
metadata:
  author: LShang001
  date: 2026-09-14
compatibility: Python 3.7+（仅标准库），Windows / macOS / Linux
---

# 飞书文件传输（Feishu / Lark File Transfer）

## When to Use

- 把本机文件（PDF、Office、视频、任意格式）或图片发送到飞书会话
- 从飞书消息里下载附件（给出 message_id 即可，自动识别文件/图片）
- 快速自检飞书机器人应用配置是否可用（token、权限、机器人状态）
- 不确定路径/权限时，先跑 `whoami` 再发

## Prerequisites

一个你自己的飞书**自建应用**（其他人需要用自己申请的应用，App Secret 是账号私密凭证）：

1. 打开开放平台：国内版 https://open.feishu.cn ，国际版（Lark）https://open.larksuite.com ，
   开发者后台 → 创建「企业自建应用」
2. 「添加应用能力」→ 启用**机器人**
3. 「权限管理」添加 scope（发文件/图片必需）：
   - `im:message`、`im:message:send_as_bot`、`im:resource`
   - 可选：要用 `--to <邮箱>` 时再加 `contact:user.id:readonly`
4. 发布应用版本（企业内通常自行审批通过即可）
5. 在「凭证与基础信息」获取 **App ID**（`cli_` 开头）与 **App Secret**

## Setup

凭证优先级：命令行参数 > 环境变量 > 配置文件。

环境变量：

```bash
export FEISHU_APP_ID=cli_xxxxxxxx
export FEISHU_APP_SECRET=xxxxxxxx
export FEISHU_DEFAULT_RECEIVE_ID=ou_xxxxxxxx   # 可选，缺省收件人
```

或配置文件 `~/.feishu-file-transfer.json`：

```json
{
  "app_id": "cli_xxxxxxxx",
  "app_secret": "xxxxxxxx",
  "default_receive_id": "ou_xxxxxxxx",
  "domain": "feishu"
}
```

`domain` 取值 `feishu`（默认，open.feishu.cn）或 `lark`（open.larksuite.com）。

## Procedure

脚本位置：`scripts/feishu_send_file.py`（仅标准库，可直接拷走使用）。

```bash
# 1) 自检：验证凭证、权限与机器人状态
python scripts/feishu_send_file.py whoami

# 2) 发送文件 / 图片（图片自动按图片消息发；--as-file 可改为文件卡片）
python scripts/feishu_send_file.py send report.pdf --to ou_xxxxxxxx
python scripts/feishu_send_file.py send photo.png  --to oc_xxxxxxxx       # 群（机器人须在群内）
python scripts/feishu_send_file.py send a.xlsx     --to someone@corp.com  # 邮箱（需 contact 权限）

# 3) 下载消息里的附件
python scripts/feishu_send_file.py download om_xxxxxxxx --out ./downloads
```

内部流程（脚本已封装，便于排障时手工复现）：

```
tenant_access_token ← POST /open-apis/auth/v3/tenant_access_token/internal
上传文件            ← POST /open-apis/im/v1/files   （file_type 按扩展名映射）
上传图片            ← POST /open-apis/im/v1/images  （image_type=message）
发送消息            ← POST /open-apis/im/v1/messages?receive_id_type=open_id|chat_id
                        content 为 JSON 字符串：{"file_key": "…"} / {"image_key": "…"}
读取消息            ← GET  /open-apis/im/v1/messages/{message_id}
下载附件            ← GET  /open-apis/im/v1/messages/{message_id}/resources/{key}?type=file|image
```

## Verification

1. `whoami` 返回应用名称与 `activate_status=2`（已启用），说明凭证与网络通
2. 给自己发一个小文件：`send test.md --to ou_<你的open_id>`，确认飞书收到
3. 下载刚发出的消息：用上一步输出的 message_id 执行 `download`，比对文件一致

## Quick Reference

- **收件人格式**：`ou_…` = 用户 open_id；`oc_…` = 群 chat_id；含 `@` = 邮箱（需 `contact:user.id:readonly`）
- **file_type 映射**：pdf / doc / docx / xls / xlsx / ppt / pptx / mp4 / opus，其余用 `stream`
- **图片判定**：png/jpg/jpeg/webp/gif/bmp/ico 自动按图片消息发送，其他按文件卡片
- **限额**（官方口径，以文档为准）：文件约 ≤30MB、图片 ≤10MB；超限会返回明确错误
- **错误码**：`99991672` 应用无该权限（scope 未开或版本未发布）；`230002` 机器人不在会话
  （群未拉机器人/用户不在可见范围）；`234003` file_key 不属于该消息（下载用错 key）
- **token 有效期**：约 50 分钟，脚本每次运行重新获取

## Pitfalls

- **下载必须用"消息体内"的 key**：上传返回的 file_key 与消息里实际存的 key 不同（服务端会
  重新编钥），直接下载上传 key 会报 `234003`。脚本自动从消息体取 key
- **open_id 是应用级隔离的**：同一个人在不同应用里 open_id 不同，换应用必须换 ID
- **content 必须是 JSON 字符串**（不是对象），否则接口报参数错误
- **给个人发消息**要求该用户对机器人可见（企业内一般可见；失败时让用户先给机器人发一条消息）；
  给群发要求机器人已在群内
- **网络抖动**：偶发 SSL/连接中断，脚本内置 2 次重试；仍失败检查代理/防火墙
- **不要把 App Secret 提交进仓库**：配置文件放家目录、环境变量或密钥管理器

## Security

- App Secret 等同账号凭证：只写本机配置，不要贴进代码、日志、提交信息
- 脚本只读取凭证用于换取 tenant_access_token，不打印、不落盘任何密钥
- 只发送你拥有权限的文件；机器人只能作用于它被授权的租户范围
