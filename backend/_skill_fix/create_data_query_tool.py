"""
直写 data_query 工具行 (create_data_query_tool.py)

API 的 ToolCreateRequest.tool_type Literal 未开放 data_query，
按 tests/test_tool_executor.py 的构造方式幂等 upsert 一条 Tool 行。
用法：
  backend/.venv/Scripts/python.exe _skill_fix/create_data_query_tool.py \
      --tenant <TENANT_ID> --template-id qt_xxx
"""

import argparse
import sys

sys.path.insert(0, ".")

from sqlmodel import Session, select

from app.agents.branching import ensure_open_gallery_binding
from app.db.database import engine
from app.db.models import Tool

TOOL_NAME = "query_realtime_sales"


def main() -> None:
    parser = argparse.ArgumentParser(description="幂等 upsert data_query 工具行")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--template-id", required=True)
    args = parser.parse_args()

    with Session(engine) as session:
        row = session.exec(
            select(Tool).where(Tool.tenant_id == args.tenant, Tool.name == TOOL_NAME)
        ).first()
        if row:
            row.config_json = {"template_id": args.template_id, "output_format": "table"}
            session.add(row)
            session.commit()
            session.refresh(row)
            ensure_open_gallery_binding(session, args.tenant, "tool", row.id, "active")
            session.commit()
            print(f"updated existing tool id={row.id}")
        else:
            row = Tool(
                tenant_id=args.tenant,
                name=TOOL_NAME,
                display_name="实时销售底表查询",
                description="查询当日实时销售底表（店铺×达播/运营×净销/成交/退款+部门/小组/平台维度）",
                bucket="数据查询",
                tool_type="data_query",
                method="GET",
                url=f"data_query://{args.template_id}",
                config_json={"template_id": args.template_id, "output_format": "table"},
                input_schema={
                    "type": "object",
                    "properties": {
                        "params": {
                            "type": "object",
                            "properties": {
                                "end_date": {
                                    "type": "string",
                                    "description": "统计结束日期 YYYY-MM-DD，定时任务传当天",
                                }
                            },
                        }
                    },
                },
                capability_scope="general",
                enabled=True,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            ensure_open_gallery_binding(session, args.tenant, "tool", row.id, "active")
            session.commit()
            print(f"created tool id={row.id}")


if __name__ == "__main__":
    main()
