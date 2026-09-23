"""
飞书群机器人通知 (feishu-webhook-notify/run.py)

职责：向飞书群自定义机器人 webhook 发送交互式卡片消息（支持表格、分栏、图表）或纯文本消息。
通道：POST <webhook>
  - 默认（卡片）：body {"msg_type":"interactive","card":{"header":...,"elements":[...]}}
  - 纯文本：body {"msg_type":"text","content":{"text":...}}
凭证：webhook URL 只从环境变量 FEISHU_SALES_REPORT_WEBHOOK 读取，不明文入库。
"""

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

try:
    from dotenv import load_dotenv
    for candidate in [
        os.path.join(os.getcwd(), "backend", ".env"),
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
    ]:
        if os.path.exists(candidate):
            load_dotenv(candidate)
            break
except Exception:  # noqa: BLE001, S110
    pass

try:
    import requests
except Exception as exc:  # noqa: BLE001  # pragma: no cover
    print(json.dumps({"status": "error", "message": f"运行环境缺少 requests 依赖: {exc}"}, ensure_ascii=False))
    sys.exit(2)

MAX_CARD_TEXT_LEN = 15000
MAX_TEXT_LEN = 9000


def _fail(message: str, exit_code: int = 1, **extra) -> None:
    print(json.dumps({"status": "error", "message": message, **extra}, ensure_ascii=False))
    sys.exit(exit_code)


def _read_stdin_payload() -> dict:
    if sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read().strip()
    except (OSError, UnicodeDecodeError):
        return {}
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {"text": raw}


def _read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _parse_title_and_content(raw_text: str, custom_title: str = "") -> tuple[str, str]:
    """从 Markdown 文本中拆解卡片标题与正文。"""
    if custom_title.strip():
        return custom_title.strip(), raw_text.strip()

    lines = raw_text.splitlines()
    first_idx = -1
    for idx, line in enumerate(lines):
        if line.strip():
            first_idx = idx
            break

    if first_idx == -1:
        return "通知播报", ""

    first_line = lines[first_idx].strip()
    first_line_clean = first_line.lstrip("#").strip()

    if not first_line.startswith("|") and len(first_line_clean) <= 64:
        rest = "\n".join(lines[first_idx + 1:]).strip()
        return first_line_clean, (rest if rest else first_line_clean)

    return "📊 实时销售播报", raw_text.strip()


def _is_separator_row(line: str) -> bool:
    """判断是否为 Markdown 表格分隔行，例如 | --- | :---: | ---: |"""
    line = line.strip()
    if not line or "|" not in line:
        return False
    cells = [c.strip() for c in line.strip("|").split("|")]
    return len(cells) >= 1 and all(bool(re.match(r"^:?-+:?$", c)) for c in cells)


def _split_table_cells(line: str) -> list[str]:
    """拆分 Markdown 表格单行中的单元格文本。"""
    raw = line.strip().removeprefix("|").removesuffix("|")
    return [c.strip() for c in raw.split("|")]


def _get_column_alignment(sep_cell: str, values: list[str]) -> str:
    """根据分隔符定义或数值特征推断列对齐方式（left/center/right）。"""
    sep = sep_cell.strip()
    if sep.startswith(":") and sep.endswith(":"):
        return "center"
    if sep.endswith(":"):
        return "right"
    if sep.startswith(":"):
        return "left"

    non_empty = [v for v in values if v and v != "-"]
    if not non_empty:
        return "left"

    clean_vals = [
        re.sub(r"[*_`¥￥$€£]", "", v).strip().replace(",", "").rstrip("%")
        for v in non_empty
    ]

    def _is_number(val: str) -> bool:
        try:
            float(val)
            return True
        except ValueError:
            return False

    if all(_is_number(v) for v in clean_vals):
        return "right"
    return "left"


