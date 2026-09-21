"""price 巡检脚本（check-price-anomalies/run.py）核心逻辑单测。

网络/数据库统一以 monkeypatch 打桩，覆盖：
- 状态目录持久化为稳定绝对路径（P0：去重跨 run 生效）
- 「同一天普惠价波动」阈值可配且默认 0.5（P1：降噪）
- 去重签名 / 变化才推送 / 状态回落清理
- 京东链接提取平台键

被测脚本不在 backend 包内，故通过 importlib 从源文件加载。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1] / "_skill_fix" / "check-price-anomalies"
RUN_PY = SKILL_DIR / "run.py"


def _load_run(
    tmp_path: Path,
    *,
    state_dir: str | None = None,
    skill_state_dir: str | None = None,
    isolate: bool = False,
) -> object:
    """从源文件加载 run 模块。

    - state_dir: 注入 PRICE_STATE_DIR 验证 env 解析（仅此测试用）。
    - skill_state_dir: 注入 SKILL_STATE_DIR 验证平台注入优先。
    - isolate: 将状态/缓存落到 tmp 绝对目录，避免写入真实用户目录。
    """
    import os

    if state_dir is not None:
        os.environ["PRICE_STATE_DIR"] = state_dir
    else:
        os.environ.pop("PRICE_STATE_DIR", None)

    if skill_state_dir is not None:
        os.environ["SKILL_STATE_DIR"] = skill_state_dir
    else:
        os.environ.pop("SKILL_STATE_DIR", None)

    spec = importlib.util.spec_from_file_location("price_audit_under_test", RUN_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if isolate:
        module.STATE_DIR = tmp_path
        module.CACHE_FILE = tmp_path / ".feishu_cache.json"
        module.ALERT_STATE_FILE = tmp_path / ".feishu_alert_state.json"
    return module


@pytest.fixture()
def run_mod(tmp_path: Path) -> object:
    return _load_run(tmp_path, isolate=True)


# ---------------------------------------------------------------------------
# P0：状态目录持久化
# ---------------------------------------------------------------------------


def test_state_dir_defaults_to_absolute_home_path(tmp_path: Path) -> None:
    import os

    os.environ.pop("PRICE_STATE_DIR", None)
    os.environ.pop("SKILL_STATE_DIR", None)
    run_mod = _load_run(tmp_path, isolate=False)
    assert run_mod.STATE_DIR.is_absolute()
    assert ".staffdeck_price_audit" in str(run_mod.STATE_DIR)


def test_state_dir_honors_env_override(tmp_path: Path) -> None:
    custom = tmp_path / "custom-state"
    run_mod = _load_run(tmp_path, state_dir=str(custom), isolate=False)
    assert run_mod.STATE_DIR == custom.resolve()
    assert run_mod.ALERT_STATE_FILE == (custom / ".feishu_alert_state.json").resolve()


def test_state_dir_prefers_skill_state_dir_over_price_state_dir(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill-state"
    price_dir = tmp_path / "price-state"
    run_mod = _load_run(
        tmp_path,
        skill_state_dir=str(skill_dir),
        state_dir=str(price_dir),
        isolate=False,
    )
    assert run_mod.STATE_DIR == skill_dir.resolve()
    assert run_mod.ALERT_STATE_FILE == (skill_dir / ".feishu_alert_state.json").resolve()



def test_dedup_state_survives_across_runs(tmp_path: Path, run_mod, monkeypatch) -> None:
    """同一异动二次巡检不重复推送 —— 状态写入稳定目录而非工作区相对路径。"""
    records = [
        {
            "platform": "京东", "product_id": "111111", "record_date": "2026-09-13",
            "full_time": "2026-09-13 10:00:00", "actual_puhui_price": 10.0,
        }
    ]
    monkeypatch.setattr(run_mod, "fetch_platform_records", lambda queries: records)
    # 基准价高于现价，触发「跌破维护普惠价」；无疫日波动
    monkeypatch.setattr(run_mod, "load_benchmarks", lambda *a, **k: ({(("京东", "111111")): 15.0}, {}, "S促", "S促普惠价"))
    pushed: list = []
    monkeypatch.setattr(run_mod, "_notify_feishu", lambda report, changed: pushed.append(list(changed)))

    run_mod.run_audit()
    assert len(pushed) == 1
    first_push_count = len(pushed[0])
    assert first_push_count >= 1

    # 第二次巡检：相同订单/同一价格，应去重不推送
    run_mod.run_audit()
    assert len(pushed) == 1, "状态应持久化，重复巡检不得再次推送"
    assert len(pushed[0]) == first_push_count


def test_failed_push_retries_on_next_audit(run_mod, monkeypatch) -> None:
    records = [
        {
            "platform": "京东", "product_id": "888888", "record_date": "2026-09-13",
            "full_time": "2026-09-13 10:00:00", "actual_puhui_price": 10.0,
        }
    ]
    monkeypatch.setattr(run_mod, "fetch_platform_records", lambda queries: records)
    monkeypatch.setattr(run_mod, "load_benchmarks", lambda *a, **k: ({("京东", "888888"): 15.0}, {}, "S促", "S促普惠价"))

    # 第一次巡检：模拟飞书推送失败（返回空列表）
    monkeypatch.setattr(run_mod, "_notify_feishu", lambda report, changed: [])
    run_mod.run_audit()

    # 状态文件不应将 888888 标记为已成功推送
    state = run_mod._load_alert_state()
    assert "京东|888888|跌破维护普惠价" not in state

    # 第二次巡检：飞书恢复，应该重新推送
    pushed: list = []
    monkeypatch.setattr(run_mod, "_notify_feishu", lambda report, changed: pushed.append(list(changed)) or list(changed))
    run_mod.run_audit()
    assert len(pushed) == 1
    state2 = run_mod._load_alert_state()
    assert "京东|888888|跌破维护普惠价" in state2


def test_cumulative_push_when_new_anomaly_occurs(run_mod, monkeypatch) -> None:
    """出现新异常时累计推送全部未恢复异常；无变化时去重不重复推送。"""
    records_run1 = [
        {"platform": "京东", "product_id": "111", "record_date": "2026-09-13",
         "full_time": "2026-09-13 10:00:00", "actual_puhui_price": 10.0}
    ]
    benchmarks = {
        ("京东", "111"): 15.0,
        ("京东", "222"): 20.0,
    }
    monkeypatch.setattr(run_mod, "load_benchmarks", lambda *a, **k: (benchmarks, {}, "S促", "S促普惠价"))
    pushed: list = []
    monkeypatch.setattr(run_mod, "_notify_feishu", lambda report, items: pushed.append(list(items)) or list(items))

    # 第 1 次巡检：只有 111 异常
    monkeypatch.setattr(run_mod, "fetch_platform_records", lambda q: records_run1)
    run_mod.run_audit()
    assert len(pushed) == 1
    assert [x["product_id"] for x in pushed[0]] == ["111"]
    assert [x["product_code"] for x in pushed[0]] == ["111"]

    # 第 2 次巡检：111 仍异常但价格未变，无新异常 -> 去重，不重复推送
    run_mod.run_audit()
    assert len(pushed) == 1

    # 第 3 次巡检：111 依然异常，且新增 222 异常 -> 累计推送 111 和 222
    records_run3 = [
        {"platform": "京东", "product_id": "111", "record_date": "2026-09-13",
         "full_time": "2026-09-13 10:00:00", "actual_puhui_price": 10.0},
        {"platform": "京东", "product_id": "222", "record_date": "2026-09-13",
         "full_time": "2026-09-13 10:00:00", "actual_puhui_price": 12.0},
    ]
    monkeypatch.setattr(run_mod, "fetch_platform_records", lambda q: records_run3)
    run_mod.run_audit()
    assert len(pushed) == 2
    assert sorted([x["product_id"] for x in pushed[1]]) == ["111", "222"]

    # 第 4 次巡检：111 和 222 均未变 -> 去重，不重复推送
    run_mod.run_audit()
    assert len(pushed) == 2




# ---------------------------------------------------------------------------
# P1：可配波动阈值
# ---------------------------------------------------------------------------


def test_fluct_tolerance_default(run_mod) -> None:
    """默认阈值 0.5，且可用于可配调优（P1 降噪需求）。"""
    assert run_mod.FLUCT_TOLERANCE == 0.5


def test_fluct_tolerance_suppresses_small_swings(run_mod, monkeypatch) -> None:
    """0.3 元小幅波动 < 默认 0.5 阈值 → 不产生波动告警（拼多多无基准场景降噪）。"""
    run_mod.FLUCT_TOLERANCE = 0.5
    pushed: list = []
    monkeypatch.setattr(
        run_mod,
        "fetch_platform_records",
        lambda queries: [
            {
                "platform": "拼多多", "product_id": "880", "record_date": "2026-09-13",
                "full_time": "2026-09-13 08:00:00", "actual_puhui_price": 50.0,
            },
            {
                "platform": "拼多多", "product_id": "880", "record_date": "2026-09-13",
                "full_time": "2026-09-13 09:00:00", "actual_puhui_price": 50.3,
            },
        ],
    )
    # 无基准 → 不会因「跌破」告警；只验证波动阈值拦截
    monkeypatch.setattr(run_mod, "load_benchmarks", lambda *a, **k: ({}, {}, "S促", "S促普惠价"))
    monkeypatch.setattr(run_mod, "_notify_feishu", lambda report, changed: pushed.append(list(changed)))

    run_mod.run_audit()
    assert pushed == [], "0.3 元波动低于 0.5 阈值，不应推送波动告警"


def test_fluct_tolerance_fires_large_swing(run_mod, monkeypatch) -> None:
    """2 元大幅波动 > 阈值 → 产生「同一天普惠价波动」告警。"""
    run_mod.FLUCT_TOLERANCE = 0.5
    pushed: list = []
    monkeypatch.setattr(
        run_mod,
        "fetch_platform_records",
        lambda queries: [
            {
                "platform": "京东", "product_id": "333333", "record_date": "2026-09-13",
                "full_time": "2026-09-13 08:00:00", "actual_puhui_price": 100.0,
            },
            {
                "platform": "京东", "product_id": "333333", "record_date": "2026-09-13",
                "full_time": "2026-09-13 09:00:00", "actual_puhui_price": 98.0,
            },
        ],
    )
    monkeypatch.setattr(run_mod, "load_benchmarks", lambda *a, **k: ({}, {}, "S促", "S促普惠价"))
    monkeypatch.setattr(run_mod, "_notify_feishu", lambda report, changed: pushed.append(list(changed)))

    run_mod.run_audit()
    assert len(pushed) == 1
    assert any(item.get("anomaly_type") == "同一天普惠价波动" for item in pushed[0])


# ---------------------------------------------------------------------------
# 去重 / 签名 / 平台键
# ---------------------------------------------------------------------------


def test_alert_signature_key_and_should_push(run_mod) -> None:
    item = {"platform": "京东", "product_id": "123456", "anomaly_type": "跌破维护普惠价", "current_price": "10.0"}
    sig = "|".join(run_mod._alert_signature(item))
    assert sig == "京东|123456|跌破维护普惠价"

    assert run_mod._should_push(item, {}) is True  # 新异动
    run_mod._persist_alert_state(run_mod._load_alert_state(), [item])
    saved = run_mod._load_alert_state()  # 从稳定目录重载，模拟跨 run 持久化
    assert run_mod._should_push(item, saved) is False  # 未变化不推送

    moved = dict(item, current_price="9.0")
    assert run_mod._should_push(moved, saved) is True  # 价格变化再推送


def test_persist_state_prunes_resolved_items(run_mod) -> None:
    push_item = {"platform": "唯品会", "product_id": "555", "anomaly_type": "同一天普惠价波动", "current_price": "30.0"}
    resolved = {"platform": "唯品会", "product_id": "666", "anomaly_type": "同一天普惠价波动", "current_price": "40.0"}
    run_mod._persist_alert_state({}, [push_item, resolved])
    saved = run_mod._load_alert_state()
    assert "唯品会|555|同一天普惠价波动" in saved
    # 本轮不再告警的异动应从状态移除，便于复发时按新异动重新推送
    run_mod._persist_alert_state({}, [push_item])
    saved2 = run_mod._load_alert_state()
    assert "唯品会|666|同一天普惠价波动" not in saved2


def test_platform_key_extraction(run_mod) -> None:
    assert run_mod._extract_platform_key("京东", "https://item.jd.com/9876543210.html") == "9876543210"
    assert run_mod._extract_platform_key("京东", "1234567") == "1234567"
    assert run_mod._extract_platform_key("抖音", "SKU_88") == "SKU_88"
    assert run_mod._extract_platform_key("京东", "") is None
    assert run_mod._extract_platform_key("京东", None) is None


# ---------------------------------------------------------------------------
# 跨天多记录去重与卡片级防护回归测试
# ---------------------------------------------------------------------------


def test_multiday_records_emit_single_latest_breach_anomaly(run_mod, monkeypatch) -> None:
    """同一商品在多天（如唯品会近3天）均跌破基准时，单次巡检必须且仅产生 1 条最新价预警。"""
    records = [
        {
            "platform": "唯品会", "product_id": "6920802685794496351", "record_date": "2026-09-12",
            "full_time": "2026-09-12 00:06:29", "actual_puhui_price": 254.0,
        },
        {
            "platform": "唯品会", "product_id": "6920802685794496351", "record_date": "2026-09-13",
            "full_time": "2026-09-13 00:07:00", "actual_puhui_price": 254.0,
        },
        {
            "platform": "唯品会", "product_id": "6920802685794496351", "record_date": "2026-09-14",
            "full_time": "2026-09-14 00:08:18", "actual_puhui_price": 254.0,
        },
    ]
    monkeypatch.setattr(run_mod, "fetch_platform_records", lambda queries: records)
    monkeypatch.setattr(
        run_mod,
        "load_benchmarks",
        lambda *a, **k: (
            {("唯品会", "6920802685794496351"): 278.61},
            {("唯品会", "6920802685794496351"): "透明质酸钠修复贴"},
            "S促",
            "S促普惠价",
        ),
    )
    pushed: list = []
    monkeypatch.setattr(run_mod, "_notify_feishu", lambda report, changed: pushed.append(list(changed)))

    run_mod.run_audit()
    assert len(pushed) == 1
    items = pushed[0]
    # 彻底杜绝老逻辑中因跨3天导致同商品重复生成3条的问题
    assert len(items) == 1
    assert items[0]["product_id"] == "6920802685794496351"
    assert items[0]["anomaly_type"] == "跌破维护普惠价"
    assert items[0]["current_price"] == 254.0
    assert items[0]["check_date"] == "2026-09-14"


def test_multiday_fluctuations_emit_single_latest_fluctuation_anomaly(run_mod, monkeypatch) -> None:
    """同一商品在多天均发生日内波动时，单次巡检仅产生 1 条最新发生日期的波动异动。"""
    records = [
        # 09-12 波动 (10 -> 20)
        {"platform": "抖音", "product_id": "999", "record_date": "2026-09-12", "full_time": "2026-09-12 08:00:00", "actual_puhui_price": 10.0},
        {"platform": "抖音", "product_id": "999", "record_date": "2026-09-12", "full_time": "2026-09-12 18:00:00", "actual_puhui_price": 20.0},
        # 09-13 波动 (30 -> 50)
        {"platform": "抖音", "product_id": "999", "record_date": "2026-09-13", "full_time": "2026-09-13 08:00:00", "actual_puhui_price": 30.0},
        {"platform": "抖音", "product_id": "999", "record_date": "2026-09-13", "full_time": "2026-09-13 18:00:00", "actual_puhui_price": 50.0},
    ]
    monkeypatch.setattr(run_mod, "fetch_platform_records", lambda queries: records)
    monkeypatch.setattr(run_mod, "load_benchmarks", lambda *a, **k: ({}, {}, "S促", "S促普惠价"))
    pushed: list = []
    monkeypatch.setattr(run_mod, "_notify_feishu", lambda report, changed: pushed.append(list(changed)))

    run_mod.run_audit()
    assert len(pushed) == 1
    items = pushed[0]
    assert len(items) == 1
    assert items[0]["anomaly_type"] == "同一天普惠价波动"
    assert items[0]["check_date"] == "2026-09-13"
    assert "2026-09-13 价格跳变" in items[0]["detail"]


def test_push_platform_card_deduplicates_in_card(run_mod, monkeypatch) -> None:
    """即使有重复元素传入卡片组装，卡片渲染层仍具有严格兜底去重能力。"""
    dupe_item = {
        "platform": "唯品会",
        "product_id": "111",
        "product_name": "面膜",
        "shop_name": "唯品会特卖旗舰店",
        "product_url": "https://detail.vip.com/item/111",
        "anomaly_type": "跌破维护普惠价",
        "current_price": 100.0,
        "benchmark_price": 120.0,
        "diff_amount": -20.0,
        "detail": "当前最新价已跌破",
    }
    sent_payloads: list = []

    class DummyResp:
        def __init__(self) -> None:
            self.status_code = 200
            self.headers = {"content-type": "application/json"}

        def json(self):
            return {"code": 0}

    monkeypatch.setattr("requests.post", lambda url, json, timeout: (sent_payloads.append(json), DummyResp())[1])

    # 传入 3 条完全一样的异动
    run_mod._push_platform_card(
        "http://dummy-webhook",
        "唯品会",
        [dupe_item, dupe_item, dupe_item],
        {"check_time": "2026-09-14 12:00:00", "current_promotion_stage": "S促"},
    )
    assert len(sent_payloads) == 1
    card = sent_payloads[0]["card"]
    assert card["schema"] == "2.0"
    assert card["header"]["title"]["content"] == "唯品会-普惠价破价预警-2026-09-14 12:00:00"
    assert "subtitle" not in card["header"]

    # CardKit 表格组件应包含 1 条去重后的异动行
    table = card["body"]["elements"][0]
    assert table["tag"] == "table"
    assert len(table["rows"]) == 1
    assert table["rows"][0]["shop_name"] == "唯品会特卖旗舰店"
    assert table["rows"][0]["product_name"] == "面膜"
    assert table["rows"][0]["product_code"] == "111"
    assert table["rows"][0]["product_url"] == "[查看商品](https://detail.vip.com/item/111)"
    assert table["rows"][0]["actual_price"] == "¥100.00"
    assert table["rows"][0]["benchmark_price"] == "¥120.00"
    assert table["rows"][0]["diff_amount"] == "-20.00"


def test_push_platform_cardkit_table_columns_and_styles(run_mod, monkeypatch) -> None:
    """验证 CardKit 2.0 表格结构完全符合 7 列规范与红色预警头。"""
    item = {
        "platform": "京东",
        "product_id": "999",
        "product_code": "999",
        "product_name": "胶原水",
        "shop_name": "京东自营店",
        "product_url": "https://item.jd.com/999.html",
        "anomaly_type": "跌破维护普惠价",
        "current_price": 50.0,
        "benchmark_price": 60.0,
        "diff_amount": -10.0,
    }
    sent_payloads: list = []

    class DummyResp:
        def __init__(self) -> None:
            self.status_code = 200
            self.headers = {"content-type": "application/json"}

        def json(self):
            return {"code": 0}

    monkeypatch.setattr("requests.post", lambda url, json, timeout: (sent_payloads.append(json), DummyResp())[1])

    run_mod._push_platform_card(
        "http://dummy-webhook",
        "京东",
        [item],
        {"check_time": "2026-09-16 10:00:00", "current_promotion_stage": "S促"},
    )
    card = sent_payloads[0]["card"]
    assert card["schema"] == "2.0"
    assert card["header"]["template"] == "red"
    assert card["header"]["title"]["content"] == "京东-普惠价破价预警-2026-09-16 10:00:00"
    assert "subtitle" not in card["header"]

    table = card["body"]["elements"][0]
    expected_col_names = [
        "shop_name",
        "product_name",
        "product_code",
        "product_url",
        "benchmark_price",
        "actual_price",
        "diff_amount",
    ]
    assert [c["name"] for c in table["columns"]] == expected_col_names
    expected_col_titles = ["店铺名", "产品昵称", "产品编码", "产品链接", "普惠锚定价", "实际执行价", "异动差值"]
    assert [c["display_name"] for c in table["columns"]] == expected_col_titles
    assert table["rows"][0]["product_code"] == "999"


def test_push_platform_card_uses_db_update_time_in_title(run_mod, monkeypatch) -> None:
    """卡片标题时间使用数据库中的更新时间而非当次巡检发送时间。"""
    item = {
        "platform": "抖音",
        "product_id": "888",
        "product_name": "修护贴",
        "db_update_time": "2026-09-16 14:00:39",
        "anomaly_type": "跌破维护普惠价",
        "current_price": 50.0,
        "benchmark_price": 60.0,
        "diff_amount": -10.0,
    }
    sent_payloads: list = []

    class DummyResp:
        def __init__(self) -> None:
            self.status_code = 200
            self.headers = {"content-type": "application/json"}

        def json(self):
            return {"code": 0}

    monkeypatch.setattr("requests.post", lambda url, json, timeout: (sent_payloads.append(json), DummyResp())[1])

    # report 的 check_time 为 14:43:12，但 platform_db_times 为数据库真实抓取更新时间 14:00:39
    run_mod._push_platform_card(
        "http://dummy-webhook",
        "抖音",
        [item],
        {
            "check_time": "2026-09-16 14:43:12",
            "platform_db_times": {"抖音": "2026-09-16 14:00:39"},
            "current_promotion_stage": "S促",
        },
    )
    card = sent_payloads[0]["card"]
    # 标题必须采用数据库更新时间 14:00:39，而不是发送时间 14:43:12
    assert card["header"]["title"]["content"] == "抖音-普惠价破价预警-2026-09-16 14:00:39"


def test_daily_reset_first_run_of_day_forces_push(run_mod, monkeypatch, tmp_path: Path) -> None:
    """跨天首次巡检：即使价格未变，也应强制推送完整异常清单。"""
    records = [
        {
            "platform": "京东", "product_id": "daily-reset-001", "record_date": "2026-09-15",
            "full_time": "2026-09-15 10:00:00", "actual_puhui_price": 10.0,
        }
    ]
    monkeypatch.setattr(run_mod, "fetch_platform_records", lambda queries: records)
    monkeypatch.setattr(run_mod, "load_benchmarks", lambda *a, **k: ({(("京东", "daily-reset-001")): 15.0}, {}, "S促", "S促普惠价"))
    pushed: list = []
    monkeypatch.setattr(run_mod, "_notify_feishu", lambda report, changed: (pushed.append(list(changed)), list(changed))[1])

    # 第一天：推送 1 次
    monkeypatch.setattr(run_mod, "_today_str", lambda: "2026-09-15")
    run_mod.run_audit()
    run_mod.run_audit()
    assert len(pushed) == 1, "同一天内重复巡检不应重复推送"

    # 第二天：日期变更，首次巡检应再次推送
    monkeypatch.setattr(run_mod, "_today_str", lambda: "2026-09-16")
    run_mod.run_audit()
    assert len(pushed) == 2, "跨天首次巡检应强制推送"

    # 第二天第二次：又去重了
    run_mod.run_audit()
    assert len(pushed) == 2, "同一天内第二次仍应去重"