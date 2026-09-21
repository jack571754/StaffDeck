"""价格巡检判断层（price_analyst.py）核心逻辑单测。

覆盖确定性判断部分（不触网络/数据库）：
- 跨平台按商品昵称归类 + 识别跨平台联动
- 去噪 / 置信标注 / 建议
- 摘要 / ai_material 组装
- 下钻 SQL 构造
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1] / "_skill_fix" / "check-price-anomalies"
ANALYST_PY = SKILL_DIR / "price_analyst.py"


@pytest.fixture(scope="module")
def pa():
    spec = importlib.util.spec_from_file_location("price_analyst_under_test", ANALYST_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _base(platform="京东", pid="123456", name="iPad", anomaly_type="跌破维护普惠价",
          price="100.0", bench="120.0", diff="-20.0", **kwargs):
    data = {
        "platform": platform, "product_id": pid, "product_name": name,
        "anomaly_type": anomaly_type, "current_price": price,
        "benchmark_price": bench, "diff_amount": diff,
    }
    data.update(kwargs)
    return data


# ---------------------------------------------------------------------------
# 跨平台归类
# ---------------------------------------------------------------------------


def test_group_by_merchandise_cross_platform(pa):
    anomalies = [
        _base(platform="京东", pid="111111", name="iPhone15", diff="-30.0"),
        _base(platform="抖音", pid="SKU_9", name="iPhone15", diff="-25.0"),
        _base(platform="唯品会", pid="400", name="iPhone15", diff="-28.0"),
        _base(platform="拼多多", pid="880", name="", anomaly_type="同一天普惠价波动",
              price="50.0", bench=None, diff=None, fluctuation_amount=2.0),
    ]
    res = pa.group_by_merchandise(anomalies)
    assert res["cross_platform_groups"] == 1
    by_name = {g["name"]: g for g in res["groups"]}
    phone = by_name["iPhone15"]
    assert phone["cross_platform"] is True
    assert phone["platforms"] == ["京东", "唯品会", "抖音"]
    assert phone["item_count"] == 3
    # 无昵称的拼多多退化为按 SKU 单组
    assert any(g["kind"] == "by_sku" and g["name"] == "拼多多:880" for g in res["groups"])


def test_group_impact_uses_breach_magnitude(pa):
    anomalies = [
        _base(pid="1", name="A", diff="-5.0"),
        _base(pid="2", name="B", diff="-50.0"),
    ]
    res = pa.group_by_merchandise(anomalies)
    assert res["groups"][0]["name"] == "B"  # impact 降序


# ---------------------------------------------------------------------------
# 去噪 / 置信
# ---------------------------------------------------------------------------


def test_denoise_small_breach_low_confidence(pa):
    small = _base(diff="-0.2")
    conf, cat = pa._confidence(small)
    assert conf == "中"
    assert "小幅" in cat


def test_denoise_no_bench_fluctuation_low(pa):
    it = {"platform": "拼多多", "product_id": "1", "product_name": "",
          "anomaly_type": "同一天普惠价波动", "current_price": "50.0",
          "benchmark_price": None, "diff_amount": None, "fluctuation_amount": 0.3}
    conf, cat = pa._confidence(it)
    assert conf == "低"
    assert "微幅" in cat


def test_denoise_partitions_high_low(pa):
    src = pa
    h = _base(diff="-20.0")
    l = {"platform": "拼多多", "product_id": "1", "product_name": "",
         "anomaly_type": "同一天普惠价波动", "current_price": "50.0",
         "benchmark_price": None, "diff_amount": None, "fluctuation_amount": 0.2}
    res = src.denoise([h, l])
    assert len(res["high"]) == 1
    assert len(res["low"]) == 1


# ---------------------------------------------------------------------------
# 建议
# ---------------------------------------------------------------------------


def test_recommendation_breach_vs_swing(pa):
    breach = {"diff_amount": "-5.0"}
    swing = {"diff_amount": None}
    assert "人工确认" in pa.recommendation_for(breach)
    assert "周期性平价" in pa.recommendation_for(swing)


# ---------------------------------------------------------------------------
# 摘要 / ai_material
# ---------------------------------------------------------------------------


def test_build_summary_counts_breach(pa):
    anomalies = [_base(diff="-20.0"), _base(diff="-5.0"),
                 {"diff_amount": None, "fluctuation_amount": 1.0, "platform": "京东"}]
    grouped = [{"name": "iPad", "impact": 20.0, "cross_platform": False}]
    s = pa.build_summary(anomalies, grouped)
    assert s["severe_breach_count"] == 2
    assert s["involved_platforms"] == ["京东"]
    assert s["cross_platform_groups"] == 0


def test_build_ai_material_contains_red_line(pa):
    report = {
        "check_time": "2026-09-14 09:00", "stage": "S促",
        "summary": {"total_anomalies": 1, "severe_breach_count": 1,
                    "cross_platform_groups": 0, "involved_platforms": [], "top_impact_group": None, "top_impact": 0},
        "grouped": [],
    }
    material = pa.build_ai_material(report)
    assert "禁止自动改价" in material
    assert "S促" in material


# ---------------------------------------------------------------------------
# 下钻 SQL
# ---------------------------------------------------------------------------


def test_build_down_sql_common(pa):
    sql = pa.build_down_sql("京东", 50)
    assert "%s" in sql
    assert "ORDER BY `date` DESC" in sql
    assert sql.count("SELECT") == 1
    assert "LIMIT 50" in sql


def test_build_down_sql_unknown_platform_raises(pa):
    with pytest.raises(ValueError):
        pa.build_down_sql("不存在", 10)


def test_build_analysis_wires_nickname_and_suggestion(pa):
    pa.LOW_CONF_WINDOW = 0.5
    raw = {
        "status": "success", "check_time": "2026-09-14 09:00",
        "current_promotion_stage": "S促", "benchmark_price_column": "S促普惠价",
        "anomalies": [_base(name="iPad", diff="-20.0")],
    }
    report = pa.build_analysis(raw)
    assert report["status"] == "success"
    assert report["denoised_count"] == 0
    assert len(report["grouped"][0]["items"]) == 1
    item = report["grouped"][0]["items"][0]
    assert item["confidence"] == "高"
    assert "人工" in item["suggestion"]
    assert "禁止自动改价" in report["ai_material"]


# ---------------------------------------------------------------------------
# 记忆学习
# ---------------------------------------------------------------------------


def test_mem_key_requires_sku(pa):
    assert pa._mem_key("京东", "123") == "京东|123"
    assert pa._mem_key("京东", "") == ""


def test_memory_note_hit_and_miss(pa):
    memory = {"京东|123": {"label": "常态调价", "note": "每周五下调",
                           "count": 2, "first_seen": "2026-09-01", "updated": "2026-09-10"}}
    assert pa.memory_note_for("京东", "123", memory) == "历史判定为「常态调价」：每周五下调"
    assert pa.memory_note_for("京东", "999", memory) == ""
    # 空 sku 不匹配
    assert pa.memory_note_for("京东", "", memory) == ""


def test_annotate_memory_adds_fields(pa):
    items = [{"platform": "抖音", "product_id": "SKU_9", "product_name": "", "diff_amount": "-2"}]
    memory = {"抖音|SKU_9": {"label": "属实跌破", "note": "确认调价"}}
    out = pa.annotate_memory(items, memory)
    assert out[0]["memory_label"] == "属实跌破"
    assert "历史判定" in out[0]["memory_note"]


def test_analyze_uses_memory_hits(pa):
    pa.LOW_CONF_WINDOW = 0.5
    raw = {
        "status": "success", "check_time": "2026-09-14 09:00",
        "current_promotion_stage": "S促", "benchmark_price_column": "S促普惠价",
        "anomalies": [_base(pid="123456", name="iPad", diff="-20.0")],
    }
    memory = {"京东|123456": {"label": "常态调价", "note": ""}}
    report = pa.build_analysis(raw, memory=memory)
    assert report["memory_hits"] == 1
    item = report["grouped"][0]["items"][0]
    assert item["memory_label"] == "常态调价"
    assert "历史判定为「常态调价」" in report["ai_material"]


def test_learn_validates_label(tmp_path, pa, monkeypatch):
    monkeypatch.setattr(pa, "MEMORY_FILE", tmp_path / "m.json")
    res = pa.learn("京东", "123", "常态调价", "每周五")
    assert res["key"] == "京东|123"
    assert res["count"] == 1
    # 非法标签拒绝
    try:
        pa.learn("京东", "123", "随便")
        assert False, "非法标签应抛错"
    except ValueError:
        pass
    # 缺 sku 拒绝
    try:
        pa.learn("京东", "", "常态调价")
        assert False, "缺 sku 应抛错"
    except ValueError:
        pass


def test_learn_persists_and_roundtrips(tmp_path, pa, monkeypatch):
    monkeypatch.setattr(pa, "MEMORY_FILE", tmp_path / "m.json")
    pa.learn("唯品会", "400", "属实跌破", "已核实")
    again = pa.learn("唯品会", "400", "属实跌破", "已核实")
    assert again["count"] == 2  # 幂等累加
    loaded = pa.load_memory()
    entry = loaded["唯品会|400"]
    assert entry["label"] == "属实跌破"
    assert entry["count"] == 2
    assert entry["first_seen"] == entry["updated"]  # 同一秒内首见与更新一致
    assert pa.memory_note_for("唯品会", "400", loaded) == "历史判定为「属实跌破」：已核实"


def test_load_memory_missing_file_returns_empty(tmp_path, pa, monkeypatch):
    monkeypatch.setattr(pa, "MEMORY_FILE", tmp_path / "nope.json")
    assert pa.load_memory() == {}


# ---------------------------------------------------------------------------
# 智能体策略卡（AGENT_PROMPT.md）：随技能包同步、且承载合规契约，不得丢失
# ---------------------------------------------------------------------------


def test_agent_prompt_strategy_card() -> None:
    prompt = SKILL_DIR / "AGENT_PROMPT.md"
    assert prompt.is_file(), "价格技能必须随包携带智能体策略卡 AGENT_PROMPT.md"
    text = prompt.read_text(encoding="utf-8")
    # 合规红线必须显式存在
    assert "绝不自动改价" in text
    assert "人工二次复核" in text or "人工核实" in text
    # 判别层的四种调用模式应齐全
    for mode in ("--mode analyze", "--mode down", "--mode learn", "--mode memory"):
        assert mode in text
    # 标签白名单（记忆降噪）
    assert "常态调价" in text and "属实跌破" in text