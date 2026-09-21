# -*- coding: utf-8 -*-
"""
致远 OA 待办审批巡检 (oa-todo-checker/run.py)

职责：登录致远 OA(Seeyon)，抓取当前待办审批列表，输出 JSON。
登录为纯 HTTP：复刻前端 CryptoJS.DES.encrypt(pwd, _SecuritySeed) 客户端加密，无需浏览器/验证码。

数据来源：致远 OA(oa.xajuzi.com)。凭据通过 --username/--password 或环境变量注入，不明文入库。
"""

import os
import sys

# 补齐 Windows 沙箱缺少的 USERNAME/LOGNAME，防止个别库/库初始化报 OSError
if not os.environ.get("USERNAME"):
    os.environ["USERNAME"] = os.environ.get("USER", "staffdeck")
if not os.environ.get("LOGNAME"):
    os.environ["LOGNAME"] = os.environ["USERNAME"]

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time

try:
    import requests
except Exception as exc:  # pragma: no cover
    print(json.dumps({"status": "error", "message": f"运行环境缺少 requests 依赖: {exc}"}, ensure_ascii=False))
    sys.exit(0)


def _log(*args) -> None:
    sys.stderr.write("[%s] " % time.strftime("%H:%M:%S") + " ".join(str(a) for a in args))
    sys.stderr.write("\n")


# ---------------------------------------------------------------------------
# CryptoJS.DES 兼容加密
# CryptoJS.DES.encrypt(utf8(pwd), passphrase) 使用 OpenSSL KDF(EVP_BytesToKey, MD5)
#   key=8B + iv=8B，输出 Salted__ 前缀 base64 (PKCS7 padding)
# ---------------------------------------------------------------------------
def _evp_bytes_to_key(password: bytes, salt: bytes, dklen: int) -> bytes:
    """OpenSSL EVP_BytesToKey(MD5, salt, iterations=1)。"""
    d = b""
    prev = b""
    while len(d) < dklen:
        prev = hashlib.md5(prev + password + salt).digest()
        d += prev
    return d[:dklen]


def _pkcs7_pad(data: bytes, block_size: int = 8) -> bytes:
    pad = block_size - (len(data) % block_size)
    return data + bytes([pad]) * pad


def _des_encrypt_salted(plaintext: str, seed: str) -> str:
    """等价 CryptoJS.DES.encrypt(pwd, seed).toString()。"""
    from Crypto.Cipher import DES

    password_bytes = plaintext.encode("utf-8")
    seed_bytes = seed.encode("utf-8")
    salt = os.urandom(8)
    derived = _evp_bytes_to_key(seed_bytes, salt, 16)  # key(8) + iv(8)
    key = derived[:8]
    iv = derived[8:16]
    cipher = DES.new(key, DES.MODE_CBC, iv)
    ct = cipher.encrypt(_pkcs7_pad(password_bytes, 8))
    return base64.b64encode(b"Salted__" + salt + ct).decode()


def _pwd_strength(pwd: str) -> str:
    """估算登录页 getPwdStrongForLoginPage 的强度等级(Seeyon: D/C/B/A)。"""
    l = len(pwd)
    classes = 0
    for c in pwd:
        if c.isdigit():
            classes |= 1
        elif c.isupper():
            classes |= 2
        elif c.islower():
            classes |= 4
        else:
            classes |= 8
    bits = bin(classes).count("1")
    if l >= 8 and bits >= 3:
        return "A"
    if l >= 6 and bits >= 2:
        return "B"
    if l >= 6:
        return "C"
    return "D"


def _extract_seed(html: str) -> str:
    m = re.search(r"_SecuritySeed\s*=\s*['\"]([^'\"]+)['\"]", html)
    return m.group(1).strip() if m else ""


def login(session: requests.Session, base: str, username: str, password: str) -> tuple[bool, str, str]:
    """登录返回 (ok, 提示, seed)。"""
    login_url = base + "/seeyon/index.jsp"
    try:
        r = session.get(login_url, timeout=20)
        html = r.text
    except Exception as exc:
        return False, f"获取登录页失败: {exc}", ""

    seed = _extract_seed(html)
    if not seed:
        # 若已登录会重定向到 index；探测是否已登录
        if "iframe" in html.lower() or "index.do" in html or "top.do" in html:
            return True, "已处于登录态(未取到明文字段)", ""
        return False, "未找到 _SecuritySeed（登录页结构变化或疑似验证码拦截）", ""

    enc_pwd = _des_encrypt_salted(password, seed)
    form = {
        "login_username": username,
        "login_password": enc_pwd,
        # 保持与前端一致的补充字段（可信空/弱校验即可）
        "login_password1": "",
        "login_validatePwdStrength": _pwd_strength(password),
        "random": "",
        "redirect_url": "",
        "login.timezone": "Etc/GMT-8",
        "signed_data": "",
        "authorization": "",
        "province": "",
        "city": "",
    }
    try:
        resp = session.post(
            base + "/seeyon/main.do?method=login",
            data=form,
            timeout=20,
            allow_redirects=False,
        )
    except Exception as exc:
        return False, f"登录请求失败: {exc}", seed

    # 成功登录通常 302 到首页（无 Location 域错误）；失败回写带错误提示的页面
    body = resp.text or ""
    if resp.status_code in (301, 302, 303, 307, 308):
        return True, f"登录成功(redirect {resp.status_code})", seed
    # 失败特征
    for err in ["账号或密码", "密码错误", "用户名", "无效", "fail", "error", "错误"]:
        if err in body:
            return False, f"登录失败：页面含'{err}'", seed
    # 登录成功后 main.do?method=login 也可能 200 直接渲染首页框架
    if "logout" in body.lower() or "main.do?method=index" in body or "personSpace" in body.lower():
        return True, "登录成功(200)", seed
    return False, f"登录结果未知(HTTP {resp.status_code})，页面片段: {body[:120]!r}", seed


