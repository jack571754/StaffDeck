"""
飞书群机器人通知 (feishu-webhook-notify/run.py)

职责：向飞书群自定义机器人 webhook 发送交互式卡片消息（支持表格）或纯文本消息。
通道：POST <webhook>
  - 默认（卡片）：body {"msg_type":"interactive","card":{"header":...,"elements":[{"tag":"markdown","content":...}]}}
  - 纯文本：body {"msg_type":"text","content":{"text":...}}
凭证：webhook URL 只从环境变量 FEISHU_SALES_REPORT_WEBHOOK 读取，不明文入库。
"""

import argparse
import json
import os
import re
import sys

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

try:
    import requests
except Exception as exc:  # noqa: BLE001  # pragma: no cover
    print(json.dumps({"status": "error", "message": f"运行环境缺少 requests 依赖: {exc}"}, ensure_ascii=False))
    sys.exit(2)

MAX_CARD_TEXT_LEN = 15000
MAX_TEXT_LEN = 9000


# 退出码约定（供上层判读，取代“失败也返回 0”的掩盖行为）：
#   0 = 发送成功
#   1 = 运行/发送失败（正文、请求、飞书异常等）
#   2 = 环境/配置缺失（缺 webhook、缺依赖等）
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
    # 清除 Markdown 标题符号
    first_line_clean = first_line.lstrip("#").strip()

    # 若首行较短且不是表格行，则提拔为卡片 Header 标题，剩余部分作为正文
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

    # 若未显式定义对齐，且该列非空内容全部为数字/百分比/千分位金额，则靠右对齐
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
    """将 Markdown 文本解析为飞书卡片 Schema 2.0 的 elements 列表。

    正文中的 Markdown 表格将自动转为飞书原生 `tag: "table"` 组件；
    表格前后的说明文字、总结等保留为 `tag: "markdown"` 组件。
    若全文无表格，则返回单个 `markdown` 组件。
    """
    lines = content.splitlines()
    elements: list[dict] = []
    text_buffer: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        # 判断当前行与下一行是否构成 Markdown 表格头部
        if "|" in line and i + 1 < len(lines) and _is_separator_row(lines[i + 1]):
            # 1. 刷新表格前的 Markdown 文本
            if text_buffer:
                prev_text = "\n".join(text_buffer).strip()
                if prev_text:
                    elements.append({"tag": "markdown", "content": prev_text})
                text_buffer = []

            # 2. 解析表头与分隔符
            header_cells = _split_table_cells(line)
            sep_cells = _split_table_cells(lines[i + 1])
            i += 2  # 跳过表头和分隔行

            # 3. 逐行收集数据行
            table_rows_raw: list[list[str]] = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                table_rows_raw.append(_split_table_cells(lines[i]))
                i += 1

            if not header_cells:
                continue

            # 4. 构建列元数据（Schema 2.0 columns 规范）
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

            # 5. 构建行数据
            rows = []
            for raw_row in table_rows_raw:
                row_dict = {}
                for col_idx, col in enumerate(columns):
                    val = raw_row[col_idx] if col_idx < len(raw_row) else ""
                    row_dict[col["name"]] = val
                rows.append(row_dict)

            # 6. 添加原生 table 组件
            elements.append({
                "tag": "table",
                "page_size": max(1, min(int(page_size), 10)),
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

    # 刷新剩余 Markdown 文本
    if text_buffer:
        remaining_text = "\n".join(text_buffer).strip()
        if remaining_text:
            elements.append({"tag": "markdown", "content": remaining_text})

    if not elements:
        elements.append({"tag": "markdown", "content": "暂无正文内容"})

    return elements


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
    parser.add_argument("--text", default="", help="消息正文字符串")
    parser.add_argument("--text-file", default="", help="正文文件路径（Markdown）")
    parser.add_argument(
        "--msg-type",
        choices=["interactive", "text"],
        default="interactive",
        help="消息格式: interactive(飞书卡片, 支持表格, 默认) 或 text(纯文本)",
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
        help="表格每页展示行数（1-10，默认 10）",
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
        title, content = _parse_title_and_content(text, args.title)
        payload = build_card_payload(
            title, content, args.template, page_size=args.page_size
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
