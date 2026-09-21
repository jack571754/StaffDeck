# -*- coding: utf-8 -*-
"""
通用技能同步脚本 (sync_skill.py)

把本地 _skill_fix/<技能目录>/ 下的 SKILL.md + run.py 同步（upsert）到运行中的 StaffDeck 系统。

机制：通用技能以 DB 行方式存储（skill_markdown=SKILL.md，skill_files=run.py 等），
更新入口为 POST /api/enterprise/general-skills/import，且必须传 original_slug=原slug，
才会走「按 slug 原地覆盖」分支。本脚本即封装这一过程。

用法：
  # 登录凭据建议用环境变量注入，避免明文进 shell 历史
  SD_TENANT=<租户id> SD_USER=<登录用户名> SD_PASSWORD=<密码> \\
  python _skill_fix/sync_skill.py [技能目录] [--base http://127.0.0.1:5173]
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    import requests
except Exception as exc:  # pragma: no cover
    print(json.dumps({"status": "error", "message": f"缺少 requests 依赖: {exc}"}, ensure_ascii=False))
    sys.exit(1)

BASE = os.getenv("SD_BASE", "http://127.0.0.1:5173")


def parse_frontmatter(text: str) -> dict:
    meta: dict = {}
    if text.startswith("---"):
        body = text.split("---", 2)[1]
        for line in body.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip("'\"")
    return meta


def load_skill(folder: Path) -> dict:
    md_path = folder / "SKILL.md"
    if not md_path.exists():
        raise SystemExit(f"未找到 SKILL.md: {md_path}")
    markdown = md_path.read_text(encoding="utf-8")
    meta = parse_frontmatter(markdown)
    slug = meta.get("slug") or meta.get("name") or folder.name
    files = []
    for p in sorted(folder.iterdir()):
        if p.name in ("SKILL.md",) or p.suffix.lower() in (".py", ".md", ".json", ".txt"):
            if p.name == ".feishu_cache.json":
                continue
            if p.is_file():
                files.append({"path": p.name, "content": p.read_text(encoding="utf-8", errors="replace")})
    return {"slug": slug, "name": meta.get("name", slug),
            "description": meta.get("description", ""), "markdown": markdown, "files": files}


def main() -> None:
    parser = argparse.ArgumentParser(description="同步本地通用技能到 StaffDeck")
    parser.add_argument("skill_dir", nargs="?", default="check-price-anomalies",
                        help="技能目录名（默认 check-price-anomalies）")
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--tenant", default=os.getenv("SD_TENANT", ""))
    parser.add_argument("--username", default=os.getenv("SD_USER", ""))
    parser.add_argument("--password", default=os.getenv("SD_PASSWORD", ""))
    args = parser.parse_args()

    folder = (Path(__file__).parent / args.skill_dir).resolve()
    skill = load_skill(folder)
    tenant = args.tenant or skill.get("metadata_tenant")
    if not args.tenant or not args.username or not args.password:
        raise SystemExit("必须提供 --tenant/--username/--password（或 SD_TENANT/SD_USER/SD_PASSWORD 环境变量）")

    base = args.base.rstrip("/")
    s = requests.Session()

    # 1. 登录取 JWT
    login = s.post(f"{base}/api/auth/login",
                   json={"tenant_id": args.tenant, "username": args.username, "password": args.password},
                   timeout=6)
    if login.status_code != 200:
        print(json.dumps({"status": "error", "step": "login", "http": login.status_code,
                          "body": login.text[:300]}, ensure_ascii=False))
        raise SystemExit(1)
    token = login.json().get("token", "")
    headers = {"Authorization": f"Bearer {token}"}

    # 2. original_slug=slug 触发原地覆盖更新
    payload = {
        "tenant_id": args.tenant,
        "slug": skill["slug"],
        "original_slug": skill["slug"],
        "name": skill["name"],
        "description": skill["description"],
        "markdown": skill["markdown"],
        "files": skill["files"],
        "status": "published",
    }
    resp = s.post(f"{base}/api/enterprise/general-skills/import",
                  json=payload, headers=headers, timeout=30)
    print(json.dumps({
        "status": "ok" if resp.status_code == 200 else "error",
        "step": "import",
        "slug": skill["slug"],
        "http": resp.status_code,
        "files": [f["path"] for f in skill["files"]],
        "body": resp.json() if resp.headers.get("content-type", "").startswith("application/json")
                else resp.text[:300],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()