def _candidate_todo_urls(base: str) -> list[str]:
    """在登录态下可尝试的待办数据源（需实测收敛）。"""
    return [
        "/seeyon/rest/todo/list",
        "/seeyon/rest/todos",
        "/seeyon/pendingApproval.do?method=list",
        "/seeyon/toDoMain.do?method=pending",
        "/seeyon/taskCenter.do?method=pendingTodo",
        "/seeyon/wf/process/list/pending",
    ]


def fetch_todos(session: requests.Session, base: str) -> tuple[list[dict], str]:
    """登录态下抓待办。返回 (items, 说明)。"""
    # 先看首页/门户是否能给到待办数据源线索
    probe_urls = _candidate_todo_urls(base)
    for url in probe_urls:
        try:
            r = session.get(base + url, timeout=15)
            ct = r.headers.get("content-type", "")
            data = r.text
            if r.status_code == 200 and len(data) > 40 and ("json" in ct or data.strip().startswith(("{", "["))):
                try:
                    parsed = r.json()
                    items = _extract_todos_from_json(parsed)
                    if items:
                        return items, f"命中 JSON 待办接口: {url}"
                except Exception:
                    continue
        except Exception:
            continue
    # 兜底：主门户页解析含"待办"关键词的条目
    try:
        r = session.get(base + "/seeyon/main.do?method=index", timeout=20)
        items = _extract_todos_from_html(r.text)
        if items:
            return items, "从门户首页 DOM 解析到待办"
    except Exception as exc:
        _log("首页解析失败:", exc)
    return [], "暂未定位到待办数据源"


def _extract_todos_from_json(parsed: object) -> list[dict]:
    results: list[dict] = []
    text = json.dumps(parsed, ensure_ascii=False)
    if not any(k in text for k in ["待办", "todo", "Todo", "pending", "Pending", "subject", "title"]):
        return results
    # 递归收集含 subject/title + url/messageId 的对象
    def walk(node):
        if isinstance(node, dict):
            if _looks_like_todo(node):
                results.append(_normalize_todo(node))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(parsed)
    return results


def _looks_like_todo(d: dict) -> bool:
    keys = set(d.keys())
    name = "".join(str(d.get(k) or "") for k in ("subject", "title", "name", "processName", "summary"))
    if not name.strip():
        return False
    return bool(keys & {"url", "href", "createTime", "createDate", "id", "messageId", "processInstanceId", "subject", "title"}) \
        or "待办" in json.dumps(d, ensure_ascii=False)[:200]


def _normalize_todo(d: dict) -> dict:
    return {
        "title": d.get("subject") or d.get("title") or d.get("name") or d.get("processName") or "",
        "url": d.get("url") or d.get("href") or "",
        "time": d.get("createTime") or d.get("createDate") or d.get("createTimeStr") or "",
        "raw": json.dumps({k: v for k, v in list(d.items())[:8]}, ensure_ascii=False)[:300],
    }


def _extract_todos_from_html(html: str) -> list[dict]:
    items: list[dict] = []
    # 尝试常见待办行模式：<a ... href="...">标题</a> 出现在待办容器内；宽松匹配含待办关键词的行
    for m in re.finditer(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>\s*</?(?:span|td|li)\b', html, re.I | re.S):
        href, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if not title or len(title) > 120:
            continue
        if any(k in title for k in ["待办", "审批", "申请", "公文", "流程"]):
            items.append({"title": title, "url": href, "time": ""})
    return items


def main() -> None:
    parser = argparse.ArgumentParser(description="致远OA待办审批巡检")
    parser.add_argument("--base", type=str, default=os.getenv("OA_BASE", "https://oa.xajuzi.com"))
    parser.add_argument("--username", type=str, default=os.getenv("OA_USERNAME", ""))
    parser.add_argument("--password", type=str, default=os.getenv("OA_PASSWORD", ""))
    parser.add_argument("--timeout", type=int, default=40)
    args = parser.parse_args()

    if not args.username or not args.password:
        print(json.dumps({
            "status": "error",
            "message": "缺少登录凭据。请通过 --username/--password 或环境变量 OA_USERNAME/OA_PASSWORD 提供。",
        }, ensure_ascii=False))
        return

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120"})

    ok, msg, seed = login(session, args.base, args.username, args.password)
    if not ok:
        print(json.dumps({"status": "error", "login": msg, "has_seed": bool(seed)}, ensure_ascii=False))
        return

    items, source = fetch_todos(session, args.base)
    output = {
        "status": "success",
        "login": msg,
        "pending_count": len(items),
        "todo_source": source,
        "items": items[:100],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()