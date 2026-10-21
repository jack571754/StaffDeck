"""Hourly snapshots using the sales template's existing attribution rules."""

import json
import logging
import re
from typing import Any

from sqlmodel import Session

from app.db.models import ScheduledTask

logger = logging.getLogger(__name__)


def build_hourly_query(
    template_sql: str, product_count: int = 0, *, include_tmall: bool = True
) -> str:
    """Reuse the source CTEs, with latest snapshots per day/hour instead of per day.

    Fail closed for unsupported templates rather than silently changing business rules.
    The original query template is never modified.
    """
    parts = re.split(r",\s*h1\s+as\s*\(", template_sql, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2 or "ss_sale_sata" not in parts[1]:
        raise ValueError("Sales template does not support hourly attribution")
    prefix = parts[0]
    prefix, time_count = re.subn(
        r"(DATE_FORMAT\(时间,\s*'%%Y-%%m-%%d'\))\s+order by 时间 desc",
        r"\1, HOUR(时间) order by 时间 desc",
        prefix,
        flags=re.IGNORECASE,
    )
    prefix, product_window_count = re.subn(
        r"(DATE_FORMAT\(date,\s*'%%Y-%%m-%%d'\))\s+order by date desc",
        r"\1, HOUR(date) order by date desc",
        prefix,
        flags=re.IGNORECASE,
    )
    prefix, date_count = re.subn(
        r"DATE_FORMAT\((时间|date),\s*'%%Y-%%m-%%d'\)\s*=\s*:end_date",
        r"(\1 >= DATE_SUB(:end_date, INTERVAL 1 DAY) "
        r"AND \1 < DATE_ADD(:end_date, INTERVAL 1 DAY))",
        prefix,
        flags=re.IGNORECASE,
    )
    prefix, timestamp_count = re.subn(
        r"DATE_FORMAT\(日期,\s*'%%Y-%%m-%%d'\)\s+as\s+日期",
        "日期 as 日期",
        prefix,
        flags=re.IGNORECASE,
    )
    if time_count != 4 or product_window_count != 1 or date_count != 5 or timestamp_count != 1:
        raise ValueError("Sales source CTEs changed; hourly attribution needs review")
    # The schedule contains years of history. Restrict its window calculation to
    # products actually present in these snapshots, preserving all dates per product.
    if product_count:
        product_params = ",".join(f":trend_product_{i}" for i in range(product_count))
        prefix = prefix.replace(
            "AND 产品ID IS NOT NULL",
            f"AND 产品ID IS NOT NULL AND TRIM(产品ID) IN ({product_params})",
        )
    prefix = re.sub(
        r"live_schedule\s+AS\s*\((.*?)\),\s*final_migration\s+AS",
        r"""live_schedule_history AS (\1),
live_schedule AS (
    SELECT * FROM live_schedule_history
    WHERE 直播开始日期 < DATE_ADD(:end_date, INTERVAL 1 DAY)
      AND (next_直播开始日期 IS NULL
           OR next_直播开始日期 > DATE_SUB(:end_date, INTERVAL 1 DAY))
),
final_migration AS""",
        prefix,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not include_tmall:
        prefix, count = re.subn(
            r'from t4 where 归属\s*=\s*"达播"',
            "from t4 where 1=0",
            prefix,
            flags=re.IGNORECASE,
        )
        if count != 1:
            raise ValueError("Tmall attribution source changed")
    return (
        prefix
        + """,
hourly_total AS (
    SELECT DATE(时间) AS day, HOUR(时间) AS hr, SUM(当日净销额)/10000.0 AS net
    FROM biao1 WHERE rk = 1 GROUP BY DATE(时间), HOUR(时间)
),
hourly_live AS (
    SELECT DATE(日期) AS day, HOUR(日期) AS hr, SUM(达播净销)/10000.0 AS net
    FROM h WHERE rk = 1 GROUP BY DATE(日期), HOUR(日期)
)
SELECT CAST(total.day AS CHAR) AS day, total.hr,
       total.net, total.net - COALESCE(live.net, 0) AS ops,
       COALESCE(live.net, 0) AS live
FROM hourly_total total
LEFT JOIN hourly_live live ON live.day = total.day AND live.hr = total.hr
ORDER BY total.day, total.hr LIMIT 48
"""
    )


def parse_hourly_series(rows: list[dict[str, Any]], today: str) -> list[dict[str, Any]]:
    """Missing hours stay null; zero and negative net sales are real observations."""
    from datetime import date, timedelta

    yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    series = [
        {"h": f"{hour:02d}时", "v": None, "ops": None, "live": None, "yesterday": None}
        for hour in range(24)
    ]
    for row in rows:
        hour = int(row["hr"])
        if not 0 <= hour < 24:
            continue
        day = str(row["day"])[:10]
        if day == today:
            for key, source in (("v", "net"), ("ops", "ops"), ("live", "live")):
                value = row.get(source)
                series[hour][key] = round(float(value), 2) if value is not None else None
        elif day == yesterday and row.get("net") is not None:
            series[hour]["yesterday"] = round(float(row["net"]), 2)
    return series


def align_current_hour(series: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    """Use the actual current summary snapshot at its recorded hour for all three lines."""
    for row in rows:
        if row.get("category") != "大盘" or row.get("item") != "电商整体":
            continue
        stamp = str(row.get("数据更新时间") or row.get("更新时间") or "")
        match = re.search(r"(?:T|\s)(\d{2}):\d{2}", stamp)
        if not match:
            return
        hour = int(match[1])
        for point in series:
            point_hour = int(point["h"][:2])
            if point_hour > hour:
                for key in ("v", "ops", "live"):
                    point[key] = None
            elif point_hour == hour:
                for key, column in (
                    ("v", "净销_万"),
                    ("ops", "运营净销_万"),
                    ("live", "达播净销_万"),
                ):
                    if row.get(column) is not None:
                        point[key] = round(float(row[column]), 2)
        return


def fetch_task_hourly_series(db: Session, task: ScheduledTask) -> list[dict[str, Any]] | None:
    """Read historical snapshots without changing or executing the task's notify step."""
    from app.data_query.connectors import get_connector
    from app.data_query.executor import _validate_params
    from app.data_query.service import execute_adhoc_query, get_data_source, get_query_template

    for step in task.pipeline_steps_json or []:
        if step.get("type") != "query" or not step.get("template_id"):
            continue
        template = get_query_template(db, step["template_id"], task.tenant_id)
        if not template or "ss_sale_sata" not in template.query_content:
            continue
        try:
            params = _validate_params(step.get("params") or {}, template.params_json)
            products = execute_adhoc_query(
                db,
                template.data_source_id,
                task.tenant_id,
                """SELECT JSON_ARRAYAGG(product_id) AS ids FROM (
                    SELECT DISTINCT TRIM(item_id) AS product_id
                    FROM ds_tm.tm_product_all_realtime
                    WHERE date >= DATE_SUB(:end_date, INTERVAL 1 DAY)
                      AND date < DATE_ADD(:end_date, INTERVAL 1 DAY)
                ) ids LIMIT 1""",
                params=params,
            )
            product_ids = products.rows[0].get("ids") if products.rows else None
            if isinstance(product_ids, str):
                product_ids = json.loads(product_ids)
            product_ids = product_ids or [""]
            params.update({f"trend_product_{i}": str(value) for i, value in enumerate(product_ids)})
            sql = build_hourly_query(template.query_content, len(product_ids), include_tmall=False)
            prefix = sql.split(",\nhourly_total AS", 1)[0]
            source = get_data_source(db, template.data_source_id, task.tenant_id)
            if source is None:
                return None
            connector = get_connector(source)
            try:
                result = connector.execute(sql, params, timeout=60, max_rows=48)
                products = connector.execute(
                    prefix
                    + """ SELECT 统计日期, 商品ID, 支付金额, 成功退款金额
                    FROM tm_shishi_pro WHERE rk=1 AND 商品名称 NOT LIKE '%%购物金%%'
                    AND 统计日期 >= '2025-09-01' LIMIT 100000""",
                    params,
                    timeout=60,
                    max_rows=100000,
                )
                if len(products.rows) >= 100000:
                    raise ValueError("Hourly product snapshot limit exceeded")
                schedules = connector.execute(
                    prefix + " SELECT * FROM live_schedule LIMIT 100000",
                    params,
                    timeout=60,
                    max_rows=100000,
                )
                if len(schedules.rows) >= 100000:
                    raise ValueError("Hourly schedule limit exceeded")
                migrations = connector.execute(
                    prefix + " SELECT * FROM final_migration LIMIT 10000",
                    params,
                    timeout=60,
                    max_rows=10000,
                )
                if len(migrations.rows) >= 10000:
                    raise ValueError("Product migration limit exceeded")
            finally:
                connector.close()
            tmall = aggregate_tmall_hourly(products.rows, schedules.rows, migrations.rows)
            for row in result.rows:
                key = (str(row["day"])[:10], int(row["hr"]))
                row["live"] = float(row["live"]) + tmall.get(key, 0.0)
                row["ops"] = float(row["net"]) - row["live"]
            return parse_hourly_series(result.rows, str(params["end_date"]))
        except Exception:
            logger.exception("Hourly sales snapshots unavailable; keep missing series empty")
            return None
    return None


def aggregate_tmall_hourly(
    products: list[dict[str, Any]],
    schedules: list[dict[str, Any]],
    migrations: list[dict[str, Any]],
) -> dict[tuple[str, int], float]:
    """Apply the template's migration-first attribution in memory, avoiding a huge join."""
    from datetime import datetime, time

    def stamp(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if hasattr(value, "year"):
            return datetime.combine(value, time.min)
        text = str(value).strip()
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d"):
                try:
                    # Match the naive local timestamps returned by the MySQL driver.
                    return datetime.strptime(text, fmt)  # noqa: DTZ007
                except ValueError:
                    continue
            raise ValueError("Unsupported attribution timestamp") from None

    schedule_map = {}
    for row in schedules:
        schedule_map.setdefault(str(row["产品ID"]).strip(), []).append(row)
    migration_map = {}
    for row in migrations:
        migration_map.setdefault(str(row["迁移ID"]).strip(), []).append(row)
    totals = {}
    for row in products:
        product = str(row["商品ID"]).strip()
        instant = stamp(row["统计日期"])
        rules = migration_map.get(product)
        if rules:
            live_count = sum(
                (
                    rule["迁移归属"]
                    if stamp(rule["迁移开始日期"]) <= instant <= stamp(rule["迁移截止日期"])
                    else rule["原始归属"]
                )
                == "达播"
                for rule in rules
            )
        else:
            live_count = int(
                any(
                    stamp(rule["直播开始日期"]) <= instant
                    and (
                        rule.get("next_直播开始日期") is None
                        or instant < stamp(rule["next_直播开始日期"])
                    )
                    for rule in schedule_map.get(product, [])
                )
            )
        if live_count:
            key = (instant.date().isoformat(), instant.hour)
            net = float(row.get("支付金额") or 0) - float(row.get("成功退款金额") or 0)
            totals[key] = totals.get(key, 0.0) + live_count * net / 10000
    return totals
