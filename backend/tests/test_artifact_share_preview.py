from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from starlette.requests import Request as StarletteRequest

from app.api.chat import (
    ShareLinkRequest,
    mint_artifact_share,
    view_published_artifact,
)
from app.core.harness_session_cleanup import harness_task_workspace_path
from app.db.models import ChatSession, HarnessTaskFrameRecord, Message, Tenant, User
from app.harness import publish_harness_artifacts
from app.security import artifact_share as artifact_share_mod
from app.security import auth as security_auth
from app.security.auth import get_current_user_optional

HTML_BODY = (
    "<!doctype html><html><head><meta charset=\"utf-8\">"
    "<title>Quarterly Report</title></head><body><h1>Q3 看板</h1></body></html>"
)


def _test_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_artifact(
    db: Session,
    *,
    tenant_id: str = "tenant_demo",
    session_id: str = "session_demo",
    task_frame_id: str = "task_demo",
    artifact_path: str = "reports/report.html",
    display_name: str = "季度报告.html",
    body: str = HTML_BODY,
) -> User:
    db.add(Tenant(id=tenant_id, name="Demo"))
    user = User(
        id="user_owner",
        tenant_id=tenant_id,
        username="owner",
        password_hash="test",
    )
    db.add(user)
    db.add(
        ChatSession(
            id=session_id,
            tenant_id=tenant_id,
            user_id=user.id,
        )
    )
    db.add(
        HarnessTaskFrameRecord(
            id="htask_demo",
            tenant_id=tenant_id,
            session_id=session_id,
            source_turn_id="turn_demo",
            task_id=task_frame_id,
        )
    )
    db.add(
        Message(
            id="msg_assistant",
            tenant_id=tenant_id,
            session_id=session_id,
            role="assistant",
            content="文件已生成。",
            metadata_json={"harness_artifacts": []},
        )
    )
    db.commit()

    workspace = harness_task_workspace_path(
        tenant_id=tenant_id,
        session_id=session_id,
        task_frame_id=task_frame_id,
    )
    file_path = workspace / artifact_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(body, encoding="utf-8")
    published = publish_harness_artifacts(
        workspace,
        task_frame_id,
        [{"path": artifact_path}],
        operation="general_skill",
    )
    assistant = db.get(Message, "msg_assistant")
    assert assistant is not None
    assistant.metadata_json = {"harness_artifacts": published}
    db.add(assistant)
    db.commit()
    return user


def _add_intruder(db: Session, *, tenant_id: str = "tenant_demo") -> User:
    intruder = User(
        id="user_intruder",
        tenant_id=tenant_id,
        username="intruder",
        password_hash="test",
    )
    db.add(intruder)
    db.commit()
    return intruder


def _mint_token(
    *,
    tenant_id: str = "tenant_demo",
    session_id: str = "session_demo",
    task_frame_id: str = "task_demo",
    path: str = "reports/report.html",
    ttl_seconds: int = artifact_share_mod.ARTIFACT_SHARE_TTL_SECONDS,
) -> str:
    return artifact_share_mod.mint_artifact_share_token(
        tenant_id=tenant_id,
        session_id=session_id,
        task_frame_id=task_frame_id,
        path=path,
        ttl_seconds=ttl_seconds,
    )


