"""读取当天全部店铺快照，复用销售任务的达播归属。"""

import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import Session

from app.db.models import ScheduledTask

logger = logging.getLogger(__name__)
BEIJING = timezone(timedelta(hours=8))


def shop_sales_timestamp(value: Any) -> str | None:
    """源数据库的无时区时间按北京时间解释，保留缺失状态。"""
    if value is None or value == "":
        return None
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        return stamp.replace(tzinfo=BEIJING).isoformat() if stamp.tzinfo is None else (
            stamp.astimezone(BEIJING).isoformat()
        )
    except (TypeError, ValueError):
        return None


def _amount(value: Any) -> float | None:
    if value is None:
        return None
    amount = float(value)
    if not math.isfinite(amount):
        raise ValueError("店铺金额不是有限数值")
    return amount


def build_all_shops_query(template_sql: str) -> str:
    # 沿用现有达播归属 CTE，店铺范围直接来自全量快照。
    parts = re.split(r',\s*ss_sale_sata\s+as\s*\(', template_sql, maxsplit=1,
                     flags=re.IGNORECASE)
    if len(parts) != 2 or not re.search(r'\bh1\s+as\s*\(', parts[0], re.IGNORECASE):
        raise ValueError('Sales template does not support complete shop attribution')
    return parts[0] + """,
shop_snapshots AS (
    SELECT CONCAT_WS('-', NULLIF(TRIM(平台), ''),
                      COALESCE(NULLIF(TRIM(店铺), ''), CONCAT('店铺#', shop_id))) AS 平台店铺,
        TRIM(平台) AS platform, TRIM(店铺) AS shop_name,
        COALESCE(NULLIF(TRIM(CAST(shop_id AS CHAR)), ''), TRIM(店铺)) AS shop_key,
        shop_id, 时间 AS updated_at,
        成交金额, 成交金额 - COALESCE(退款金额, 0) AS 净销金额,
        DENSE_RANK() OVER (
            PARTITION BY 平台,
                COALESCE(NULLIF(TRIM(CAST(shop_id AS CHAR)), ''), TRIM(店铺)), DATE(时间)
            ORDER BY 时间 DESC
        ) AS rk
    FROM ds_all.`实时销售数据汇总`
    WHERE 时间 >= :end_date AND 时间 < DATE_ADD(:end_date, INTERVAL 1 DAY)
),
shop_live AS (
    SELECT shop_id, rk, SUM(达播净销) AS 达播净销
    FROM h1 GROUP BY shop_id, rk
),
all_shop_sales AS (
    SELECT MAX(CASE WHEN s.rk = 1 THEN s.平台店铺 END) AS item,
        s.platform, MAX(s.shop_id) AS shop_id,
        MAX(CASE WHEN s.rk = 1 THEN s.shop_name END) AS shop_name,
        MAX(CASE WHEN s.rk = 1 THEN s.updated_at END) AS updated_at,
        SUM(CASE WHEN s.rk = 1 THEN s.成交金额 END) AS 销售金额,
        SUM(CASE WHEN s.rk = 1 THEN s.净销金额 END) AS 净销金额,
        SUM(CASE WHEN s.rk = 1 THEN s.净销金额 END)
            - SUM(CASE WHEN s.rk = 2 THEN s.净销金额 END) AS 增量金额,
        SUM(CASE WHEN s.rk = 1 THEN COALESCE(l.达播净销, 0) ELSE 0 END) AS 达播金额,
        COUNT(DISTINCT s.rk) AS snapshot_count
    FROM shop_snapshots s
    LEFT JOIN shop_live l ON s.shop_id = l.shop_id AND s.rk = l.rk
    WHERE s.rk IN (1, 2)
    GROUP BY s.platform, s.shop_key
)
SELECT item, platform, shop_id, shop_name, updated_at,
    ROUND(净销金额/10000.0, 2) AS 净销_万,
    CASE WHEN snapshot_count < 2 THEN NULL
         ELSE ROUND(增量金额/10000.0, 2) END AS 环比增量_万,
    ROUND((净销金额-达播金额)/10000.0, 2) AS 运营净销_万,
    ROUND(达播金额/10000.0, 2) AS 达播净销_万
FROM all_shop_sales
ORDER BY 净销金额 DESC, item
"""


def fetch_task_all_shops(db: Session, task: ScheduledTask) -> list[dict[str, Any]] | None:
    from app.data_query.connectors import get_connector
    from app.data_query.executor import _validate_params
    from app.data_query.service import get_data_source, get_query_template

    for step in task.pipeline_steps_json or []:
        if step.get('type') != 'query' or not step.get('template_id'):
            continue
        template = get_query_template(db, step['template_id'], task.tenant_id)
        if not template or 'ss_sale_sata' not in template.query_content:
            continue
        if getattr(template, 'status', 'active') != 'active':
            return None
        try:
            params = _validate_params(step.get('params') or {}, template.params_json)
            source = get_data_source(db, template.data_source_id, task.tenant_id)
            if source is None or source.status != 'active':
                return None
            connector = get_connector(source)
            try:
                result = connector.execute(
                    build_all_shops_query(template.query_content), params,
                    timeout=60, max_rows=10000,
                )
            finally:
                connector.close()
            if len(result.rows) >= 10000:
                raise ValueError('Complete shop query exceeded row limit')
            return [{
                'name': str(row['item']), 'net': _amount(row.get('净销_万')),
                'ops': _amount(row.get('运营净销_万')),
                'live': _amount(row.get('达播净销_万')),
                'platform': str(row.get('platform') or ''),
                'shop_id': str(row.get('shop_id') or ''),
                'shop_name': str(row.get('shop_name') or row['item']),
                'updated_at': shop_sales_timestamp(row.get('updated_at')),
                'mom': (f"{float(row['环比增量_万']):+.2f}万"
                        if row.get('环比增量_万') is not None else None),
                'neg': (float(row.get('净销_万') or 0) < 0),
            } for row in result.rows]
        except Exception:
            logger.exception('Complete shop sales unavailable')
            return None
    return None