def parse_markdown_to_card_elements(content: str, page_size: int = 10) -> list[dict]:
    """将 Markdown 文本解析为飞书卡片 Schema 2.0 的 elements 列表。"""
    lines = content.splitlines()
    elements: list[dict] = []
    text_buffer: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        if "|" in line and i + 1 < len(lines) and _is_separator_row(lines[i + 1]):
            if text_buffer:
                prev_text = "\n".join(text_buffer).strip()
                if prev_text:
                    elements.append({"tag": "markdown", "content": prev_text})
                text_buffer = []

            header_cells = _split_table_cells(line)
            sep_cells = _split_table_cells(lines[i + 1])
            i += 2

            table_rows_raw: list[list[str]] = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                table_rows_raw.append(_split_table_cells(lines[i]))
                i += 1

            if not header_cells:
                continue

            columns = []
            for col_idx, raw_name in enumerate(header_cells):
                col_name = raw_name.strip() or f"列{col_idx + 1}"
                sep_spec = sep_cells[col_idx] if col_idx < len(sep_cells) else ""
                col_vals = [
                    row[col_idx] for row in table_rows_raw if col_idx < len(row)
                ]
                align = _get_column_alignment(sep_spec, col_vals)

                columns.append({
                    "name": f"col_{col_idx}",
                    "display_name": col_name,
                    "data_type": "lark_md",
                    "width": "auto",
                    "horizontal_align": align,
                })

            rows = []
            for raw_row in table_rows_raw:
                row_dict = {}
                for col_idx, col in enumerate(columns):
                    val = raw_row[col_idx] if col_idx < len(raw_row) else ""
                    row_dict[col["name"]] = val
                rows.append(row_dict)

            elements.append({
                "tag": "table",
                "page_size": max(1, min(int(page_size), 20)),
                "row_height": "low",
                "freeze_first_column": True,
                "header_style": {
                    "text_align": "left",
                    "bold": True,
                },
                "columns": columns,
                "rows": rows,
            })
            continue

        text_buffer.append(line)
        i += 1

    if text_buffer:
        remaining_text = "\n".join(text_buffer).strip()
        if remaining_text:
            elements.append({"tag": "markdown", "content": remaining_text})

    if not elements:
        elements.append({"tag": "markdown", "content": "暂无正文内容"})

    return elements


