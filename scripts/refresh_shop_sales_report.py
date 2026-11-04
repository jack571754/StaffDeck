"""检查或生成指定任务的店铺独立页，不执行通知和调度。"""

import argparse
import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from app.db.database import engine
from app.db.models import ArtifactShareLink, ScheduledTask
from app.reporting.dashboard_shops import fetch_task_all_shops
from app.reporting.shop_sales_syncer import sync_task_shop_sales_report
from sqlmodel import Session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id")
    parser.add_argument("--apply", action="store_true", help="开启独立页并生成首次快照")
    args = parser.parse_args(argv)
    with Session(engine) as db:
        task = db.get(ScheduledTask, args.task_id)
        if task is None:
            print(json.dumps({"ok": False, "error": "任务不存在"}, ensure_ascii=True))
            return 1
        metadata = dict(task.metadata_json or {})
        link_id = metadata.get("shop_sales_share_link_id") or metadata.get("artifact_share_link_id")
        link = db.get(ArtifactShareLink, link_id) if link_id else None
        if not args.apply:
            print(json.dumps({
                "mode": "check", "task_id": task.id, "task_status": task.status,
                "enabled": metadata.get("shop_sales_report_enabled") is True,
                "has_context": bool(link and not link.is_revoked and link.tenant_id == task.tenant_id),
                "schedule": task.schedule_json,
            }, ensure_ascii=True))
            return 0
        shops = fetch_task_all_shops(db, task)
        if shops is None:
            print(json.dumps({"ok": False, "error": "全量店铺取数失败，未修改任务"}, ensure_ascii=True))
            return 1
        metadata["shop_sales_report_enabled"] = True
        task.metadata_json = metadata
        url = sync_task_shop_sales_report(db, task, shops=shops)
        if not url:
            db.rollback()
            print(json.dumps({"ok": False, "error": "页面发布失败，请检查分享绑定和 owner"},
                             ensure_ascii=True))
            return 1
        print(json.dumps({"ok": True, "task_id": task.id, "task_status": task.status,
                          "shops": len(shops), "url": url}, ensure_ascii=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
