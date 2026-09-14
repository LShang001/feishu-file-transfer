#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Feishu / Lark file transfer CLI -- send files, images and text; download attachments.

Zero-config first run:
    feishu_send_file.py init

Commands:
    init [--app-id X] [--app-secret Y] [--to ou_...|email] [--save]
        detect + verify credentials; --save persists them to the config file
    whoami                          show bot status and the credential source in use
    send <file> [--to ...] [--as-file]
        send a file; images (png/jpg/...) are sent as image messages by default
    text "<content>" [--to ...]     send a text message
    download <message_id> [--out <dir>]
        save an attachment from a message (original filename preserved)

Credential resolution (first hit wins):
    1. --app-id / --app-secret flags
    2. FEISHU_APP_ID / FEISHU_APP_SECRET  (+ FEISHU_DEFAULT_RECEIVE_ID, FEISHU_DOMAIN=feishu|lark)
    3. config file ~/.feishu-file-transfer.json  (override path with FEISHU_FT_CONFIG)
    4. auto-discovery of local agent credentials (no manual setup needed):
       - ZCode desktop bot: ~/.zcode/v2/  (AES-256-GCM, decrypted in memory; needs `cryptography`)
       - OpenClaw-family agents: ~/.openclaw-autoclaw/openclaw.json  (plain JSON)

