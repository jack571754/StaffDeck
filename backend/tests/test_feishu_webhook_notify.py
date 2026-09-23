"""
Tests for feishu-webhook-notify table and card parsing functionality.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

# Load run.py from backend/_skill_fix/feishu-webhook-notify/run.py dynamically
SKILL_DIR = Path(__file__).resolve().parent.parent / "_skill_fix" / "feishu-webhook-notify"
RUN_PY_PATH = SKILL_DIR / "run.py"


def _load_run_module():
    spec = importlib.util.spec_from_file_location("feishu_webhook_notify_run", RUN_PY_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def run_mod():
    return _load_run_module()


class TestTableParsing:
    def test_is_separator_row(self, run_mod):
        assert run_mod._is_separator_row("| --- | --- |") is True
        assert run_mod._is_separator_row("|:---|---:|") is True
        assert run_mod._is_separator_row("| :-: | :-- |") is True
        assert run_mod._is_separator_row("--- | ---") is True
        assert run_mod._is_separator_row("| 部门 | 平台 |") is False
        assert run_mod._is_separator_row("常规文本") is False
        assert run_mod._is_separator_row("") is False

    def test_split_table_cells(self, run_mod):
        cells = run_mod._split_table_cells("| 部门 | 平台 | 达播净销 |")
        assert cells == ["部门", "平台", "达播净销"]

        cells_no_border = run_mod._split_table_cells("部门 | 平台 | 达播净销")
        assert cells_no_border == ["部门", "平台", "达播净销"]

    def test_get_column_alignment(self, run_mod):
        # Separator-specified alignments
        assert run_mod._get_column_alignment(":---:", ["100"]) == "center"
        assert run_mod._get_column_alignment("---:", ["100"]) == "right"
        assert run_mod._get_column_alignment(":---", ["100"]) == "left"

        # Auto-detected numeric alignment
        assert run_mod._get_column_alignment("---", ["402239.50", "0.00", "-134.43"]) == "right"
        assert run_mod._get_column_alignment("---", ["1,234.50", "99%"]) == "right"
        assert run_mod._get_column_alignment("---", ["¥100", "$50.2"]) == "right"

        # Text fallback
        assert run_mod._get_column_alignment("---", ["电商一部", "天猫"]) == "left"


class TestCardElementsConversion:
    def test_plain_markdown_without_table(self, run_mod):
        text = "今日无销售数据，请稍后重试。"
        elements = run_mod.parse_markdown_to_card_elements(text)
        assert len(elements) == 1
        assert elements[0]["tag"] == "markdown"
        assert elements[0]["content"] == text

    def test_markdown_with_table(self, run_mod):
        text = (
            "今日累计销售总净销 1115.73万，各部门明细如下：\n\n"
            "| 部门 | 平台 | 达播净销 | 运营净销 | 合计净销 |\n"
            "| --- | --- | ---: | ---: | ---: |\n"
            "| 电商一部 | 天猫 | 402239.50 | 2450062.75 | 2852302.25 |\n"
            "| 电商二部 | 抖音 | 2395882.51 | 3144916.36 | 5540798.87 |\n"
            "| 全店合计 | 全店合计 | 2798122.01 | 5594979.11 | 8393101.12 |\n\n"
            "数据截至 17:00，请各部门注意跟进退款。"
        )

        elements = run_mod.parse_markdown_to_card_elements(text, page_size=10)
        assert len(elements) == 3

        # Element 1: summary markdown
        assert elements[0]["tag"] == "markdown"
        assert "今日累计销售总净销" in elements[0]["content"]

        # Element 2: native table
        table = elements[1]
        assert table["tag"] == "table"
        assert table["page_size"] == 10
        assert table["freeze_first_column"] is True
        assert len(table["columns"]) == 5
        assert [c["display_name"] for c in table["columns"]] == ["部门", "平台", "达播净销", "运营净销", "合计净销"]
        assert table["columns"][0]["horizontal_align"] == "left"
        assert table["columns"][2]["horizontal_align"] == "right"
        assert all(c["width"] == "auto" for c in table["columns"])

        rows = table["rows"]
        assert len(rows) == 3
        assert rows[0]["col_0"] == "电商一部"
        assert rows[0]["col_1"] == "天猫"
        assert rows[0]["col_2"] == "402239.50"
        assert rows[2]["col_0"] == "全店合计"

        # Element 3: post-table notes
        assert elements[2]["tag"] == "markdown"
        assert "数据截至 17:00" in elements[2]["content"]

    def test_ragged_rows_padding(self, run_mod):
        text = (
            "| 部门 | 平台 | 达播净销 |\n"
            "| --- | --- | --- |\n"
            "| 电商一部 | 天猫 |\n"  # missing col 3
            "| 电商二部 | 抖音 | 100.00 | extra |\n"  # extra col
        )
        elements = run_mod.parse_markdown_to_card_elements(text)
        assert len(elements) == 1
        table = elements[0]
        assert len(table["columns"]) == 3
        assert len(table["rows"]) == 2
        assert table["rows"][0]["col_2"] == ""  # padded
        assert table["rows"][1]["col_2"] == "100.00"

    def test_build_card_payload_schema_2(self, run_mod):
        title = "📊 实时销售播报 · 09-20 23:59（当日累计）"
        content = (
            "总净销 1115.73万\n\n"
            "| 部门 | 平台 | 达播净销 |\n"
            "| --- | --- | ---: |\n"
            "| 电商一部 | 天猫 | 402239.50 |"
        )
        payload = run_mod.build_card_payload(title, content, template="blue", page_size=10)

        assert payload["msg_type"] == "interactive"
        card = payload["card"]
        assert card["schema"] == "2.0"
        assert card["header"]["title"]["content"] == title
        assert card["header"]["template"] == "blue"
        assert card["config"]["wide_screen_mode"] is True

        elements = card["body"]["elements"]
        assert len(elements) == 2
        assert elements[0]["tag"] == "markdown"
        assert elements[1]["tag"] == "table"


class TestSubprocessDryRun:
    def test_cli_dry_run(self):
        text = (
            "📊 实时销售播报 · 09-20 23:59\n\n"
            "一句话总结：全店总净销 1115.73万元。\n\n"
            "| 部门 | 平台 | 合计净销 |\n"
            "| --- | --- | ---: |\n"
            "| 电商一部 | 天猫 | 2852302.25 |\n"
            "| 全店合计 | 全店合计 | 11157334.16 |"
        )

        cmd = [
            sys.executable,
            str(RUN_PY_PATH),
            "--dry-run",
            "--text",
            text,
        ]
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            stdin=subprocess.DEVNULL,
            check=False,
        )
        assert res.returncode == 0
        data = json.loads(res.stdout)
        assert data["status"] == "dry_run"
        payload = data["payload"]
        assert payload["msg_type"] == "interactive"
        assert payload["card"]["schema"] == "2.0"
        assert payload["card"]["header"]["title"]["content"] == "📊 实时销售播报 · 09-20 23:59"
        table = payload["card"]["body"]["elements"][1]
        assert table["tag"] == "table"
        assert len(table["rows"]) == 2
