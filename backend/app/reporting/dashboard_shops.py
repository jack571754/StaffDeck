"""Complete shop rankings from today's sales snapshots, without a priority list."""

import logging
import re
from typing import Any

from sqlmodel import Session

from app.db.models import ScheduledTask

logger = logging.getLogger(__name__)


def build_all_shops_query(template_sql: str) -> str:
    # Reuse the task's live-sales attribution CTEs; the shop totals and names
    # come directly from sales snapshots rather than the curated dimension list.
    parts = re.split(r',\s*ss_sale_sata\s+as\s*\(', template_sql, maxsplit=1,
                     flags=re.IGNORECASE)
    if len(parts) != 2 or not re.search(r'\bh1\s+as\s*\(', parts[0], re.IGNORECASE):
        raise ValueError('Sales template does not support complete shop attribution')
    return parts[0] + """,
shop_snapshots AS (
    SELECT CONCAT_WS('-', NULLIF(TRIM(平台), ''),
                      COALESCE(NULLIF(TRIM(店铺), ''), CONCAT('店铺#', shop_id))) AS 平台店铺,
        shop_id, 成交金额, 成交金额 - COALESCE(退款金额, 0) AS 净销金额,
        DENSE_RANK() OVER (
            PARTITION BY 平台, 店铺, DATE(时间) ORDER BY 时间 DESC
        ) AS rk
    FROM ds_all.`实时销售数据汇总`
    WHERE 时间 >= :end_date AND 时间 < DATE_ADD(:end_date, INTERVAL 1 DAY)
),
shop_live AS (
    SELECT shop_id, rk, SUM(达播净销) AS 达播净销
    FROM h1 GROUP BY shop_id, rk
),
all_shop_sales AS (
    SELECT s.平台店铺 AS item,
        SUM(CASE WHEN s.rk = 1 THEN s.成交金额 ELSE 0 END) AS 销售金额,
        SUM(CASE WHEN s.rk = 1 THEN s.净销金额 ELSE 0 END) AS 净销金额,
        SUM(CASE WHEN s.rk = 1 THEN s.净销金额 ELSE 0 END)
            - SUM(CASE WHEN s.rk = 2 THEN s.净销金额 ELSE 0 END) AS 增量金额,
        SUM(CASE WHEN s.rk = 1 THEN COALESCE(l.达播净销, 0) ELSE 0 END) AS 达播金额,
        COUNT(DISTINCT s.rk) AS snapshot_count
    FROM shop_snapshots s
    LEFT JOIN shop_live l ON s.shop_id = l.shop_id AND s.rk = l.rk
    WHERE s.rk IN (1, 2)
    GROUP BY s.平台店铺
)
SELECT item, ROUND(净销金额/10000.0, 2) AS 净销_万,
    CASE WHEN snapshot_count < 2 THEN NULL
         ELSE ROUND(增量金额/10000.0, 2) END AS 环比增量_万,
    ROUND((净销金额-达播金额)/10000.0, 2) AS 运营净销_万,
    ROUND(达播金额/10000.0, 2) AS 达播净销_万
FROM all_shop_sales
WHERE 销售金额 > 0
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
        try:
            params = _validate_params(step.get('params') or {}, template.params_json)
            source = get_data_source(db, template.data_source_id, task.tenant_id)
            if source is None:
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
                'name': str(row['item']), 'net': float(row['净销_万']),
                'ops': float(row['运营净销_万']), 'live': float(row['达播净销_万']),
                'mom': (f"{float(row['环比增量_万']):+.2f}万"
                        if row.get('环比增量_万') is not None else None),
                'neg': float(row['净销_万']) < 0,
            } for row in result.rows]
        except Exception:
            logger.exception('Complete shop sales unavailable')
            return None
    return None
