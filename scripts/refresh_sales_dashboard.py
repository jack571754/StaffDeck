"""Refresh one existing sales dashboard without running notification steps."""

import argparse
import json

from app.data_query.service import execute_query_by_id
from app.db.database import engine
from app.db.models import ScheduledTask
from app.reporting.dashboard_hourly import fetch_task_hourly_series
from app.reporting.realtime_dashboard_syncer import sync_task_artifact_report
from sqlmodel import Session


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    with Session(engine) as db:
        task = db.get(ScheduledTask, args.task_id)
        if task is None:
            raise SystemExit("Task not found")
        hourly = fetch_task_hourly_series(db, task)
        if not hourly or not any(row["ops"] is not None for row in hourly):
            raise SystemExit(
                "No verified hourly business splits; dashboard not changed"
            )
        counts = {
            key: sum(row[key] is not None for row in hourly)
            for key in ("v", "yesterday", "ops", "live")
        }
        if args.check_only:
            print(json.dumps({"hourly_points": counts}, ensure_ascii=True))
            return
        rows = []
        for step in task.pipeline_steps_json or []:
            if step.get("type") == "query" and step.get("template_id"):
                result = execute_query_by_id(
                    db,
                    step["template_id"],
                    task.tenant_id,
                    step.get("params") or {},
                )
                rows.extend(result.rows)
        digest = sync_task_artifact_report(db, task, rows, hourly_series=hourly)
        if not digest:
            raise SystemExit("Dashboard artifact could not be updated")
        print(json.dumps({"updated": True, "sha256": digest, "hourly_points": counts}))


if __name__ == "__main__":
    main()