def build_sales_report_card(
    rows: list[dict],
    title: str = "",
    template: str = "blue",
) -> dict:
    """基于实时销售数据行构建原生飞书卡片 2.0：

    1. 顶部指标汇总栏 (column_set): 今日净销、达播净销、运营端净销
    2. 品牌与渠道明细表格 (table): 7 列规范 (品牌/净销/环比净销/运营端净销/环比净销/达播端净销/环比净销)
    3. 24小时时段环比柱状图 (chart): 今日 vs 昨日 24 时段走势对比
    """
    brand_rows = []
    hourly_rows = []
    summary_row = None

    for r in rows:
        cat = r.get("category", "")
        item = r.get("item", "")
        if cat == "24小时环比":
            hourly_rows.append(r)
        elif cat == "大盘" and item == "电商整体":
            summary_row = r
            brand_rows.append(("电商整体", r))
        elif cat == "可复美" and item == "整体":
            brand_rows.append(("可复美整体", r))
        elif cat == "可复美":
            brand_rows.append((f"{item}可复美", r))
        elif cat == "可丽金" and item == "整体":
            brand_rows.append(("可丽金整体", r))
        elif cat == "可丽金":
            brand_rows.append((f"{item}可丽金", r))
        else:
            brand_rows.append((item, r))

    tot_net = float(summary_row.get("净销_万") or 0) if summary_row else 0.0
    tot_ops = float(summary_row.get("运营净销_万") or 0) if summary_row else 0.0
    tot_live = float(summary_row.get("达播净销_万") or 0) if summary_row else 0.0
    tot_diff = float(summary_row.get("环比增量_万") or 0) if summary_row else 0.0

    # 提取数据更新时间（优先从数据行中获取）
    update_time = None
    if summary_row:
        update_time = (
            summary_row.get("数据更新时间")
            or summary_row.get("更新时间")
            or summary_row.get("data_time")
        )
    if not update_time and rows:
        for r in rows:
            t_val = r.get("数据更新时间") or r.get("更新时间") or r.get("data_time")
            if t_val:
                update_time = str(t_val).strip()
                break

    if not update_time:
        update_time = datetime.now(UTC).astimezone().strftime("%m-%d %H:%M")
    elif len(update_time) >= 16 and update_time[4] == "-" and update_time[7] == "-":
        update_time = update_time[5:16]

    card_title = title or f"📊 实时销售播报 · {update_time}（当日累计）"

    def fmt_val_styled(v, is_core: bool = False) -> str:
        try:
            f = float(v or 0)
            if abs(f) < 0.0001:
                return "<font color='grey'>0.00万</font>"
            txt = f"{f:.2f}万"
            return f"**{txt}**" if is_core else txt
        except (ValueError, TypeError):
            return "<font color='grey'>0.00万</font>"

    def fmt_diff_styled(v, is_core: bool = False) -> str:
        try:
            f = float(v or 0)
            if abs(f) < 0.0001:
                return "<font color='grey'>0.00万</font>"
            sign = "+" if f > 0 else ""
            txt = f"{sign}{f:.2f}万"
            color = "green" if f > 0 else "red"
            body = f"**{txt}**" if is_core else txt
            return f"<font color='{color}'>{body}</font>"
        except (ValueError, TypeError):
            return "<font color='grey'>0.00万</font>"

    table_rows = []
    for display_name, r in brand_rows:
        is_core_row = display_name in ("电商整体", "可复美整体", "可丽金整体")
        brand_label = f"**{display_name}**" if is_core_row else display_name
        table_rows.append({
            "brand": brand_label,
            "net_sales": fmt_val_styled(r.get("净销_万"), is_core=is_core_row),
            "net_diff": fmt_diff_styled(r.get("环比增量_万"), is_core=is_core_row),
            "ops_net_sales": fmt_val_styled(r.get("运营净销_万"), is_core=is_core_row),
            "ops_diff": fmt_diff_styled(r.get("运营增量_万"), is_core=is_core_row),
            "live_net_sales": fmt_val_styled(r.get("达播净销_万"), is_core=is_core_row),
            "live_diff": fmt_diff_styled(r.get("达播增量_万"), is_core=is_core_row),
        })

    chart_values = []
    for hr in hourly_rows:
        h_str = str(hr.get("item", ""))
        v_today = float(hr.get("运营净销_万") or 0)
        v_yest = float(hr.get("运营增量_万") or 0)
        chart_values.append({"hour": h_str, "type": "今日", "value": v_today})
        chart_values.append({"hour": h_str, "type": "昨日", "value": v_yest})

    elements = [
        {
            "tag": "markdown",
            "content": f"**实时汇总** <font color='grey'>（数据更新至 {update_time}）</font>",
            "text_align": "left",
            "text_size": "normal",
        },
        {
            "tag": "column_set",
            "flex_mode": "bisect",
            "background_style": "grey",
            "horizontal_spacing": "8px",
            "horizontal_align": "left",
            "columns": [
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "vertical_align": "top",
                    "vertical_spacing": "8px",
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"<font color='grey'>今日净销</font>\n**{tot_net:.2f}万**",
                            "text_align": "center",
                            "text_size": "normal",
                        }
                    ],
                },
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "vertical_align": "top",
                    "vertical_spacing": "8px",
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"<font color='grey'>达播净销</font>\n**{tot_live:.2f}万**",
                            "text_align": "center",
                            "text_size": "normal",
                        }
                    ],
                },
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "vertical_align": "top",
                    "vertical_spacing": "8px",
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"<font color='grey'>运营端净销</font>\n**{tot_ops:.2f}万**",
                            "text_align": "center",
                            "text_size": "normal",
                        }
                    ],
                },
            ],
        },
        {
            "tag": "markdown",
            "content": f"📢 **整体销售播报**：截止 {update_time}，电商整体净销 **{tot_net:.2f}万**（较上一时刻增量 {fmt_diff_styled(tot_diff, is_core=True)}，其中运营端 **{tot_ops:.2f}万**，达播端 **{tot_live:.2f}万**），各渠道运行平稳。",
            "text_align": "left",
            "text_size": "normal",
        },
        {
            "tag": "markdown",
            "content": "**品牌与渠道销售明细（万元）**",
            "text_align": "left",
        },
        {
            "tag": "table",
            "columns": [
                {"data_type": "lark_md", "name": "brand", "display_name": "品牌", "horizontal_align": "left", "width": "auto"},
                {"data_type": "lark_md", "name": "net_sales", "display_name": "净销", "horizontal_align": "right", "width": "auto"},
                {"data_type": "lark_md", "name": "net_diff", "display_name": "环比净销", "horizontal_align": "right", "width": "auto"},
                {"data_type": "lark_md", "name": "ops_net_sales", "display_name": "运营端净销", "horizontal_align": "right", "width": "auto"},
                {"data_type": "lark_md", "name": "ops_diff", "display_name": "环比净销", "horizontal_align": "right", "width": "auto"},
                {"data_type": "lark_md", "name": "live_net_sales", "display_name": "达播端净销", "horizontal_align": "right", "width": "auto"},
                {"data_type": "lark_md", "name": "live_diff", "display_name": "环比净销", "horizontal_align": "right", "width": "auto"},
            ],
            "rows": table_rows,
            "row_height": "low",
            "freeze_first_column": False,
            "header_style": {
                "background_style": "grey",
                "bold": True,
                "lines": 1,
            },
            "page_size": 10,
        },
        {
            "tag": "chart",
            "chart_spec": {
                "type": "bar",
                "title": {
                    "text": "24小时时段走势环比（今日 vs 昨日，万元）",
                },
                "data": {
                    "values": chart_values,
                },
                "xField": [
                    "hour",
                    "type",
                ],
                "yField": "value",
                "seriesField": "type",
                "legends": {
                    "visible": True,
                    "orient": "bottom",
                },
            },
            "preview": True,
            "color_theme": "converse",
            "height": "300px",
        },
    ]

    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "config": {
                "wide_screen_mode": True,
                "update_multi": True,
            },
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": card_title,
                },
                "template": template or "blue",
            },
            "body": {
                "elements": elements,
            },
        },
    }


