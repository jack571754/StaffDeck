"""开源技能市场（skills.sh 注册表数据源）集成单测。

覆盖 backend/app/api/general_skills.py 中 3 个市场端点：
GET /market/items · GET /market/skills/{slug}/preview · POST /market/install

数据源 = skills.sh 搜索 API（/api/search，空关键字用预设词合成热门榜），
安装/预览 = skills.sh 下载代理（/api/download，返回内联全部文件的 JSON）。

网络统一以 monkeypatch 打桩（_market_json / _download_url），
目录归一、slug 去重、解包、入库、权限守卫走真实实现。
GitHub 目录加载器（手动导入端点仍用）的测试保留在文件后部。
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.api.general_skills as gs
from app.api.general_skills import (
    skill_market_install,
    skill_market_items,
    skill_market_preview,
)
from app.db.models import AgentProfile, AgentResourceBinding, GeneralSkill, ModelConfig, Tenant, User
from app.security.auth import hash_password
from app.security.encryption import encrypt_secret

SKILL_MD = """\
---
name: docx 文档助手
description: 创建、编辑和分析 Word 文档
---

# docx 助手

常用 Word 文档操作。
"""


@pytest.fixture(autouse=True)
def _clear_market_cache():
    # 市场目录/包缓存是模块级全局状态，测试间必须隔离
    gs._market_cache.clear()
    yield
    gs._market_cache.clear()


def _test_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _seed_minimal_tenant(db: Session) -> None:
    db.add(Tenant(id="tenant_demo", name="Demo"))
    db.add(
        User(
            id="user_demo",
            tenant_id="tenant_demo",
            username="user_demo",
            password_hash=hash_password("demo"),
        )
    )
    db.add(
        ModelConfig(
            tenant_id="tenant_demo",
            name="Fake model",
            api_key_encrypted=encrypt_secret("test-key"),
            model="fake",
            is_default=True,
            enabled=True,
        )
    )
    db.commit()


def _admin_user() -> User:
    return User(id="user_admin", tenant_id="tenant_demo", username="admin", role="admin")


# 搜索 API 返回结构：{id: "owner/repo/path", skillId, name, installs, source}
_SEARCH_DOCX = {
    "skills": [
        {
            "id": "anthropics/skills/docx",
            "skillId": "docx",
            "name": "docx",
            "installs": 188510,
            "source": "anthropics/skills",
        },
        {
            "id": "nexu-io/open-design/docx",
            "skillId": "docx",
            "name": "docx",
            "installs": 2673,
            "source": "nexu-io/open-design",
        },
        {
            "id": "vercel/skills/git",
            "skillId": "git",
            "name": "git",
            "installs": 95200,
            "source": "vercel/skills",
        },
    ]
}

_SEARCH_PDF = {
    "skills": [
        {
            "id": "anthropics/skills/pdf",
            "skillId": "pdf",
            "name": "pdf",
            "installs": 120000,
            "source": "anthropics/skills",
        }
    ]
}


def _stub_market_json(
    monkeypatch, searches: dict[str, object] | None = None, *, fail: bool = False
) -> None:
    """按 URL 中的 q 参数分发打桩 _market_json；fail=True 模拟搜索接口不可用。"""
    data = searches if searches is not None else {"docx": _SEARCH_DOCX}

    def fake_json(url: str) -> object:
        assert url.startswith(f"{gs.SKILLS_SH_BASE_URL}/api/search?"), url
        if fail:
            return None
        for key, value in data.items():
            if f"q={key}&" in url:
                return value
        return {"skills": []}

    monkeypatch.setattr(gs, "_market_json", fake_json)


def _download_payload(markdown: str = SKILL_MD) -> bytes:
    """skills.sh 下载代理返回的整包 JSON（files 内联全部内容）。"""
    return json.dumps(
        {
            "files": [
                {"path": "SKILL.md", "contents": markdown},
                {"path": "run.py", "contents": "print('hi')"},
            ],
            "hash": "a" * 64,
        }
    ).encode("utf-8")


def _stub_skills_sh_download(monkeypatch, markdown: str = SKILL_MD):
    """打桩 _download_url，仅放行 skills.sh 下载代理，记录调用 URL。"""
    captured: dict[str, object] = {}

    def fake_download(url: str) -> tuple[bytes, str]:
        captured["url"] = url
        assert url.startswith(f"{gs.SKILLS_SH_BASE_URL}/api/download/"), url
        return _download_payload(markdown), "application/json"

    monkeypatch.setattr(gs, "_download_url", fake_download)
    return captured


# ---------------------------------------------------------------------------
# GET /market/items
# ---------------------------------------------------------------------------


def test_market_items_preset_catalog_merges_dedupes_and_sorts(monkeypatch) -> None:
    # 空关键字：每个预设词各搜一次，同 id 去重，按安装量降序
    _stub_market_json(monkeypatch, {"docx": _SEARCH_DOCX, "pdf": _SEARCH_PDF})
    with _test_session() as db:
        _seed_minimal_tenant(db)
        result = skill_market_items(
            tenant_id="tenant_demo", source="all", db=db, current_user=_admin_user()
        )
        assert result["source"] == "all"
        slugs = [item["slug"] for item in result["items"]]
        # 188510 > 120000 > 95200 > 2673，docx 两处来源各自保留（slug 不同）
        assert slugs == ["docx", "pdf", "git", "nexu-io-docx"]
        by_slug = {item["slug"]: item for item in result["items"]}
        docx = by_slug["docx"]
        assert docx["name"] == "docx"
        assert docx["origin"] == "skills_sh"
        assert docx["installed"] is False
        # 描述带来源仓库，分类展示安装量
        assert docx["description"] == "GitHub: anthropics/skills"
        assert docx["category"] == "18.9万次安装"


def test_market_items_slug_collision_prefixed_with_owner(monkeypatch) -> None:
    # 两条搜索结果叶子名相同（docx）→ 第二条加 owner 前缀
    _stub_market_json(monkeypatch)
    with _test_session() as db:
        _seed_minimal_tenant(db)
        result = skill_market_items(
            tenant_id="tenant_demo", source="all", db=db, current_user=_admin_user()
        )
        by_slug = {item["slug"]: item for item in result["items"]}
        assert by_slug["docx"]["description"] == "GitHub: anthropics/skills"
        assert by_slug["nexu-io-docx"]["description"] == "GitHub: nexu-io/open-design"


def test_market_items_search_forwards_query(monkeypatch) -> None:
    _stub_market_json(monkeypatch, {"pdf": _SEARCH_PDF})
    with _test_session() as db:
        _seed_minimal_tenant(db)
        result = skill_market_items(
            tenant_id="tenant_demo", source="all", q="pdf", db=db, current_user=_admin_user()
        )
        assert [item["slug"] for item in result["items"]] == ["pdf"]


def test_market_items_unknown_source_raises_400() -> None:
    with _test_session() as db:
        _seed_minimal_tenant(db)
        with pytest.raises(HTTPException) as exc:
            skill_market_items(
                tenant_id="tenant_demo", source="official", db=db, current_user=_admin_user()
            )
        assert exc.value.status_code == 400
        assert "未知的市场来源" in exc.value.detail


def test_market_items_search_failure_returns_empty(monkeypatch) -> None:
    _stub_market_json(monkeypatch, fail=True)
    with _test_session() as db:
        _seed_minimal_tenant(db)
        result = skill_market_items(
            tenant_id="tenant_demo", source="all", db=db, current_user=_admin_user()
        )
        assert result["total"] == 0
        assert result["items"] == []


def test_market_items_installed_flag(monkeypatch) -> None:
    _stub_market_json(monkeypatch)
    with _test_session() as db:
        _seed_minimal_tenant(db)
        db.add(
            GeneralSkill(
                tenant_id="tenant_demo",
                slug="docx",
                name="已有 docx",
                skill_markdown="# x",
                skill_files_json=[],
                status="published",
            )
        )
        db.commit()
        result = skill_market_items(
            tenant_id="tenant_demo", source="all", db=db, current_user=_admin_user()
        )
        by_slug = {item["slug"]: item for item in result["items"]}
        assert by_slug["docx"]["installed"] is True
        assert by_slug["git"]["installed"] is False


def test_market_json_refuses_non_whitelist_host_without_network() -> None:
    # 非白名单域名在发起网络请求前即被拦截，返回 None
    assert gs._market_json("https://evil.example/data/skills.json") is None


def test_market_endpoints_hit_real_constants() -> None:
    assert gs.SKILLS_SH_BASE_URL == "https://skills.sh"
    assert gs.MARKET_DATASET_HOSTS == {"skills.sh"}
    assert gs.MARKET_SOURCES == ("all", "installed")


# ---------------------------------------------------------------------------
# GET /market/skills/{slug}/preview
# ---------------------------------------------------------------------------


def test_market_preview_resolves_slug_via_search_cache(monkeypatch) -> None:
    # 先搜索（写入 slug -> 下载地址缓存），再预览：URL 应指向 skills.sh 下载代理
    _stub_market_json(monkeypatch)
    captured = _stub_skills_sh_download(monkeypatch)
    with _test_session() as db:
        _seed_minimal_tenant(db)
        skill_market_items(
            tenant_id="tenant_demo", source="all", q="docx", db=db, current_user=_admin_user()
        )
        result = skill_market_preview(slug="docx", db=db, current_user=_admin_user())
        assert captured["url"] == f"{gs.SKILLS_SH_BASE_URL}/api/download/anthropics/skills/docx"
        assert result["slug"] == "docx"
        assert result["name"] == "docx 文档助手"
        assert result["markdown"].startswith("---")
        assert "SKILL.md" in result["files"]
        assert "run.py" in result["files"]


def test_skills_sh_search_skips_malformed_ids(monkeypatch) -> None:
    # id 必须 ≥3 段且各段为安全字符（防 URL 路径穿越）；畸形条目整体跳过
    malformed = {
        "skills": [
            {"id": "only-two/segments", "name": "a"},
            {"id": "a/b/../..", "name": "b"},
            {"id": "ok/repo/name", "name": "c"},
            "not-a-dict",
        ]
    }
    _stub_market_json(monkeypatch, {"docx": malformed})
    entries = gs._skills_sh_search("docx")
    assert [entry["slug"] for entry in entries] == ["name"]


def test_market_preview_unknown_slug_raises_404(monkeypatch) -> None:
    # 目录与 slug 缓存都没有该技能 → 404
    _stub_market_json(monkeypatch, fail=True)
    with _test_session() as db:
        _seed_minimal_tenant(db)
        with pytest.raises(HTTPException) as exc:
            skill_market_preview(slug="nope", db=db, current_user=_admin_user())
        assert exc.value.status_code == 404


def test_load_skills_sh_package_requires_skill_md(monkeypatch) -> None:
    payload = json.dumps({"files": [{"path": "README.md", "contents": "# hi"}]}).encode("utf-8")

    def fake_download(url: str) -> tuple[bytes, str]:
        return payload, "application/json"

    monkeypatch.setattr(gs, "_download_url", fake_download)
    with pytest.raises(HTTPException) as exc:
        gs._load_skills_sh_package(f"{gs.SKILLS_SH_BASE_URL}/api/download/a/b/c")
    assert exc.value.status_code == 400


def test_load_skills_sh_package_converts_files(monkeypatch) -> None:
    payload = json.dumps(
        {
            "files": [
                {"path": "/skills/SKILL.md", "contents": SKILL_MD},
                {"path": "", "contents": "无路径应跳过"},
                {"path": ".git/config", "contents": "应被过滤"},
                {"path": "big.bin", "contents": "x" * (gs.MAX_CLAWHUB_FILE_BYTES + 1)},
            ]
        }
    ).encode("utf-8")

    def fake_download(url: str) -> tuple[bytes, str]:
        return payload, "application/json"

    monkeypatch.setattr(gs, "_download_url", fake_download)
    files = gs._load_skills_sh_package(f"{gs.SKILLS_SH_BASE_URL}/api/download/a/b/c")
    assert [f.path for f in files] == ["skills/SKILL.md"]
    assert files[0].content == SKILL_MD
    assert files[0].mime_type == "text/markdown"


def test_market_preview_prefers_local_database(monkeypatch) -> None:
    # 模拟网络如果被调用就报错，以此验证本地已安装技能优先读库且零网络
    def _fail_loader(url: str, visited=None):  # type: ignore[no-untyped-def]
        raise AssertionError("本地技能不应触发远端下载！")

    monkeypatch.setattr(gs, "_load_remote_skill_source", _fail_loader)

    with _test_session() as db:
        _seed_minimal_tenant(db)
        skill = GeneralSkill(
            tenant_id="tenant_demo",
            slug="local-reporter",
            name="本地报表生成器",
            description="本地生成的审计周报技能",
            skill_markdown="---\nname: 本地报表生成器\nversion: 2.0.0\n---\n# 本地文档预览内容",
            skill_files_json=[
                {"path": "SKILL.md", "content": "# 本地文档预览内容", "size": 10},
                {"path": "report.py", "content": "print('ok')", "size": 15},
            ],
            status="published",
        )
        db.add(skill)
        db.commit()

        preview = skill_market_preview(
            slug="local-reporter",
            tenant_id="tenant_demo",
            db=db,
            current_user=_admin_user(),
        )
        assert preview["slug"] == "local-reporter"
        assert preview["name"] == "本地报表生成器"
        assert "# 本地文档预览内容" in preview["markdown"]
        assert "SKILL.md" in preview["files"]
        assert "report.py" in preview["files"]


# ---------------------------------------------------------------------------
# POST /market/install
# ---------------------------------------------------------------------------


def test_market_install_into_plaza_and_dedup_slug(monkeypatch) -> None:
    _stub_market_json(monkeypatch)
    _stub_skills_sh_download(monkeypatch)
    with _test_session() as db:
        _seed_minimal_tenant(db)

        first = skill_market_install(
            gs.SkillMarketInstallRequest(tenant_id="tenant_demo", slug="docx"),
            db,
            _admin_user(),
        )
        assert first.slug == "docx"
        assert first.name == "docx 文档助手"
        assert first.metadata.get("import_source") == "skills-sh:docx"

        second = skill_market_install(
            gs.SkillMarketInstallRequest(tenant_id="tenant_demo", slug="docx"),
            db,
            _admin_user(),
        )
        assert second.slug == "docx-2"  # 重复 slug 追加后缀，不覆盖

        rows = db.exec(
            select(GeneralSkill).where(GeneralSkill.tenant_id == "tenant_demo")
        ).all()
        assert {row.slug for row in rows} == {"docx", "docx-2"}


def test_market_install_private_agent_binding(monkeypatch) -> None:
    _stub_market_json(monkeypatch)
    _stub_skills_sh_download(monkeypatch)
    with _test_session() as db:
        _seed_minimal_tenant(db)
        db.add(
            AgentProfile(
                id="agent_a", tenant_id="tenant_demo", name="专属员工", is_overall=False
            )
        )
        db.commit()
        row = skill_market_install(
            gs.SkillMarketInstallRequest(tenant_id="tenant_demo", slug="docx", agent_id="agent_a"),
            db,
            _admin_user(),
        )
        assert row.metadata.get("owner_agent_id") == "agent_a"
        assert row.slug == "docx"


def test_market_install_invalid_slug_raises_400(monkeypatch) -> None:
    _stub_market_json(monkeypatch)
    with _test_session() as db:
        _seed_minimal_tenant(db)
        with pytest.raises(HTTPException) as exc:
            skill_market_install(
                gs.SkillMarketInstallRequest(tenant_id="tenant_demo", slug="../evil"),
                db,
                _admin_user(),
            )
        assert exc.value.status_code == 400


def test_market_install_unknown_slug_raises_404(monkeypatch) -> None:
    _stub_market_json(monkeypatch, fail=True)
    with _test_session() as db:
        _seed_minimal_tenant(db)
        with pytest.raises(HTTPException) as exc:
            skill_market_install(
                gs.SkillMarketInstallRequest(tenant_id="tenant_demo", slug="nope"),
                db,
                _admin_user(),
            )
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# 已安装技能 (source="installed") 过滤与本地预览
# ---------------------------------------------------------------------------


def test_market_items_installed_empty() -> None:
    with _test_session() as db:
        _seed_minimal_tenant(db)
        res = skill_market_items(
            source="installed",
            tenant_id="tenant_demo",
            db=db,
            current_user=_admin_user(),
        )
        assert res["source"] == "installed"
        assert res["total"] == 0
        assert res["items"] == []


def test_market_items_installed_returns_installed_skills() -> None:
    with _test_session() as db:
        _seed_minimal_tenant(db)
        skill1 = GeneralSkill(
            tenant_id="tenant_demo",
            slug="excel-helper",
            name="Excel 处理专家",
            description="自动分析 Excel 表格数据",
            skill_markdown="---\nname: Excel 处理专家\nversion: 1.2.0\n---\n# Doc",
            skill_files_json=[{"path": "SKILL.md", "content": "# Doc", "size": 5}],
            metadata_json={"category": "office", "stars": 10},
            status="published",
            capability_scope="general",
        )
        skill2 = GeneralSkill(
            tenant_id="tenant_demo",
            slug="sql-runner",
            name="SQL 执行器",
            description="执行 SQL 查询并导出结果",
            skill_markdown="---\nname: SQL 执行器\nversion: 0.9.0\n---\n# Doc",
            skill_files_json=[{"path": "SKILL.md", "content": "# Doc", "size": 5}],
            metadata_json={"category": "dev", "stars": 25},
            status="draft",
            capability_scope="sop_specific",
        )
        db.add(skill1)
        db.add(skill2)
        db.commit()

        # 全部已安装
        res = skill_market_items(
            source="installed",
            tenant_id="tenant_demo",
            db=db,
            current_user=_admin_user(),
        )
        assert res["source"] == "installed"
        assert res["total"] == 2
        items = res["items"]
        slugs = [item["slug"] for item in items]
        assert "excel-helper" in slugs
        assert "sql-runner" in slugs
        excel_item = next(it for it in items if it["slug"] == "excel-helper")
        assert excel_item["name"] == "Excel 处理专家"
        assert excel_item["version"] == "1.2.0"
        assert excel_item["status"] == "published"
        assert excel_item["capability_scope"] == "general"
        assert excel_item["installed"] is True
        assert excel_item["files_count"] == 1

        # 搜索过滤
        res_search = skill_market_items(
            source="installed",
            q="sql",
            tenant_id="tenant_demo",
            db=db,
            current_user=_admin_user(),
        )
        assert res_search["total"] == 1
        assert res_search["items"][0]["slug"] == "sql-runner"


# ---------------------------------------------------------------------------
# GitHub 目录列举：tree 网页内嵌 JSON 通道（api.github.com 403/不可达时的主通道）
# ---------------------------------------------------------------------------


def _tree_page_html(items: list[dict]) -> str:
    payload = json.dumps({"payload": {"tree": {"items": items}}})
    return (
        "<html><body><script type=\"application/json\" "
        f"data-target=\"react-app.embeddedData\">{payload}</script>"
        "</body></html>"
    )


def test_github_tree_html_listing_parses_and_recurses(monkeypatch) -> None:
    pages = {
        "skills/demo": _tree_page_html(
            [{"name": "sub", "path": "skills/demo/sub", "contentType": "directory"}]
        ),
        "skills/demo/sub": _tree_page_html(
            [
                {"name": "SKILL.md", "path": "skills/demo/sub/SKILL.md", "contentType": "file"},
                {"name": "run.py", "path": "skills/demo/sub/run.py", "contentType": "file"},
            ]
        ),
    }

    def fake_download(url: str) -> tuple[bytes, str]:
        assert "api.github.com" not in url, "列举不应请求 api.github.com"
        # URL 形如 .../tree/<branch>/<path>，去掉 branch 段得到页面路径
        clean = url.split("/tree/", 1)[1].split("?")[0].strip("/").split("/", 1)[1]
        return pages[clean].encode("utf-8"), "text/html"

    monkeypatch.setattr(gs, "_download_url", fake_download)
    result = gs._list_github_tree_via_html("anbeime", "skill", "main", "skills/demo")
    assert sorted(result) == ["skills/demo/sub/SKILL.md", "skills/demo/sub/run.py"]


def test_github_tree_html_listing_raises_on_unparsable_page(monkeypatch) -> None:
    monkeypatch.setattr(
        gs, "_download_url", lambda url: (b"<html>no payload here</html>", "text/html")
    )
    with pytest.raises(HTTPException):
        gs._list_github_tree_via_html("anbeime", "skill", "main", "skills/demo")


def test_download_github_directory_prefers_html_listing_and_raw_files(monkeypatch) -> None:
    pages = {
        "skills/demo": _tree_page_html(
            [{"name": "SKILL.md", "path": "skills/demo/SKILL.md", "contentType": "file"}]
        ),
    }

    def fake_download(url: str) -> tuple[bytes, str]:
        assert "api.github.com" not in url, "不应回退到 api.github.com"
        assert "archive" not in url, "不应回退到整仓 zip"
        if "/tree/" in url:
            clean = url.split("/tree/", 1)[1].split("?")[0].strip("/").split("/", 1)[1]
            return pages[clean].encode("utf-8"), "text/html"
        assert url.startswith("https://raw.githubusercontent.com/anbeime/skill/main/")
        if url.endswith("SKILL.md"):
            return SKILL_MD.encode("utf-8"), "text/markdown"
        raise AssertionError(f"意外请求: {url}")

    monkeypatch.setattr(gs, "_download_url", fake_download)
    files = gs._download_github_directory_contents("anbeime", "skill", "main", "skills/demo")
    assert [f.path for f in files] == ["SKILL.md"]
    assert files[0].content == SKILL_MD
    assert files[0].mime_type == "text/markdown"


def test_download_files_via_raw_downloads_all_and_preserves_order(monkeypatch) -> None:
    contents = {
        "skills/demo/SKILL.md": SKILL_MD,
        "skills/demo/run.py": "print('a')\n",
        "skills/demo/refs/guide.md": "# guide\n",
    }

    def fake_download(url: str) -> tuple[bytes, str]:
        for path, text in contents.items():
            if url.endswith(path):
                return text.encode("utf-8"), "text/markdown"
        raise AssertionError(f"意外请求: {url}")

    monkeypatch.setattr(gs, "_download_url", fake_download)
    files = gs._download_files_via_raw(
        "anbeime", "skill", "main", list(contents), "skills/demo"
    )
    # 输入顺序即输出顺序，全部下载
    assert [f.path for f in files] == ["SKILL.md", "run.py", "refs/guide.md"]
    assert files[2].content == "# guide\n"


def test_load_market_package_caches_result(monkeypatch) -> None:
    # 同一技能预览后再点安装，不应触发第二次远端下载
    calls: list[str] = []

    def fake_download(url: str) -> tuple[bytes, str]:
        calls.append(url)
        return _download_payload(), "application/json"

    monkeypatch.setattr(gs, "_download_url", fake_download)
    monkeypatch.setattr(
        gs,
        "_market_install_url_for",
        lambda slug: f"{gs.SKILLS_SH_BASE_URL}/api/download/anthropics/skills/demo",
    )
    first = gs._load_market_package("demo")
    second = gs._load_market_package("demo")
    assert len(calls) == 1
    assert [f.path for f in first] == [f.path for f in second]


def test_download_files_via_raw_retries_transient_errors(monkeypatch) -> None:
    calls: list[str] = []

    def flaky_download(url: str) -> tuple[bytes, str]:
        calls.append(url)
        if len(calls) == 1:
            raise gs.URLError("transient reset")
        return SKILL_MD.encode("utf-8"), "text/markdown"

    monkeypatch.setattr(gs, "_download_url", flaky_download)
    files = gs._download_files_via_raw(
        "anbeime", "skill", "main", ["skills/demo/SKILL.md"], "skills/demo"
    )
    assert len(calls) == 2  # 首次瞬态失败后重试成功
    assert [f.path for f in files] == ["SKILL.md"]


def test_download_files_via_raw_gives_up_after_retries(monkeypatch) -> None:
    attempts: list[int] = []

    def always_failing(url: str) -> tuple[bytes, str]:
        attempts.append(1)
        raise gs.URLError("down")

    monkeypatch.setattr(gs, "_download_url", always_failing)
    with pytest.raises(HTTPException) as exc:
        gs._download_files_via_raw(
            "anbeime", "skill", "main", ["skills/demo/SKILL.md"], "skills/demo"
        )
    assert len(attempts) == 2
    assert exc.value.status_code == 400


def test_download_github_directory_falls_back_to_api_on_html_failure(monkeypatch) -> None:
    def fake_download(url: str) -> tuple[bytes, str]:
        if "/tree/" in url:
            return b"<html>no payload</html>", "text/html"
        raise AssertionError(f"意外请求: {url}")

    monkeypatch.setattr(gs, "_download_url", fake_download)

    def fake_json(api_url: str) -> list[dict]:
        assert "api.github.com" in api_url
        return [
            {
                "type": "file",
                "path": "skills/demo/SKILL.md",
                "size": len(SKILL_MD),
                "download_url": "https://raw.githubusercontent.com/anbeime/skill/main/skills/demo/SKILL.md",
            }
        ]

    monkeypatch.setattr(gs, "_download_json", fake_json)
    monkeypatch.setattr(
        gs,
        "_download_url",
        lambda url: (SKILL_MD.encode("utf-8"), "text/markdown"),  # noqa: ARG005
    )
    files = gs._download_github_directory_contents("anbeime", "skill", "main", "skills/demo")
    assert [f.path for f in files] == ["SKILL.md"]
    assert files[0].content == SKILL_MD


def test_market_items_installed_excludes_deleted_and_respects_agent_scope() -> None:
    with _test_session() as db:
        _seed_minimal_tenant(db)
        overall_agent = AgentProfile(
            id="agent_overall",
            tenant_id="tenant_demo",
            name="企业员工整体",
            is_overall=True,
        )
        emp_agent = AgentProfile(
            id="agent_emp_1",
            tenant_id="tenant_demo",
            name="销售专员",
            is_overall=False,
        )
        db.add(overall_agent)
        db.add(emp_agent)

        skill_active = GeneralSkill(
            id="gs_1",
            tenant_id="tenant_demo",
            slug="active-skill",
            name="活跃技能",
            skill_markdown="# 活跃技能",
            status="published",
        )
        skill_deleted = GeneralSkill(
            id="gs_2",
            tenant_id="tenant_demo",
            slug="deleted-skill",
            name="已删技能",
            skill_markdown="# 已删技能",
            status="published",
        )
        skill_private = GeneralSkill(
            id="gs_3",
            tenant_id="tenant_demo",
            slug="private-skill",
            name="私有技能",
            skill_markdown="# 私有技能",
            status="published",
            metadata_json={"owner_agent_id": "agent_emp_1", "scope": "agent_private"},
        )
        db.add(skill_active)
        db.add(skill_deleted)
        db.add(skill_private)
        db.commit()

        db.add(
            AgentResourceBinding(
                id="bind_1",
                tenant_id="tenant_demo",
                agent_id="agent_overall",
                resource_type="general_skill",
                resource_id="gs_1",
                status="active",
            )
        )
        db.add(
            AgentResourceBinding(
                id="bind_2",
                tenant_id="tenant_demo",
                agent_id="agent_overall",
                resource_type="general_skill",
                resource_id="gs_2",
                status="deleted",
            )
        )
        db.add(
            AgentResourceBinding(
                id="bind_3",
                tenant_id="tenant_demo",
                agent_id="agent_emp_1",
                resource_type="general_skill",
                resource_id="gs_3",
                status="active",
            )
        )
        db.commit()

        # 广场查询：不应包含已删技能与他人私有技能
        gallery_res = skill_market_items(
            source="installed",
            tenant_id="tenant_demo",
            db=db,
            current_user=_admin_user(),
        )
        gallery_slugs = [item["slug"] for item in gallery_res["items"]]
        assert "active-skill" in gallery_slugs
        assert "deleted-skill" not in gallery_slugs
        assert "private-skill" not in gallery_slugs

        # 员工查询：包含 active-skill 和 private-skill，不包含已删技能
        emp_res = skill_market_items(
            source="installed",
            tenant_id="tenant_demo",
            agent_id="agent_emp_1",
            db=db,
            current_user=_admin_user(),
        )
        emp_slugs = [item["slug"] for item in emp_res["items"]]
        assert "active-skill" in emp_slugs
        assert "private-skill" in emp_slugs
        assert "deleted-skill" not in emp_slugs
