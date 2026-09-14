#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Feishu / Lark file transfer CLI -- send files or images to a chat, download attachments.

Zero third-party dependencies (Python stdlib only).

Setup (bring your own app):
  1) Create a self-built app at https://open.feishu.cn (Lark: https://open.larksuite.com)
     - enable the "Bot" capability
     - add scopes: im:message, im:message:send_as_bot, im:resource
     - optional (for --to <email>): contact:user.id:readonly
  2) Publish the app version, collect App ID / App Secret
  3) Configure credentials (priority: CLI flags > environment > config file)

Environment:
  FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_DEFAULT_RECEIVE_ID, FEISHU_DOMAIN (feishu|lark)

Config file: ~/.feishu-file-transfer.json
  {"app_id": "cli_xxx", "app_secret": "xxx", "default_receive_id": "ou_xxx", "domain": "feishu"}

Usage:
  feishu_send_file.py whoami
  feishu_send_file.py send <file> [--to <ou_...|oc_...|email>] [--as-file]
  feishu_send_file.py download <message_id> [--out <dir>]

Recipient id prefixes: ou_ = user open_id, oc_ = group chat_id, otherwise an email.
"""
import argparse
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

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".feishu-file-transfer.json")
DOMAINS = {"feishu": "https://open.feishu.cn", "lark": "https://open.larksuite.com"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".ico"}
FILE_TYPE = {".pdf": "pdf", ".doc": "doc", ".docx": "doc", ".xls": "xls", ".xlsx": "xls",
             ".ppt": "ppt", ".pptx": "ppt", ".mp4": "mp4", ".opus": "opus"}
MIME_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
HINTS = {
    99991672: "应用缺少权限：到开放平台「权限管理」添加所需 scope 并重新发布版本",
    230002: "机器人不在该会话：先把机器人拉进群，或对个人使用 open_id 发送",
    234003: "file_key 不属于该消息：下载请用消息体内的 key（本脚本已自动处理）",
}


def die(msg, code=1):
    print("ERROR: " + str(msg), file=sys.stderr)
    sys.exit(code)


def load_config():
    cfg = {}
    if os.path.isfile(CONFIG_PATH):
        try:
            cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
        except Exception as e:
            die("配置文件解析失败 %s: %s" % (CONFIG_PATH, e))
    for env, key in (("FEISHU_APP_ID", "app_id"), ("FEISHU_APP_SECRET", "app_secret"),
                     ("FEISHU_DEFAULT_RECEIVE_ID", "default_receive_id"), ("FEISHU_DOMAIN", "domain")):
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    return cfg


def base_url(cfg):
    return DOMAINS.get(str(cfg.get("domain", "feishu")).lower(), DOMAINS["feishu"])


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


def get_token(cfg, base):
    if not cfg.get("app_id") or not cfg.get("app_secret"):
        die("缺少 app_id/app_secret：设置环境变量 FEISHU_APP_ID/FEISHU_APP_SECRET，"
            "或写入配置文件 " + CONFIG_PATH)
    r = api("POST", "/open-apis/auth/v3/tenant_access_token/internal", base,
            data={"app_id": cfg["app_id"], "app_secret": cfg["app_secret"]})
    if r.get("code") != 0:
        die("获取 tenant_access_token 失败（检查 App ID/Secret）: " + json.dumps(r, ensure_ascii=False)[:200])
    return r["tenant_access_token"]


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


def cmd_whoami(cfg):
    base = base_url(cfg)
    token = get_token(cfg, base)
    r = expect(api("GET", "/open-apis/bot/v3/info", base, token))
    bot = r.get("bot", {})
    print("应用名称: %s" % bot.get("app_name"))
    print("机器人 open_id: %s" % bot.get("open_id"))
    print("激活状态: activate_status=%s" % bot.get("activate_status"))
    print("默认收件人: %s" % (cfg.get("default_receive_id") or "（未配置）"))
    print("域名: %s" % base)


def cmd_send(cfg, path, to, as_file):
    if not os.path.isfile(path):
        die("文件不存在: " + path)
    base = base_url(cfg)
    token = get_token(cfg, base)
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


def cmd_download(cfg, message_id, out_dir):
    base = base_url(cfg)
    token = get_token(cfg, base)
    r = expect(api("GET", "/open-apis/im/v1/messages/" + message_id, base, token))
    items = r.get("data", {}).get("items", [])
    if not items:
        die("消息不存在或机器人不可见: " + message_id)
    item = items[0]
    body = json.loads(item["body"]["content"])
    ct_ext = ".png"
    if "file_key" in body:
        key, kind = body["file_key"], "file"
        name = body.get("file_name") or "%s_%s" % (message_id, key[-8:])
    elif "image_key" in body:
        key, kind = body["image_key"], "image"
        name = "%s_%s%s" % (message_id, key[-8:], ct_ext)
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


def main():
    p = argparse.ArgumentParser(description="Feishu/Lark file transfer (stdlib only)")
    p.add_argument("--app-id"); p.add_argument("--app-secret")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("whoami", help="检查应用配置与机器人状态")

    ps = sub.add_parser("send", help="发送文件或图片")
    ps.add_argument("file")
    ps.add_argument("--to", help="ou_… / oc_… / email（缺省用 default_receive_id）")
    ps.add_argument("--as-file", action="store_true", help="图片也按文件卡片发送")

    pd = sub.add_parser("download", help="下载消息中的附件")
    pd.add_argument("message_id")
    pd.add_argument("--out", default=".", help="输出目录（缺省当前目录）")

    args = p.parse_args()
    cfg = load_config()
    if args.app_id:
        cfg["app_id"] = args.app_id
    if args.app_secret:
        cfg["app_secret"] = args.app_secret

    if args.cmd == "whoami":
        cmd_whoami(cfg)
    elif args.cmd == "send":
        cmd_send(cfg, args.file, args.to, args.as_file)
    else:
        cmd_download(cfg, args.message_id, args.out)


if __name__ == "__main__":
    main()
