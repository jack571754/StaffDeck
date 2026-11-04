"""全店销售独立页的取数、展示和分享回归。"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.reporting.dashboard_shops import fetch_task_all_shops


def _shop_connector(monkeypatch, rows, *, error=None):
    from app.data_query import connectors, service

    template = SimpleNamespace(
        query_content="WITH h1 AS (SELECT 1), ss_sale_sata AS (SELECT 2) SELECT 3",
        data_source_id="source", params_json=[], status="active",
    )
    source = SimpleNamespace(status="active", tenant_id="tenant")
    calls = []

    def execute(sql, params, **kwargs):
        calls.append(sql)
        if error:
            raise error
        return SimpleNamespace(rows=rows)

    monkeypatch.setattr(service, "get_query_template", lambda *a: template)
    monkeypatch.setattr(service, "get_data_source", lambda *a: source)
    monkeypatch.setattr(connectors, "get_connector", lambda *a: SimpleNamespace(
        execute=execute, close=lambda: None,
    ))
    task = SimpleNamespace(tenant_id="tenant", pipeline_steps_json=[
        {"type": "query", "template_id": "sales"},
    ])
    return task, calls, source


def test_shop_query_includes_zero_sales_refunds_and_shop_identity(monkeypatch):
    task, calls, _ = _shop_connector(monkeypatch, [
        {"item": "天猫-同名店铺", "platform": "天猫", "shop_id": "1",
         "shop_name": "同名店铺", "净销_万": 0, "运营净销_万": 0,
         "达播净销_万": 0, "updated_at": datetime(2026, 11, 2, 10, 2)},
        {"item": "京东-同名店铺", "platform": "京东", "shop_id": "2",
         "shop_name": "同名店铺", "净销_万": -3, "运营净销_万": -2,
         "达播净销_万": -1, "updated_at": datetime(2026, 11, 2, 9, 58)},
    ])
    shops = fetch_task_all_shops(None, task)
    assert len(shops) == 2
    assert shops[0]["platform"] == "天猫"
    assert shops[0]["updated_at"] == "2026-11-02T10:02:00+08:00"
    assert shops[1]["shop_id"] == "2"
    assert shops[1]["net"] == -3
    assert shops[1]["ops"] == -2
    assert shops[1]["live"] == -1
    assert "WHERE 销售金额 > 0" not in calls[0]
    assert "s.platform, s.shop_key" in calls[0]


def test_missing_shop_amount_does_not_become_zero(monkeypatch):
    task, _, _ = _shop_connector(monkeypatch, [
        {"item": "店铺", "净销_万": None, "运营净销_万": None, "达播净销_万": None},
    ])
    shops = fetch_task_all_shops(None, task)
    assert shops is not None
    assert shops[0]["net"] is None
    assert shops[0]["ops"] is None
    assert shops[0]["updated_at"] is None


@pytest.mark.parametrize("rows,error,expected", [
    ([], None, []),
    ([], TimeoutError("测试取数超时"), None),
    ([{}] * 10000, None, None),
])
def test_shop_query_distinguishes_empty_failure_and_row_limit(monkeypatch, rows, error, expected):
    task, _, _ = _shop_connector(monkeypatch, rows, error=error)
    assert fetch_task_all_shops(None, task) == expected


def test_disabled_source_cannot_supply_shop_sales(monkeypatch):
    task, calls, source = _shop_connector(monkeypatch, [])
    source.status = "disabled"
    assert fetch_task_all_shops(None, task) is None
    assert calls == []


def test_page_renders_all_shops_splits_and_source_times():
    from app.reporting.shop_sales_report import render_shop_sales_report

    shops = [
        {"name": f"天猫-店铺{i}", "platform": "天猫", "shop_name": f"店铺{i}",
         "net": i, "ops": i - 1, "live": 1,
         "updated_at": "2026-11-02T10:02:00+08:00"}
        for i in range(25)
    ]
    shops.append({"name": '<img src=x onerror="alert(1)">', "net": -3,
                  "ops": None, "live": 0, "updated_at": None})
    page = render_shop_sales_report(shops, generated_at=datetime(2026, 11, 2, 3, tzinfo=UTC))
    assert page.count('data-shop-row="true"') == 26
    assert 'data-net="24"' in page
    assert page.index('data-net="24"') < page.index('data-net="0"')
    assert "运营净销" in page and "达播净销" in page and "合计净销" in page
    assert "2026-11-02 10:02:00" in page
    assert "2026-11-02 11:00:00" in page
    assert '<img src=x' not in page
    assert '&lt;img src=x' in page
    assert "未提供" in page
    assert "-3.00" in page and "0.00" in page
    assert 'data-refresh-seconds="60"' in page


def test_page_empty_result_does_not_fabricate_sales():
    from app.reporting.shop_sales_report import render_shop_sales_report

    page = render_shop_sales_report([], generated_at=datetime(2026, 11, 2, tzinfo=UTC))
    assert "暂无店铺销售数据" in page
    assert 'data-shop-row="true"' not in page


def test_page_marks_stale_source_independently_from_generation_time():
    from app.reporting.shop_sales_report import render_shop_sales_report

    page = render_shop_sales_report([
        {"name": "旧快照", "net": 3, "ops": 2, "live": 1,
         "updated_at": "2026-11-02T08:00:00+08:00"},
    ], generated_at=datetime(2026, 11, 2, 2, tzinfo=UTC))
    assert "数据可能延迟" in page
    assert "2026-11-02 08:00:00" in page


@pytest.fixture
def report_context(tmp_path, monkeypatch):
    import hashlib

    from app.core.harness_session_cleanup import harness_reports_root
    from app.db.models import (
        ArtifactShareLink,
        ChatSession,
        HarnessTaskFrameRecord,
        Message,
        ScheduledTask,
        User,
    )
    from app.db.utils import utc_now
    from app.reporting.link import mint_report_share_link

    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(User(id="owner", tenant_id="tenant", username="owner", password_hash="test"))
        db.add(ChatSession(id="session", tenant_id="tenant", user_id="owner"))
        db.add(HarnessTaskFrameRecord(id="frame", tenant_id="tenant", session_id="session",
                                     source_turn_id="turn", task_id="task_frame"))
        db.commit()
        root = harness_reports_root(tenant_id="tenant", session_id="session",
                                    owner_kind="harness_frame", owner_id="task_frame", db=db)
        root.mkdir(parents=True, exist_ok=True)
        (root / "dashboard.html").write_text("<html>原大屏</html>", encoding="utf-8")
        path = "reports/dashboard.html"
        share = mint_report_share_link(tenant_id="tenant", session_id="session",
                                       owner_kind="harness_frame", owner_id="task_frame", path=path)
        from datetime import timedelta

        anchor = ArtifactShareLink(
            id="anchor", tenant_id="tenant", session_id="session", owner_kind="harness_frame",
            owner_id="task_frame", path=path, token=share.token, token_hash="anchor-hash",
            url=share.url, created_by_user_id="owner", expires_at=utc_now() + timedelta(days=7),
        )
        artifact = {"type": "workspace_file", "path": path, "task_frame_id": "task_frame",
                    "owner_kind": "harness_frame", "owner_id": "task_frame",
                    "sha256": hashlib.sha256((root / "dashboard.html").read_bytes()).hexdigest(),
                    "size": (root / "dashboard.html").stat().st_size}
        message = Message(tenant_id="tenant", session_id="session", role="assistant",
                          content="原大屏", metadata_json={"harness_artifacts": [artifact]})
        task = ScheduledTask(id="sales", tenant_id="tenant", agent_id="agent",
                             created_by_user_id="owner", title="电商实时销售数据", prompt="",
                             status="paused", metadata_json={"artifact_share_link_id": "anchor",
                                                              "shop_sales_report_enabled": True})
        db.add_all([anchor, message, task])
        db.commit()
        yield SimpleNamespace(db=db, task=task, anchor=anchor, root=root, message=message)
    engine.dispose()


def test_sync_publishes_reusable_page_and_valid_share(report_context):
    from app.core.artifact_owners import resolve_artifact_owner
    from app.db.models import ArtifactShareLink
    from app.reporting.shop_sales_syncer import sync_task_shop_sales_report

    ctx = report_context
    url = sync_task_shop_sales_report(ctx.db, ctx.task, shops=[
        {"name": "店铺A", "net": 3, "ops": 2, "live": 1},
    ])
    assert url
    link_id = ctx.task.metadata_json["shop_sales_share_link_id"]
    link = ctx.db.get(ArtifactShareLink, link_id)
    resolved = resolve_artifact_owner(ctx.db, owner_kind=link.owner_kind, tenant_id=link.tenant_id,
                                      session_id=link.session_id, owner_id=link.owner_id,
                                      path=link.path)
    assert resolved is not None
    assert "店铺A" in (resolved.workspace_root / link.path).read_text(encoding="utf-8")
    assert ctx.task.status == "paused"
    sync_task_shop_sales_report(ctx.db, ctx.task, shops=[])
    assert ctx.task.metadata_json["shop_sales_share_link_id"] == link_id
    assert len(ctx.db.exec(select(ArtifactShareLink)).all()) == 2
    assert "暂无店铺销售数据" in (resolved.workspace_root / link.path).read_text(encoding="utf-8")
    assert (ctx.root / "dashboard.html").read_text(encoding="utf-8") == "<html>原大屏</html>"


def test_failed_query_keeps_previous_successful_page(report_context, monkeypatch):
    from app.reporting import dashboard_shops
    from app.reporting.shop_sales_syncer import sync_task_shop_sales_report

    ctx = report_context
    first = sync_task_shop_sales_report(ctx.db, ctx.task, shops=[{"name": "上次成功", "net": 8}])
    files = {p: p.read_bytes() for p in ctx.root.glob("*.html")}
    monkeypatch.setattr(dashboard_shops, "fetch_task_all_shops", lambda *a: None)
    assert sync_task_shop_sales_report(ctx.db, ctx.task) == first
    assert all(p.read_bytes() == data for p, data in files.items())


@pytest.mark.parametrize("invalid", ["revoked", "tenant", "owner"])
def test_invalid_shop_share_cannot_be_recreated_or_updated(report_context, invalid):
    from app.db.models import ArtifactShareLink
    from app.reporting.shop_sales_syncer import sync_task_shop_sales_report

    ctx = report_context
    assert sync_task_shop_sales_report(ctx.db, ctx.task, shops=[])
    link = ctx.db.get(ArtifactShareLink, ctx.task.metadata_json["shop_sales_share_link_id"])
    if invalid == "revoked":
        link.is_revoked = True
    elif invalid == "tenant":
        link.tenant_id = "other"
    else:
        link.owner_id = "missing"
    ctx.db.add(link)
    ctx.db.commit()
    files = {p: p.read_bytes() for p in ctx.root.glob("*.html")}
    assert sync_task_shop_sales_report(ctx.db, ctx.task, shops=[{"name": "不应写入"}]) is None
    assert len(ctx.db.exec(select(ArtifactShareLink)).all()) == 2
    assert all(p.read_bytes() == data for p, data in files.items())


def test_page_uses_existing_share_route_after_update_and_revocation(report_context):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.chat import router
    from app.db import get_session
    from app.db.models import ArtifactShareLink
    from app.reporting.shop_sales_syncer import sync_task_shop_sales_report

    ctx = report_context
    url = sync_task_shop_sales_report(ctx.db, ctx.task, shops=[{"name": "第一版", "net": 1}])
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: ctx.db
    path = "/api/chat/artifacts/view/" + url.rsplit("/", 1)[1]
    with TestClient(app) as client:
        response = client.get(path)
        assert response.status_code == 200
        assert "第一版" in response.text
        assert "sandbox allow-scripts" in response.headers["content-security-policy"]
        sync_task_shop_sales_report(ctx.db, ctx.task, shops=[{"name": "最新数据", "net": 20}])
        latest = client.get(path)
        assert latest.status_code == 200
        assert "最新数据" in latest.text and "第一版" not in latest.text
        link = ctx.db.get(ArtifactShareLink, ctx.task.metadata_json["shop_sales_share_link_id"])
        link.is_revoked = True
        ctx.db.add(link)
        ctx.db.commit()
        assert client.get(path).status_code == 403


def test_refresh_script_defaults_to_checking_without_changes(report_context, capsys):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "refresh_shop_sales_report.py"
    spec = importlib.util.spec_from_file_location("refresh_shops", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.engine = report_context.db.get_bind()
    before = dict(report_context.task.metadata_json)
    assert module.main([report_context.task.id]) == 0
    report_context.db.refresh(report_context.task)
    assert report_context.task.metadata_json == before
    assert '"mode": "check"' in capsys.readouterr().out


@pytest.mark.parametrize("invalid", ["missing_binding", "missing_creator", "not_enabled"])
def test_report_requires_explicit_binding_creator_and_enablement(report_context, invalid):
    from app.db.models import ArtifactShareLink, User
    from app.reporting.shop_sales_syncer import sync_task_shop_sales_report

    ctx = report_context
    meta = dict(ctx.task.metadata_json)
    if invalid == "missing_binding":
        meta.pop("artifact_share_link_id")
    elif invalid == "missing_creator":
        ctx.db.add(User(id="other_admin", tenant_id="tenant", username="other_admin",
                        password_hash="test", role="admin"))
        ctx.task.created_by_user_id = "不存在的创建者"
    else:
        meta.pop("shop_sales_report_enabled")
    ctx.task.metadata_json = meta
    ctx.db.add(ctx.task)
    ctx.db.commit()
    assert sync_task_shop_sales_report(ctx.db, ctx.task, shops=[]) is None
    assert len(ctx.db.exec(select(ArtifactShareLink)).all()) == 1


def test_rotated_shop_share_old_token_is_revoked_with_current_link(report_context, monkeypatch):
    import time
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.chat import router
    from app.db import get_session
    from app.db.models import ArtifactShareLink
    from app.db.utils import utc_now
    from app.reporting.shop_sales_syncer import sync_task_shop_sales_report
    from app.scheduled_tasks.service import resolve_task_share_link_url

    ctx = report_context
    old_url = sync_task_shop_sales_report(ctx.db, ctx.task, shops=[])
    link = ctx.db.get(ArtifactShareLink, ctx.task.metadata_json['shop_sales_share_link_id'])
    link.expires_at = utc_now() + timedelta(minutes=30)
    ctx.db.add(link)
    ctx.db.commit()
    future = time.time() + 3600
    with monkeypatch.context() as context:
        context.setattr('app.security.artifact_share.time.time', lambda: future)
        new_url = resolve_task_share_link_url(ctx.db, ctx.task, metadata_key='shop_sales_share_link_id')
    assert new_url != old_url
    link.is_revoked = True
    ctx.db.add(link)
    ctx.db.commit()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: ctx.db
    with TestClient(app) as client:
        response = client.get('/api/chat/artifacts/view/' + old_url.rsplit('/', 1)[1])
    assert response.status_code == 403


def test_page_reload_cannot_trigger_source_query(report_context, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.chat import router
    from app.db import get_session
    from app.reporting import dashboard_shops
    from app.reporting.shop_sales_syncer import sync_task_shop_sales_report

    ctx = report_context
    url = sync_task_shop_sales_report(ctx.db, ctx.task, shops=[{'name': '已有快照', 'net': 3}])
    calls = []

    def fetch(*args):
        calls.append(True)
        return []

    monkeypatch.setattr(dashboard_shops, 'fetch_task_all_shops', fetch)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: ctx.db
    with TestClient(app) as client:
        response = client.get('/api/chat/artifacts/view/' + url.rsplit('/', 1)[1] + '?refresh=1')
    assert response.status_code == 200
    assert calls == []
    assert '已有快照' in response.text


def test_page_renders_delay_badges_for_all_tiers():
    from app.reporting.shop_sales_report import render_shop_sales_report

    gen = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)  # 18:00 Beijing
    shops = [
        {"name": "极速店", "platform": "天猫", "shop_name": "极速店", "net": 10,
         "updated_at": "2026-11-02T18:00:00+08:00"},  # 0 min
        {"name": "正常店", "platform": "天猫", "shop_name": "正常店", "net": 5,
         "updated_at": "2026-11-02T17:55:00+08:00"},  # 5 min
        {"name": "预警店", "platform": "京东", "shop_name": "预警店", "net": 3,
         "updated_at": "2026-11-02T17:40:00+08:00"},  # 20 min
        {"name": "超时店", "platform": "抖音", "shop_name": "超时店", "net": 1,
         "updated_at": "2026-11-02T17:15:00+08:00"},  # 45 min
        {"name": "无时间店", "platform": "快手", "shop_name": "无时间店", "net": 0,
         "updated_at": None},
    ]
    html = render_shop_sales_report(shops, generated_at=gen)
    assert 'class="row-delay delay-good">延迟 <1 分钟</span>' in html
    assert 'class="row-delay delay-good">延迟 5 分钟</span>' in html
    assert 'class="row-delay delay-warn">延迟 20 分钟</span>' in html
    assert 'class="row-delay delay-danger">延迟 45 分钟</span>' in html
    assert 'class="row-delay delay-missing">时间未提供</span>' in html


