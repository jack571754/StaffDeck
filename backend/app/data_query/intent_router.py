"""Natural language to query template intent router.

Routes natural language questions to pre-defined and verified QueryTemplates,
extracting parameters and resolving relative dates, with graceful fallback
to the full Harness Agent loop on low confidence or unmatched queries.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlmodel import Session, select

from app.data_query.date_resolver import (
    compute_prev_period_date,
    resolve_date_expression,
)
from app.data_query.models import QueryTemplate

logger = logging.getLogger(__name__)

# In-memory template cache: tenant_id -> (cache_timestamp, list[template_meta])
_CACHE_LOCK = threading.Lock()
_CACHE_TTL_SECONDS = 300.0  # 5 minutes
_TEMPLATE_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}

INTENT_ROUTER_PROMPT = """你是一个专业的 BI 数据查询意图路由专家。
你的任务是将用户的自然语言提问映射到系统已注册并审核通过的数据查询模板白名单中，并准确提取查询参数。

当前基准日期: {current_date} (周{weekday})

可选的活跃查询模板清单如下:
{templates_json}

规则与要求:
1. 仔细对比用户意图与模板描述及参数定义。
2. 仅当用户意图与某个模板明确匹配时，返回 matched: true 并给出所选模板 ID 和对应参数字典。
3. 如果用户只是闲聊、或者没有合适的模板能够支持该查询，必须返回 matched: false，置信度设为 0。
4. 置信度 confidence 范围 0.0 ~ 1.0。若完全匹配请给出 0.9 以上；若意图模糊或缺少关键参数，请降低置信度。
5. 对于日期参数（如"今天"、"昨天"、"近7天"），可以原样提取为自然语言或具体日期，后续系统会自动转换。

