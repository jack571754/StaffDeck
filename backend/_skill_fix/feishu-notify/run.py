# -*- coding: utf-8 -*-
"""
飞书单聊通知 (feishu-notify/run.py)

职责：向指定飞书用户发送一条单聊文本消息。
通道：飞书开放接口纯 HTTP —— POST /auth/v3/tenant_access_token/internal 取 token，
     POST /im/v1/messages?receive_id_type=open_id 发文本消息。
凭证：与平台内置飞书 ChannelBinding 同源（自建应用 App ID + App Secret），
     经 --app-id/--app-secret 或环境变量 FEISHU_APP_ID/FEISHU_APP_SECRET 注入，不明文入库。

前置缺失（无 Binding/无 open_id）时输出结构化错误并说明缺什么，不做静默降级。
"""

import os
import sys

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import argparse
import json
import sys

try:
    import requests
except Exception as exc:  # pragma: no cover
    print(json.dumps({"status": "error", "message": f"运行环境缺少 requests 依赖: {exc}"}, ensure_ascii=False))
    sys.exit(0)

FEISHU_BASE = os.getenv("FEISHU_BASE", "https://open.feishu.cn")
MAX_TEXT_LEN = 9000  # 平台 CHANNEL_TEXT_LIMIT=2000 的三倍余量内做截断，飞书文本上限约 150KB，此处保守控制


def _fail(message: str, **extra) -> None:
    print(json.dumps({"status": "error", "message": message, **extra}, ensure_ascii=False))


def _read_stdin_payload() -> dict:
    """StaffDeck runner 会把 stdin_json 传进来（QUERY/ARGUMENTS/text 等），兼容读取。"""
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        if raw.strip():
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, str):
                return {"text": parsed}
    except Exception:
        pass
    return {}


def get_tenant_access_token(app_id: str, app_secret: str) -> tuple[str, str]:
    """返回 (token, 错误信息)。"""
    try:
        resp = requests.post(
            f"{FEISHU_BASE}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=15,
        )
    except Exception as exc:
        return "", f"获取 tenant_access_token 请求失败: {exc}"
    try:
        data = resp.json()
    except Exception:
        return "", f"tenant_access_token 响应非 JSON(HTTP {resp.status_code}): {resp.text[:200]}"
    # token 接口 code=0 表示成功；其余为业务错误（如 app_id 不存在/secret 错误 10003/10001）
    if data.get("code") == 0 and data.get("tenant_access_token"):
        return str(data["tenant_access_token"]), ""
    return "", f"获取 tenant_access_token 失败: code={data.get('code')} msg={data.get('msg')}"


def send_text(token: str, open_id: str, text: str) -> tuple[bool, str, dict]:
    """发送文本单聊，返回 (ok, 说明, 响应摘要)。"""
    try:
        resp = requests.post(
            f"{FEISHU_BASE}/open-apis/im/v1/messages",
            params={"receive_id_type": "open_id"},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
            json={"receive_id": open_id, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
            timeout=15,
        )
    except Exception as exc:
        return False, f"发送消息请求失败: {exc}", {}
    try:
        data = resp.json()
    except Exception:
        return False, f"发送消息响应非 JSON(HTTP {resp.status_code}): {resp.text[:200]}", {}
    code = data.get("code")
    if code == 0:
        return True, "发送成功", {"message_id": (data.get("data") or {}).get("message_id", "")}
    # 常见错误：open_id 无效(230001)、应用无 im:message 权限(99991672)、机器人未开通能力
    return False, f"发送失败: code={code} msg={data.get('msg')}", {"data": data.get("data") or {}}


def main() -> None:
    parser = argparse.ArgumentParser(description="飞书单聊文本通知")
    parser.add_argument("--app-id", default=os.getenv("FEISHU_APP_ID", ""))
    parser.add_argument("--app-secret", default=os.getenv("FEISHU_APP_SECRET", ""))
    parser.add_argument("--open-id", default=os.getenv("FEISHU_OPEN_ID", ""))
    parser.add_argument("--text", default="")
    parser.add_argument("--text-file", default="", help="从文件读取正文（长文本/含特殊字符时用）")
    args = parser.parse_args()

    stdin_payload = _read_stdin_payload()

    app_id = args.app_id or str(stdin_payload.get("app_id") or "")
    app_secret = args.app_secret or str(stdin_payload.get("app_secret") or "")
    open_id = args.open_id or str(stdin_payload.get("open_id") or "")

    text = args.text
    if not text and args.text_file:
        try:
            with open(args.text_file, "r", encoding="utf-8") as fh:
                text = fh.read()
        except Exception as exc:
            _fail(f"读取 --text-file 失败: {exc}")
            return
    if not text:
        text = str(stdin_payload.get("text") or stdin_payload.get("QUERY") or stdin_payload.get("ARGUMENTS") or "")
    text = text.strip()
    if len(text) > MAX_TEXT_LEN:
        text = text[:MAX_TEXT_LEN] + "\n…(内容过长已截断)"

    # ---- 前置校验：缺什么明确说什么，不做静默降级 ----
    missing = [
        name for name, val in (("FEISHU_APP_ID", app_id), ("FEISHU_APP_SECRET", app_secret), ("FEISHU_OPEN_ID", open_id))
        if not val
    ]
    if missing:
        _fail(
            "缺少飞书发送前置条件: " + ", ".join(missing)
            + "。平台需先配置 active 的飞书自建应用 ChannelBinding（App ID+Secret+长连接），"
            "并建立接收人 open_id 与 StaffDeck 用户的关联（channel_identity）；"
            "本次以 --app-id/--app-secret/--open-id 或对应环境变量注入后重试。",
            missing=missing,
        )
        return
    if not text:
        _fail("消息内容为空：请通过 --text/--text-file 或 stdin text 字段提供正文。")
        return

    token, err = get_tenant_access_token(app_id, app_secret)
    if not token:
        _fail(err)
        return

    ok, msg, extra = send_text(token, open_id, text)
    if ok:
        print(json.dumps({"status": "success", "message": msg, **extra}, ensure_ascii=False))
    else:
        _fail(msg, **extra)


if __name__ == "__main__":
    main()