Bring-your-own-app setup: create a self-built app at https://open.feishu.cn
(Lark: https://open.larksuite.com), enable the Bot capability, add scopes
im:message / im:message:send_as_bot / im:resource (plus contact:user.id:readonly
if you want email recipients), publish the version, then run `init --save`.

Recipient ids: ou_ = user open_id, oc_ = group chat_id, otherwise an email address.
"""
import argparse
import base64
import getpass
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HOME = os.path.expanduser("~")
CONFIG_PATH = os.environ.get("FEISHU_FT_CONFIG") or os.path.join(HOME, ".feishu-file-transfer.json")
DOMAINS = {"feishu": "https://open.feishu.cn", "lark": "https://open.larksuite.com"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".ico"}
FILE_TYPE = {".pdf": "pdf", ".doc": "doc", ".docx": "doc", ".xls": "xls", ".xlsx": "xls",
             ".ppt": "ppt", ".pptx": "ppt", ".mp4": "mp4", ".opus": "opus"}
MIME_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
HINTS = {
    99991672: "应用缺少权限：到开放平台「权限管理」添加所需 scope 并重新发布版本",
    230002: "机器人不在该会话：先把机器人拉进群，或对个人使用 open_id 发送",
    234003: "file_key 不属于该消息：下载请用消息体内的 key（本脚本已自动处理）",
    230013: "参数构造错误：检查 receive_id / msg_type / content 组合",
}
# Local agent config locations used by auto-discovery
OPENCLAW_CONFIG = os.path.join(HOME, ".openclaw-autoclaw", "openclaw.json")
ZCODE_BOT_CONFIG = os.path.join(HOME, ".zcode", "v2", "bot-config.v3.json")
ZCODE_CREDENTIALS = os.path.join(HOME, ".zcode", "v2", "credentials.json")


def die(msg, code=1):
    print("ERROR: " + str(msg), file=sys.stderr)
    sys.exit(code)


def b64url(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ------------------------------------------------------------------ 凭证发现
def _aes_gcm_decrypt(enc, secret):
    """ZCode stores bot credentials as 'enc:v1:<iv>.<tag>.<ciphertext>' (base64url, AES-256-GCM)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv, tag, ct = (b64url(x) for x in enc[len("enc:v1:"):].split("."))
    return AESGCM(hashlib.sha256(secret.encode("utf-8")).digest()).decrypt(iv, ct + tag, None).decode("utf-8")


def discover_openclaw():
    """OpenClaw-family agents keep plaintext Feishu credentials in their config file."""
    if not os.path.isfile(OPENCLAW_CONFIG):
        return None, None
    try:
        fe = (json.load(open(OPENCLAW_CONFIG, encoding="utf-8")).get("channels") or {}).get("feishu") or {}
    except Exception as e:
        return None, "读取 %s 失败: %s" % (OPENCLAW_CONFIG, e)
    if fe.get("appId") and fe.get("appSecret"):
        return {"app_id": fe["appId"], "app_secret": fe["appSecret"],
                "default_receive_id": (fe.get("allowFrom") or [None])[0],
                "domain": "feishu",
                "source": "本地 agent 配置 " + OPENCLAW_CONFIG}, None
    return None, None


def discover_zcode():
    """ZCode desktop bot: decrypt the stored credential in memory.

    The encryption secret is either the ZCODE_CREDENTIAL_SECRET environment
    variable or a per-machine fallback derived from platform + home dir +
    username (same derivation as ZCode itself uses). The plaintext never
    leaves this process and is never written anywhere.
    """
    if not (os.path.isfile(ZCODE_BOT_CONFIG) and os.path.isfile(ZCODE_CREDENTIALS)):
        return None, None
    try:
        bots = json.load(open(ZCODE_BOT_CONFIG, encoding="utf-8")).get("bots", [])
        creds = json.load(open(ZCODE_CREDENTIALS, encoding="utf-8"))
    except Exception as e:
        return None, "读取 ZCode 配置失败: %s" % e
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return None, "发现 ZCode 机器人配置，但缺少 cryptography 库无法解密（pip install cryptography）"
    env_secret = (os.environ.get("ZCODE_CREDENTIAL_SECRET") or "").strip()
    if env_secret:
        secrets = [env_secret]
    else:
        try:
            user = getpass.getuser()
        except Exception:
            user = "unknown"
        secrets = ["zcode-credential-fallback:%s:%s:%s"
                   % (sys.platform, os.path.normpath(HOME), user)]
    for bot in bots:
        app_id = bot.get("feishuAppId")
        enc = creds.get("bot:%s:credential" % bot.get("id"))
        if not (app_id and isinstance(enc, str) and enc.startswith("enc:v1:")):
            continue
        plain = None
        for s in secrets:
            try:
                plain = _aes_gcm_decrypt(enc, s)
                break
            except Exception:
                continue
        if not plain:
            continue
        return {"app_id": app_id, "app_secret": plain,
                "default_receive_id": bot.get("providerUserId"), "domain": "feishu",
                "source": "ZCode 机器人「%s」凭证（内存解密）" % (bot.get("name") or "?")}, None
    return None, "发现 ZCode 配置但未能解密机器人凭证（若曾设置自定义 ZCODE_CREDENTIAL_SECRET，请重新设置）"


def discover_local():
    """Try every known local source. Returns (creds_or_None, notes)."""
    notes = []
    c, note = discover_openclaw()
    if note:
        notes.append(note)
    if c:
        return c, notes
    c, note = discover_zcode()
    if note:
        notes.append(note)
    if c:
        return c, notes
    return None, notes


def resolve_credentials(app_id=None, app_secret=None):
    """Resolve credentials: flags > env > config file > auto-discovery. Returns (cfg, notes)."""
    notes = []
    cfg = {"source": None}
    if app_id:
        cfg["app_id"], cfg["source"] = app_id, "命令行参数"
    if app_secret:
        cfg["app_secret"], cfg["source"] = app_secret, cfg["source"] or "命令行参数"
    for env, key in (("FEISHU_APP_ID", "app_id"), ("FEISHU_APP_SECRET", "app_secret"),
                     ("FEISHU_DEFAULT_RECEIVE_ID", "default_receive_id"), ("FEISHU_DOMAIN", "domain")):
        if os.environ.get(env):
            if not cfg.get(key):
                cfg[key] = os.environ[env]
            if key == "app_id" and not cfg["source"]:
                cfg["source"] = "环境变量 " + env

    if os.path.isfile(CONFIG_PATH):
        try:
            f = json.load(open(CONFIG_PATH, encoding="utf-8"))
        except Exception as e:
            die("配置文件解析失败 %s: %s" % (CONFIG_PATH, e))
        for k in ("app_id", "app_secret", "default_receive_id", "domain"):
            if not cfg.get(k) and f.get(k):
                cfg[k] = f[k]
        if not cfg["source"] and f.get("app_id"):
            cfg["source"] = "配置文件 " + CONFIG_PATH

    has_id, has_secret = bool(cfg.get("app_id")), bool(cfg.get("app_secret"))
    if has_id != has_secret:
        die("凭证不完整：app_id 与 app_secret 需同时提供（当前只配置了 %s）"
            % ("app_id" if has_id else "app_secret"))
    if not (has_id and has_secret):
        c, notes = discover_local()
        if c:
            for k in ("default_receive_id", "domain"):
                if not cfg.get(k) and c.get(k):
                    cfg[k] = c[k]
            cfg["app_id"], cfg["app_secret"] = c["app_id"], c["app_secret"]
            cfg["source"] = "自动发现：" + c["source"]
    return cfg, notes


def save_config_file(cfg):
    data = {k: cfg[k] for k in ("app_id", "app_secret", "default_receive_id", "domain")
            if cfg.get(k)}
    d = os.path.dirname(CONFIG_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CONFIG_PATH)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except Exception:
        pass
    return CONFIG_PATH


def setup_guide(notes=()):
    lines = ["未找到可用的飞书应用凭证。两种方式任选：", ""]
    lines += ["  (%s)" % n for n in notes]
    if notes:
        lines.append("")
    lines += [
        "A) 零配置自动发现：确认本机已安装 ZCode 桌面端（或 OpenClaw 系 agent），再运行：",
        "   feishu_send_file.py init",
        "",
        "B) 使用你自己的飞书应用（约 3 分钟）：",
        "   1. 打开 https://open.feishu.cn 创建「企业自建应用」，添加「机器人」能力",
        "   2. 权限管理添加：im:message、im:message:send_as_bot、im:resource",
        "      （要用邮箱收件人再加 contact:user.id:readonly）",
        "   3. 发布版本，复制 App ID 与 App Secret",
        "   4. 运行： feishu_send_file.py init --app-id cli_xxx --app-secret xxx --save",
        "      （或设置环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET）",
    ]
    return "\n".join(lines)


def base_url(cfg):
    return DOMAINS.get(str(cfg.get("domain", "feishu")).lower(), DOMAINS["feishu"])


# ------------------------------------------------------------------ HTTP
def api(method, path, base, token=None, data=None, raw=None, headers=None, params=""):
    """JSON request with 2 retries on transport errors. Returns the parsed response dict."""
    url = base + path + params
    last = None
    for attempt in range(3):
        h = dict(headers or {})
        if token:
            h["Authorization"] = "Bearer " + token
        body = None
        if raw is not None:
            body = raw
        elif data is not None:
            body = json.dumps(data).encode("utf-8")
            h.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=body, headers=h, method=method)
        try:
            return json.loads(urllib.request.urlopen(req, timeout=120).read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                return json.loads(payload.decode("utf-8"))
            except Exception:
                die("HTTP %s: %s" % (e.code, payload[:200]))
        except Exception as e:  # transient network / SSL
            last = "%s: %s" % (type(e).__name__, e)
            if attempt < 2:
                time.sleep(2)
    die("网络错误（已重试 2 次）: " + last)


def api_binary(path, base, token):
    """Binary download (message resources). Returns (bytes, headers)."""
    req = urllib.request.Request(base + path, headers={"Authorization": "Bearer " + token})
    last = None
    for attempt in range(3):
        try:
            r = urllib.request.urlopen(req, timeout=120)
            return r.read(), r.headers
        except urllib.error.HTTPError as e:
            payload = e.read()
            die("下载失败 HTTP %s: %s" % (e.code, payload[:200]))
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
            if attempt < 2:
                time.sleep(2)
    die("下载网络错误（已重试 2 次）: " + last)


def expect(resp, extra=""):
    if not isinstance(resp, dict) or resp.get("code") != 0:
        code = resp.get("code") if isinstance(resp, dict) else "?"
        msg = resp.get("msg") if isinstance(resp, dict) else str(resp)
        hint = HINTS.get(code, "")
        die("飞书返回错误 code=%s msg=%s %s %s" % (code, msg, hint, extra))
    return resp


def get_token(cfg):
    base = base_url(cfg)
    if not cfg.get("app_id") or not cfg.get("app_secret"):
        die(setup_guide())
    r = api("POST", "/open-apis/auth/v3/tenant_access_token/internal", base,
            data={"app_id": cfg["app_id"], "app_secret": cfg["app_secret"]})
    if r.get("code") != 0:
        die("获取 tenant_access_token 失败（检查 App ID/Secret 是否有效）: "
            + json.dumps(r, ensure_ascii=False)[:200])
    return r["tenant_access_token"]


# ------------------------------------------------------------------ helpers
def multipart(fields, file_field, filename, content, ctype="application/octet-stream"):
    boundary = "----FeishuFT" + os.urandom(8).hex()
    safe_name = filename.replace('"', "_").replace("\r", "").replace("\n", "")
    out = b""
    for k, v in fields:
        out += ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                % (boundary, k, v)).encode("utf-8")
    out += ('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
            'Content-Type: %s\r\n\r\n' % (boundary, file_field, safe_name, ctype)).encode("utf-8")
    out += content + ("\r\n--%s--\r\n" % boundary).encode("utf-8")
    return out, "multipart/form-data; boundary=" + boundary