def _ensure_table_columns_auto(data: object) -> None:
    """递归遍历卡片结构，将所有 table 组件中的所有列宽度设为自适应 ('width': 'auto')。"""
    if isinstance(data, dict):
        if data.get("tag") == "table":
            for col in data.get("columns", []):
                if isinstance(col, dict):
                    col["width"] = "auto"
        for val in data.values():
            _ensure_table_columns_auto(val)
    elif isinstance(data, list):
        for item in data:
            _ensure_table_columns_auto(item)


def _resolve_interactive_payload(
    raw_text: str,
    custom_title: str = "",
    template: str = "blue",
    page_size: int = 10,
) -> dict:
    """智能解析输入为飞书 Card Schema 2.0 交互式卡片 Payload：

    1. 若为包含 query_realtime_sales_summary 结果行的 JSON，则自动生成图表+汇总+表格卡片；
    2. 若为原生飞书卡片 JSON，则直接打包返回；
    3. 若为 Markdown 文本，则走标准解析器转为带有原生表格的卡片。
    """
    payload: dict | None = None
    trimmed = raw_text.strip()
    if "```" in trimmed:
        match = re.search(r"```(?:json)?\s*([\{\[].*?[\}\]])\s*```", trimmed, re.DOTALL)
        if match:
            trimmed = match.group(1).strip()

    if (trimmed.startswith("{") and trimmed.endswith("}")) or (trimmed.startswith("[") and trimmed.endswith("]")):
        try:
            parsed = json.loads(trimmed)
            # Case 1: 直接是 rows 列表或包含 rows 的字典
            rows = None
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict) and "category" in parsed[0]:
                rows = parsed
            elif isinstance(parsed, dict) and "rows" in parsed and isinstance(parsed["rows"], list):
                first_r = parsed["rows"][0] if parsed["rows"] else {}
                if isinstance(first_r, dict) and "category" in first_r:
                    rows = parsed["rows"]

            if rows is not None:
                payload = build_sales_report_card(rows, title=custom_title, template=template)

            # Case 2: 完整的 card payload
            elif isinstance(parsed, dict):
                if "msg_type" in parsed and "card" in parsed:
                    payload = parsed
                elif "card" in parsed and isinstance(parsed["card"], dict):
                    payload = {"msg_type": "interactive", "card": parsed["card"]}
                elif "schema" in parsed or "i18n_elements" in parsed or "body" in parsed or "i18n_header" in parsed or "config" in parsed:
                    card_dict = dict(parsed)
                    if "schema" not in card_dict:
                        card_dict["schema"] = "2.0"
                    if "header" not in card_dict and "i18n_header" not in card_dict:
                        card_dict["header"] = {
                            "title": {"tag": "plain_text", "content": custom_title or "📊 实时销售播报"},
                            "template": template or "blue",
                        }
                    payload = {"msg_type": "interactive", "card": card_dict}
        except (json.JSONDecodeError, ValueError):
            pass

    if payload is None:
        # Case 3: 传统 Markdown 正文解析
        title, content = _parse_title_and_content(raw_text, custom_title)
        payload = build_card_payload(title, content, template=template, page_size=page_size)

    _ensure_table_columns_auto(payload)
    return payload