请输出如下且仅如下的 JSON 格式（无任何额外 markdown 标记）：
{{
  "matched": true,
  "template_id": "qt_xxx",
  "template_name": "query_name",
  "params": {{
    "param_name": "param_value"
  }},
  "confidence": 0.95,
  "explanation": "匹配理由简述"
}}
如果未匹配：
{{
  "matched": false,
  "template_id": "",
  "template_name": "",
  "params": {{}},
  "confidence": 0.0,
  "explanation": "无合适模板"
}}
"""


@dataclass
class RouteResult:
    template_id: str
    template_name: str
    params: dict[str, Any]
    confidence: float
    explanation: str = ""


def clear_intent_template_cache(tenant_id: str | None = None) -> None:
    """Clear cached template metadata for a specific tenant or all tenants."""
    with _CACHE_LOCK:
        if tenant_id:
            _TEMPLATE_CACHE.pop(tenant_id, None)
        else:
            _TEMPLATE_CACHE.clear()


def get_cached_templates(db: Session, tenant_id: str) -> list[dict[str, Any]]:
    """Retrieve active query templates for the given tenant, using a short-lived cache."""
    now = time.time()
    with _CACHE_LOCK:
        cached = _TEMPLATE_CACHE.get(tenant_id)
        if cached and (now - cached[0] < _CACHE_TTL_SECONDS):
            return cached[1]

    # Fetch active templates from DB
    stmt = (
        select(QueryTemplate)
        .where(
            QueryTemplate.tenant_id == tenant_id,
            QueryTemplate.status == "active",
        )
        .order_by(QueryTemplate.updated_at.desc())
    )
    rows = db.exec(stmt).all()

    items: list[dict[str, Any]] = []
    for row in rows:
        items.append(
            {
                "id": row.id,
                "name": row.name,
                "description": row.description,
                "params": row.params_json or [],
                "dimensions": row.dimensions_json or [],
                "metrics": row.metrics_json or [],
                "example_questions": row.example_questions_json or [],
                "query_type": row.query_type,
            }
        )

    with _CACHE_LOCK:
        _TEMPLATE_CACHE[tenant_id] = (now, items)

    return items


def _call_router_llm(
    question: str,
    templates: list[dict[str, Any]],
    base_date: date,
    tenant_id: str = "tenant_default",
    db: Session | None = None,
) -> dict[str, Any]:
    """Execute LLM call to classify intent and extract parameters."""
    weekday_labels = ["一", "二", "三", "四", "五", "六", "日"]
    current_date_str = base_date.isoformat()
    weekday_str = weekday_labels[base_date.weekday()]

    prompt = INTENT_ROUTER_PROMPT.format(
        current_date=current_date_str,
        weekday=weekday_str,
        templates_json=json.dumps(templates, ensure_ascii=False, indent=2),
    )

    try:
        from app.agents.branching import model_for_agent
        from app.db import engine
        from app.llm import LLMClient

        model_config = None
        if db is not None:
            model_config = model_for_agent(db, tenant_id, "router") or model_for_agent(db, "tenant_default", "router")
        if not model_config:
            with Session(engine) as session:
                model_config = model_for_agent(session, tenant_id, "router") or model_for_agent(session, "tenant_default", "router")
        if not model_config:
            logger.warning("No model config available for intent router (tenant=%s)", tenant_id)
            return {"matched": False}

        client = LLMClient(model_config)
        raw = client.generate_json(prompt, {"user_question": question})
        if isinstance(raw, dict):
            return raw
        return {"matched": False}
    except Exception:
        logger.exception("Failed to call router LLM")
        return {"matched": False}


def route_question(
    question: str,
    tenant_id: str,
    db: Session,
    base_date: date | None = None,
    threshold: float = 0.8,
) -> RouteResult | None:
    """Analyze a natural language question and route to a QueryTemplate if confidence >= threshold.

    Returns None if unmatched, low confidence, or on any error.
    """
    if not question or not question.strip():
        return None

    templates = get_cached_templates(db, tenant_id)
    if not templates:
        return None

    effective_base_date = base_date or datetime.now(tz=UTC).date()

    # Fast-path rule matching: template name or example question exact/contain match
    q_norm = question.strip().lower()
    for t in templates:
        t_name = str(t.get("name") or "").lower()
        if t_name and (t_name in q_norm or q_norm in t_name):
            logger.info("Direct name match for template %s (%s)", t["name"], t["id"])
            return RouteResult(
                template_id=t["id"],
                template_name=t["name"],
                params={},
                confidence=0.98,
                explanation=f"Direct match with template '{t['name']}'",
            )
        for ex in t.get("example_questions") or []:
            ex_norm = str(ex).strip().lower()
            if ex_norm and (ex_norm in q_norm or q_norm in ex_norm):
                logger.info("Direct example question match for template %s (%s): %s", t["name"], t["id"], ex)
                return RouteResult(
                    template_id=t["id"],
                    template_name=t["name"],
                    params={},
                    confidence=0.98,
                    explanation=f"Direct match with example question: {ex}",
                )

    try:
        raw_result = _call_router_llm(
            question=question.strip(),
            templates=templates,
            base_date=effective_base_date,
            tenant_id=tenant_id,
            db=db,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Intent routing execution failed, falling back: %s", exc)
        return None

    if not isinstance(raw_result, dict) or not raw_result.get("matched"):
        return None

    confidence = float(raw_result.get("confidence") or 0.0)
    if confidence < threshold:
        logger.info(
            "Intent matched template %s but confidence %.2f is below threshold %.2f",
            raw_result.get("template_name"),
            confidence,
            threshold,
        )
        return None

    template_id = str(raw_result.get("template_id") or "").strip()
    template_name = str(raw_result.get("template_name") or "").strip()
    raw_params = raw_result.get("params") or {}
    if not isinstance(raw_params, dict):
        raw_params = {}

    # Find the matching template definition to inspect param schemas
    target_tpl = next((t for t in templates if t["id"] == template_id or t["name"] == template_name), None)
    if not target_tpl:
        return None

    # Normalization & post-processing of parameters (e.g. date resolution & comparison)
    resolved_params = dict(raw_params)
    param_schemas = target_tpl.get("params") or []
    schema_by_name = {p.get("name"): p for p in param_schemas if isinstance(p, dict) and p.get("name")}

    for name, val in list(resolved_params.items()):
        val_str = str(val) if val is not None else ""
        schema = schema_by_name.get(name, {})
        ptype = str(schema.get("type") or "").lower()

        # Resolve date if param type is date or name implies date
        if ptype == "date" or "date" in name.lower():
            resolved_date = resolve_date_expression(val_str, base_date=effective_base_date)
            resolved_params[name] = resolved_date

            # Auto inject compare_to field if specified in schema
            compare_field = schema.get("compare_to")
            if compare_field and compare_field not in resolved_params:
                try:
                    prev_date = compute_prev_period_date(resolved_date, period="day")
                    resolved_params[compare_field] = prev_date
                except Exception:  # noqa: S110, BLE001
                    pass

    return RouteResult(
        template_id=target_tpl["id"],
        template_name=target_tpl["name"],
        params=resolved_params,
        confidence=confidence,
        explanation=str(raw_result.get("explanation") or ""),
    )