def resolve_receive(to, base, token):
    if to.startswith("oc_"):
        return to, "chat_id"
    if to.startswith("ou_"):
        return to, "open_id"
    if "@" in to:
        r = expect(api("POST", "/open-apis/contact/v3/users/batch_get_id", base, token,
                       data={"emails": [to]}, params="?user_id_type=open_id"),
                   extra="（邮箱查 open_id 需要 contact:user.id:readonly 权限；也可直接使用 ou_/oc_）")
        users = r.get("data", {}).get("user_list", [])
        if users and users[0].get("user_id"):
            return users[0]["user_id"], "open_id"
        die("未找到邮箱 %s 对应的用户（或该用户不在应用可见范围内）" % to)
    die("无法识别的收件人 %r：请用 open_id（ou_…）、chat_id（oc_…）或邮箱" % to)


def sanitize_filename(name):
    name = os.path.basename(str(name)).strip() or "download"
    for c in '\\/:*?"<>|\r\n\t':
        name = name.replace(c, "_")
    return name


# ------------------------------------------------------------------ commands
def cmd_init(cfg, notes, save=False, to=None):
    if not (cfg.get("app_id") and cfg.get("app_secret")):
        print(setup_guide(notes))
        sys.exit(1)
    base = base_url(cfg)
    token = get_token(cfg)
    bot = expect(api("GET", "/open-apis/bot/v3/info", base, token)).get("bot", {})
    print("OK 凭证可用")
    print("  来源: %s" % cfg.get("source"))
    print("  应用: %s  (activate_status=%s)" % (bot.get("app_name"), bot.get("activate_status")))
    if to:
        rid, rtype = resolve_receive(to, base, token)
        cfg["default_receive_id"] = rid
        print("  收件人: %s:%s（将保存为缺省）" % (rtype, rid))
    print("  缺省收件人: %s" % (cfg.get("default_receive_id") or "（未设置，发送时请用 --to）"))
    if save:
        if (cfg.get("source") or "").startswith("配置文件"):
            print("  配置已存在: %s" % CONFIG_PATH)
        else:
            print("  已保存配置: %s（含 app_secret 明文，请勿提交到仓库）" % save_config_file(cfg))
    for n in notes:
        print("  提示: %s" % n)
    print("\n下一步: feishu_send_file.py send <文件路径>")