def build_card_payload(
    title: str,
    content: str,
    template: str = "blue",
    page_size: int = 10,
) -> dict:
    """构建飞书交互式卡片 JSON（Schema 2.0，原生支持表格与 Markdown 高亮）。"""
    if len(content) > MAX_CARD_TEXT_LEN:
        content = content[:MAX_CARD_TEXT_LEN] + "\n\n（正文超长已截断）"

    elements = parse_markdown_to_card_elements(content, page_size=page_size)

    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "config": {
                "wide_screen_mode": True,
                "update_multi": True,
            },
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": title or "通知播报",
                },
                "template": template or "blue",
            },
            "body": {
                "elements": elements,
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="飞书群机器人卡片/文本通知")
    parser.add_argument("--text", default="", help="消息正文字符串（支持 Markdown、原生卡片 JSON 或查询结果 JSON）")
    parser.add_argument("--text-file", default="", help="正文文件路径（Markdown 或 JSON）")
    parser.add_argument(
        "--msg-type",
        choices=["interactive", "text"],
        default="interactive",
        help="消息格式: interactive(飞书卡片, 支持表格/图表, 默认) 或 text(纯文本)",
    )
    parser.add_argument("--title", default="", help="自定义卡片标题（默认自动提取正文首行）")
    parser.add_argument(
        "--template",
        default="blue",
        help="卡片顶部颜色(blue/turquoise/wathet/carmine/violet/green/orange/red)",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=10,
        help="表格每页展示行数（1-20，默认 10）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="演练模式：仅打印构建的消息卡片 JSON，不实际发起 HTTP 请求",
    )
    args = parser.parse_args()

    text = args.text
    if args.text_file:
        try:
            text = _read_text_file(args.text_file)
        except OSError as exc:
            _fail(f"读取正文文件失败: {exc}")
    if not text.strip():
        stdin_payload = _read_stdin_payload()
        text = str(stdin_payload.get("text") or stdin_payload.get("QUERY") or "")
    if not text.strip():
        _fail("正文为空：请通过 --text/--text-file 或 stdin 提供 text")

    if args.msg_type == "interactive":
        payload = _resolve_interactive_payload(
            text,
            custom_title=args.title,
            template=args.template,
            page_size=args.page_size,
        )
    else:
        if len(text) > MAX_TEXT_LEN:
            text = text[:MAX_TEXT_LEN] + "\n\n（正文超长已截断）"
        payload = {"msg_type": "text", "content": {"text": text}}

    if args.dry_run:
        print(json.dumps({"status": "dry_run", "payload": payload}, ensure_ascii=False, indent=2))
        sys.exit(0)

    webhook = (os.getenv("FEISHU_SALES_REPORT_WEBHOOK") or "").strip()
    if not webhook:
        _fail(
            "缺少环境变量 FEISHU_SALES_REPORT_WEBHOOK，请先在 backend/.env 配置群机器人 webhook 地址",
            exit_code=2,
        )

    try:
        resp = requests.post(
            webhook,
            json=payload,
            timeout=15,
        )
    except requests.RequestException as exc:
        _fail(f"webhook 请求失败: {exc}")
    except Exception as exc:  # noqa: BLE001
        _fail(f"webhook 请求异常: {exc}")

    try:
        body = resp.json()
    except (ValueError, json.JSONDecodeError):
        body = {}
    status_code = body.get("StatusCode", body.get("code"))
    if resp.status_code == 200 and status_code in (0, None):
        print(
            json.dumps(
                {
                    "status": "success",
                    "message": "发送成功",
                    "msg_type": args.msg_type,
                    "StatusCode": 0,
                },
                ensure_ascii=False,
            )
        )
    else:
        _fail(f"飞书返回异常 http={resp.status_code} body={str(body)[:200]}")


if __name__ == "__main__":
    main()
