# -*- coding: utf-8 -*-
"""
商品价格速查 - 单品价格与维护价速查 (product-price-lookup/run.py)

职责：仅查询单个商品的价盘数据（S促/618/D11 三档 + 当前大促阶段）。
不连接业务数据库，不触发任何审计/推送副作用。独立为纯查询脚本，避免 import 链过重失败。

数据来源：飞书多维表格（价盘基准价）。带 5 分钟本地缓存，避免重复全量表。
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
import json
import re
import time
from pathlib import Path

try:
    import requests
except Exception as exc:  # pragma: no cover
    print(json.dumps({"status": "error", "message": f"运行环境缺少 requests 依赖: {exc}"}, ensure_ascii=False))
    sys.exit(0)

# ================= 配置（优先环境变量，可被平台注入覆盖） =================
APP_TOKEN = os.getenv("FEISHU_APP_TOKEN", "JspJbuCiRawJ4IsVlz5ceX4enrc")
PRODUCT_TABLE_ID = os.getenv("FEISHU_TABLE_ID", "tbl0v7eI79pY8zxj")
SCHEDULE_TABLE_ID = "tblE43CKC009P90S"
APP_ID = os.getenv("FEISHU_APP_ID", "cli_a40af3f077f8d00d")
APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")

CACHE_FILE = Path(".feishu_cache.json")
CACHE_TTL = 300  # 秒


def _log(*args) -> None:
    sys.stderr.write("[%s] " % time.strftime("%H:%M:%S") + " ".join(str(a) for a in args))
    sys.stderr.write("\n")


def get_feishu_token() -> str:
    if not APP_ID or not APP_SECRET:
        return ""
    try:
        r = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": APP_ID, "app_secret": APP_SECRET},
            timeout=5,
        )
        return r.json().get("tenant_access_token", "")
    except Exception as exc:
        _log("获取飞书 token 失败:", exc)
        return ""


def current_stage(token: str) -> tuple[str, str]:
    """判断当前大促阶段，返回 (阶段名, 价档列名)。查不到/网络失败一律兜底 S促。"""
    default = ("S促", "S促普惠价")
    if not token:
        return default
    try:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{SCHEDULE_TABLE_ID}/records"
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params={"page_size": 100}, timeout=4)
        now_ms = time.time() * 1000
        for item in r.json().get("data", {}).get("items", []):
            f = item.get("fields", {})
            st = str(f.get("大促阶段") or f.get("阶段") or "").upper()
            tn = f.get("时间节点") or f.get("时间范围")
            start_ms = end_ms = None
            if isinstance(tn, list) and len(tn) >= 2:
                start_ms, end_ms = tn[0], tn[1]
            if isinstance(start_ms, (int, float)) and isinstance(end_ms, (int, float)) and start_ms <= now_ms <= end_ms:
                if "618" in st:
                    return ("618", "618价格")
                if any(k in st for k in ("D11", "双11", "11.11")):
                    return ("D11", "D11价格")
    except Exception as exc:
        _log("判断大促阶段失败:", exc)
    return default


def load_cache(refresh: bool = False) -> tuple[list, str, str]:
    """带 5 分钟缓存的飞书价盘加载。命中直接返回内存缓存，冷启动时才全量拉。"""
    if not refresh and CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if time.time() - data.get("ts", 0) < CACHE_TTL:
                return data.get("items", []), data.get("stage", "S促"), data.get("col", "S促普惠价")
        except Exception:
            pass

    token = get_feishu_token()
    stage, col = current_stage(token)
    items: list = []
    if token:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{PRODUCT_TABLE_ID}/records"
        page_token = ""
        while True:
            params: dict = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token
            try:
                res = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=8)
            except Exception as exc:
                _log("拉取飞书价盘失败:", exc)
                break
            data = res.json()
            if data.get("code") != 0:
                _log("飞书接口返回错误:", data.get("code"))
                break
            batch = data.get("data", {}).get("items", [])
            items.extend(batch)
            if not data.get("data", {}).get("has_more"):
                break
            page_token = data.get("data", {}).get("page_token", "")
    try:
        CACHE_FILE.write_text(
            json.dumps({"ts": time.time(), "stage": stage, "col": col, "items": items}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass
    return items, stage, col


def _name_of(fields: dict) -> str:
    return " ".join(str(fields.get(k) or "") for k in ("商品名称", "商品昵称", "商品名")).lower().strip()


def query(keyword: str) -> None:
    items, stage, col = load_cache()
    # 多词：按常见分隔符切分，任一词命中即算；同时支持整体子串
    keywords = [k.strip() for k in re.split(r"[\s，,、;；/]+", keyword) if k.strip()]
    if not keywords:
        keywords = [keyword.strip()]
    low_kws = [k.lower() for k in keywords]

    results = []
    for item in items:
        fields = item.get("fields", {})
        name = _name_of(fields)
        if not name:
            continue
        # 任一名词是商品名的子串，或商品名包含任一名词
        hit = any(kw in name for kw in low_kws) or any(name in kw for kw in low_kws)
        if not hit:
            continue
        results.append({
            "商品名称": fields.get("商品名称") or fields.get("商品昵称") or "",
            "商品昵称": fields.get("商品昵称"),
            "规格": fields.get("规格"),
            "当前大促阶段": stage,
            "当前执行维护价": fields.get(col) or fields.get("S促普惠价"),
            "价盘": {
                "S促普惠价": fields.get("S促普惠价"),
                "618价格": fields.get("618价格"),
                "D11价格": fields.get("D11价格"),
            },
        })

    output = {
        "status": "success",
        "query_type": "单品价格速查",
        "search_keyword": keyword,
        "current_promotion_stage": stage,
        "matched_count": len(results),
        "results": results,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="商品价格速查")
    parser.add_argument("--mode", choices=["query"], default="query", help="固定为 query，不支持 audit")
    parser.add_argument("--keyword", type=str, default="")
    parser.add_argument("--refresh", action="store_true", help="强制刷新缓存后查询")
    args = parser.parse_args()

    if args.mode != "query" or not args.keyword.strip():
        print(json.dumps({
            "status": "error",
            "message": "缺少查询关键词。用法: --mode query --keyword <商品关键词>",
        }, ensure_ascii=False))
        return

    try:
        if args.refresh:
            load_cache(refresh=True)
        query(args.keyword.strip())
    except Exception as exc:
        print(json.dumps({"status": "error", "message": f"查询异常: {exc}"}, ensure_ascii=False))


if __name__ == "__main__":
    main()