def cmd_whoami(cfg):
    base = base_url(cfg)
    token = get_token(cfg)
    bot = expect(api("GET", "/open-apis/bot/v3/info", base, token)).get("bot", {})
    print("应用名称: %s" % bot.get("app_name"))
    print("机器人 open_id: %s" % bot.get("open_id"))
    print("激活状态: activate_status=%s" % bot.get("activate_status"))
    print("凭证来源: %s" % cfg.get("source"))
    print("缺省收件人: %s" % (cfg.get("default_receive_id") or "（未配置）"))
    print("域名: %s" % base)


def cmd_send(cfg, path, to, as_file):
    if not os.path.isfile(path):
        die("文件不存在: " + path)
    base = base_url(cfg)
    token = get_token(cfg)
    to = to or cfg.get("default_receive_id")
    if not to:
        die("未指定收件人：用 --to，或配置 default_receive_id")
    rid, rtype = resolve_receive(to, base, token)

    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    with open(path, "rb") as f:
        data = f.read()

    if ext in IMAGE_EXT and not as_file:
        body, ctype = multipart([("image_type", "message")], "image", name, data)
        r = expect(api("POST", "/open-apis/im/v1/images", base, token, raw=body,
                       headers={"Content-Type": ctype}))
        content, msg_type = {"image_key": r["data"]["image_key"]}, "image"
    else:
        ftype = FILE_TYPE.get(ext, "stream")
        body, ctype = multipart([("file_type", ftype), ("file_name", name)], "file", name, data)
        r = expect(api("POST", "/open-apis/im/v1/files", base, token, raw=body,
                       headers={"Content-Type": ctype}))
        content, msg_type = {"file_key": r["data"]["file_key"]}, "file"

    r = expect(api("POST", "/open-apis/im/v1/messages", base, token,
                   data={"receive_id": rid, "msg_type": msg_type,
                         "content": json.dumps(content)},
                   params="?receive_id_type=" + rtype))
    print("OK 已发送: %s (%s) -> %s:%s  message_id=%s"
          % (name, msg_type, rtype, rid, r["data"]["message_id"]))