def _handcraft_token(payload: dict) -> str:
    """Sign an arbitrary payload with the real secret to reach decode checks."""
    body = security_auth._b64(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    return f"{body}.{security_auth._sign(body)}"


def _share_link_payload() -> ShareLinkRequest:
    return ShareLinkRequest(
        tenant_id="tenant_demo",
        session_id="session_demo",
        task_frame_id="task_demo",
        path="reports/report.html",
    )


async def _read_response_body(response) -> bytes:
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
    if response.background is not None:
        await response.background()
    return b"".join(chunks)


def test_inline_preview_serves_html_with_sandbox_csp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        _seed_artifact(db)
        token = _mint_token()

        response = view_published_artifact(token, current_user=None, db=db)

        assert asyncio.run(_read_response_body(response)) == HTML_BODY.encode("utf-8")
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert response.headers["content-disposition"].startswith(
            'inline; filename="report.html"; filename*=UTF-8'
        )
        csp = response.headers["content-security-policy"]
        assert "sandbox" in csp
        assert "allow-scripts" in csp
        assert "allow-same-origin" not in csp
        assert "https:" in csp
        assert response.headers["x-frame-options"] == "SAMEORIGIN"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cross-origin-resource-policy"] == "same-origin"
        assert "no-store" in response.headers["cache-control"]
        assert response.headers["etag"].startswith('"sha256:')


def test_share_token_expired_returns_404(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        _seed_artifact(db)
        token = _mint_token(ttl_seconds=-10)

        with pytest.raises(HTTPException) as expired:
            view_published_artifact(token, current_user=None, db=db)
        assert expired.value.status_code == 404


def test_tampered_signature_returns_404(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        _seed_artifact(db)
        token = _mint_token()
        tampered = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")

        with pytest.raises(HTTPException) as invalid:
            view_published_artifact(tampered, current_user=None, db=db)
        assert invalid.value.status_code == 404


def test_cross_tenant_token_returns_404(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        _seed_artifact(db)
        # Properly signed, but pointing at a tenant that has no such frame.
        token = _mint_token(tenant_id="tenant_other")

        with pytest.raises(HTTPException) as missing:
            view_published_artifact(token, current_user=None, db=db)
        assert missing.value.status_code == 404


def test_non_html_extension_served_as_attachment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        _seed_artifact(
            db,
            artifact_path="reports/chart.svg",
            display_name="chart.svg",
            body="<svg xmlns='http://www.w3.org/2000/svg'/>",
        )
        token = _mint_token(path="reports/chart.svg")

        response = view_published_artifact(token, current_user=None, db=db)

        assert asyncio.run(_read_response_body(response))
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["content-disposition"].startswith("attachment; ")
        assert "content-security-policy" not in response.headers


def test_sha256_mismatch_returns_409(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        _seed_artifact(db)
        workspace = harness_task_workspace_path(
            tenant_id="tenant_demo",
            session_id="session_demo",
            task_frame_id="task_demo",
        )
        (workspace / "reports" / "report.html").write_text(
            "<html>tampered</html>", encoding="utf-8"
        )
        token = _mint_token()

        with pytest.raises(HTTPException) as changed:
            view_published_artifact(token, current_user=None, db=db)
        assert changed.value.status_code == 409


def test_traversal_path_in_signed_token_returns_404(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        _seed_artifact(db)
        token = _handcraft_token(
            {
                "type": artifact_share_mod.ARTIFACT_SHARE_CLAIM,
                "tenant_id": "tenant_demo",
                "session_id": "session_demo",
                "task_frame_id": "task_demo",
                "path": "../../secret.html",
                "iat": int(time.time()),
                "exp": int(time.time()) + 600,
            }
        )

        with pytest.raises(HTTPException) as traversal:
            view_published_artifact(token, current_user=None, db=db)
        assert traversal.value.status_code == 404


def test_authenticated_view_enforces_session_visibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_artifact(db)
        intruder = _add_intruder(db)
        token = _mint_token()

        with pytest.raises(HTTPException) as forbidden:
            view_published_artifact(token, current_user=intruder, db=db)
        assert forbidden.value.status_code == 404

        response = view_published_artifact(token, current_user=user, db=db)
        assert asyncio.run(_read_response_body(response)) == HTML_BODY.encode("utf-8")


def test_invalid_bearer_does_not_downgrade_to_anonymous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        _seed_artifact(db)

        bad_request = StarletteRequest(
            {
                "type": "http",
                "headers": [(b"authorization", b"Bearer not.a.valid-token")],
            }
        )
        with pytest.raises(HTTPException) as invalid:
            get_current_user_optional(bad_request, db=db)
        assert invalid.value.status_code == 401

        anonymous_request = StarletteRequest({"type": "http", "headers": []})
        assert get_current_user_optional(anonymous_request, db=db) is None


def test_mint_requires_published_artifact_and_visibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_artifact(db)
        intruder = _add_intruder(db)

        with pytest.raises(HTTPException) as forbidden:
            mint_artifact_share(_share_link_payload(), current_user=intruder, db=db)
        assert forbidden.value.status_code == 404

        with pytest.raises(HTTPException) as unpublished:
            mint_artifact_share(
                ShareLinkRequest(
                    tenant_id="tenant_demo",
                    session_id="session_demo",
                    task_frame_id="task_demo",
                    path="unpublished.txt",
                ),
                current_user=user,
                db=db,
            )
        assert unpublished.value.status_code == 404

        minted = mint_artifact_share(_share_link_payload(), current_user=user, db=db)
        assert minted.token
        decoded = artifact_share_mod.decode_artifact_share_token(minted.token)
        assert decoded["path"] == "reports/report.html"
        assert decoded["tenant_id"] == "tenant_demo"
        assert (
            minted.expires_at
            == pytest.approx(int(time.time()) + artifact_share_mod.ARTIFACT_SHARE_TTL_SECONDS, abs=5)
        )


def test_mint_returns_tool_base_url_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        _seed_artifact(db)

        minted = mint_artifact_share(
            _share_link_payload(),
            current_user=db.get(User, "user_owner"),
            db=db,
        )

        base = security_auth.get_settings().normalized_tool_base_url
        assert minted.url == f"{base}/api/chat/artifacts/view/{minted.token}"
        # The token must be URL-path safe.
        quote(minted.token, safe="")


def test_view_rejects_login_token_as_share_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_artifact(db)
        # A valid *login* token must not open artifacts: wrong claim type.
        login_token = security_auth.create_access_token(user)

        with pytest.raises(HTTPException) as wrong_type:
            view_published_artifact(login_token, current_user=None, db=db)
        assert wrong_type.value.status_code == 404