def cmd_text(cfg, text, to):
    base = base_url(cfg)
    token = get_token(cfg)
    to = to or cfg.get("default_receive_id")
    if not to:
        die("未指定收件人：用 --to，或配置 default_receive_id")
    rid, rtype = resolve_receive(to, base, token)
    r = expect(api("POST", "/open-apis/im/v1/messages", base, token,
                   data={"receive_id": rid, "msg_type": "text",
                         "content": json.dumps({"text": text})},
                   params="?receive_id_type=" + rtype))
    print("OK 已发送文本 -> %s:%s  message_id=%s" % (rtype, rid, r["data"]["message_id"]))


def cmd_download(cfg, message_id, out_dir):
    base = base_url(cfg)
    token = get_token(cfg)
    r = expect(api("GET", "/open-apis/im/v1/messages/" + message_id, base, token))
    items = r.get("data", {}).get("items", [])
    if not items:
        die("消息不存在或机器人不可见: " + message_id)
    item = items[0]
    body = json.loads(item["body"]["content"])
    if "file_key" in body:
        key, kind = body["file_key"], "file"
        name = sanitize_filename(body.get("file_name") or "%s_%s" % (message_id, key[-8:]))
    elif "image_key" in body:
        key, kind, name = body["image_key"], "image", None
    else:
        die("该消息（msg_type=%s）没有可下载的附件" % item.get("msg_type"))

    blob, headers = api_binary("/open-apis/im/v1/messages/%s/resources/%s?type=%s"
                               % (message_id, key, kind), base, token)
    if kind == "image":
        ct = (headers.get("Content-Type") or "").split(";")[0].strip()
        name = "%s_%s%s" % (message_id, key[-8:], MIME_EXT.get(ct, ".png"))
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, name)
    with open(dest, "wb") as f:
        f.write(blob)
    print("OK 已下载: %s (%d bytes)" % (dest, len(blob)))


# ------------------------------------------------------------------ CLI
def main():
    argv = sys.argv[1:]
    known = {"init", "whoami", "send", "text", "download"}
    # legacy shorthand: first arg is an existing file -> implicit "send"
    if argv and argv[0] not in known and not argv[0].startswith("-") and os.path.isfile(argv[0]):
        argv = ["send"] + argv

    p = argparse.ArgumentParser(description="Feishu/Lark file transfer (stdlib only, zero-config)")
    p.add_argument("--app-id", help="override app id (also accepted after 'init')")
    p.add_argument("--app-secret", help="override app secret (also accepted after 'init')")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init", help="初始化：检测/验证凭证，--save 持久化配置")
    pi.add_argument("--app-id", dest="app_id", default=argparse.SUPPRESS)
    pi.add_argument("--app-secret", dest="app_secret", default=argparse.SUPPRESS)
    pi.add_argument("--to", help="可选：验证并保存缺省收件人（ou_… / 邮箱）")
    pi.add_argument("--save", action="store_true", help="把生效凭证写入配置文件")

    sub.add_parser("whoami", help="检查凭证、权限与机器人状态")

    ps = sub.add_parser("send", help="发送文件或图片")
    ps.add_argument("file")
    ps.add_argument("--to", help="ou_… / oc_… / email（缺省用 default_receive_id）")
    ps.add_argument("--as-file", action="store_true", help="图片也按文件卡片发送")

    pt = sub.add_parser("text", help="发送文本消息")
    pt.add_argument("content")
    pt.add_argument("--to", help="ou_… / oc_… / email（缺省用 default_receive_id）")

    pd = sub.add_parser("download", help="下载消息中的附件")
    pd.add_argument("message_id")
    pd.add_argument("--out", default=".", help="输出目录（缺省当前目录）")

    args = p.parse_args(argv)
    cfg, notes = resolve_credentials(getattr(args, "app_id", None), getattr(args, "app_secret", None))

    if args.cmd == "init":
        cmd_init(cfg, notes, save=args.save, to=args.to)
    elif args.cmd == "whoami":
        cmd_whoami(cfg)
    elif args.cmd == "send":
        cmd_send(cfg, args.file, args.to, args.as_file)
    elif args.cmd == "text":
        cmd_text(cfg, args.content, args.to)
    else:
        cmd_download(cfg, args.message_id, args.out)


if __name__ == "__main__":
    main()
