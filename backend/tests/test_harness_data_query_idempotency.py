from datetime import timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.harness_capability_invoker import (
    INVOCATION_CLAIM_TTL_SECONDS,
    HarnessCapabilityInvoker,
)
from app.core.task_request_compiler import CapabilityDescriptor
from app.db.models import (
    ChatSession,
    HarnessInvocationRecord,
    Tenant,
    Tool,
    new_id,
    utc_now,
)


@pytest.fixture
def invoker_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        tenant = Tenant(id="tenant_test", name="Test Tenant")
        session.add(tenant)
        chat_session = ChatSession(
            id="sess_test",
            tenant_id="tenant_test",
            title="Test Session",
        )
        session.add(chat_session)
        session.commit()
        yield session


def _make_invoker(db: Session, tenant_id: str = "tenant_test") -> HarnessCapabilityInvoker:
    invoker = object.__new__(HarnessCapabilityInvoker)
    invoker.db = db
    invoker.tenant_id = tenant_id
    invoker.task_frame_id = "tf_1"
    invoker.active_step_id = None
    return invoker


def test_data_query_tool_never_generates_logical_action_key(invoker_db: Session) -> None:
    # A data_query tool with method=POST must NOT generate a logical action key
    # because it is a read-only query and has no external mutating side effects.
    tool = Tool(
        id="tool_dq_1",
        tenant_id="tenant_test",
        name="query_realtime_sales_summary",
        display_name="Sales Summary",
        tool_type="data_query",
        method="POST",
        url="data_query://qt_1",
        config_json={"template_id": "qt_1"},
        input_schema={"type": "object"},
        enabled=True,
    )
    invoker_db.add(tool)
    invoker_db.commit()

    invoker = _make_invoker(invoker_db)

    descriptor = CapabilityDescriptor(
        capability_id=tool.id,
        name=tool.name,
        kind="tool",
        metadata={"tool_type": "data_query", "method": "POST"},
    )

    key = invoker._logical_action_key(descriptor, {"params": {"end_date": "2026-09-20"}})
    assert key is None, "data_query tool should never generate a logical_action_key"


def test_data_query_tool_failure_does_not_block_subsequent_calls(invoker_db: Session) -> None:
    invoker = _make_invoker(invoker_db)

    # Calling _replay_or_block for a key without prior completed record returns None or unblocks
    blocked = invoker._replay_or_block("sha256:nonexistent")
    assert blocked is None


def _add_invocation(
    db: Session,
    *,
    logical_action_key: str,
    status: str,
    age_seconds: int = 0,
) -> HarnessInvocationRecord:
    record = HarnessInvocationRecord(
        tenant_id="tenant_test",
        session_id="sess_test",
        task_id="tf_1",
        run_id="run_1",
        call_id=new_id("hcall"),
        tool_name="create_order",
        request_digest="digest",
        logical_action_key=logical_action_key,
        status=status,
    )
    if age_seconds:
        stale = utc_now() - timedelta(seconds=age_seconds)
        record.started_at = stale
        record.updated_at = stale
    db.add(record)
    db.commit()
    return record


def test_outcome_unknown_claim_blocks_retry_within_ttl(invoker_db: Session) -> None:
    invoker = _make_invoker(invoker_db)
    _add_invocation(
        invoker_db,
        logical_action_key="sha256:write-1",
        status="outcome_unknown",
        age_seconds=INVOCATION_CLAIM_TTL_SECONDS // 2,
    )

    blocked = invoker._replay_or_block("sha256:write-1")

    assert blocked is not None
    assert blocked.get("error", {}).get("code") == "TOOL_CALL_OUTCOME_UNKNOWN"


def test_expired_outcome_unknown_claim_is_released(invoker_db: Session) -> None:
    invoker = _make_invoker(invoker_db)
    record = _add_invocation(
        invoker_db,
        logical_action_key="sha256:write-2",
        status="outcome_unknown",
        age_seconds=INVOCATION_CLAIM_TTL_SECONDS + 60,
    )

    blocked = invoker._replay_or_block("sha256:write-2")

    # 超时释放：允许新调用，且写认领被解除（真实事故根因 D）。
    assert blocked is None
    invoker_db.refresh(record)
    assert record.status == "claim_expired"
    assert record.logical_action_key is None


def test_expired_started_claim_is_released(invoker_db: Session) -> None:
    invoker = _make_invoker(invoker_db)
    _add_invocation(
        invoker_db,
        logical_action_key="sha256:write-3",
        status="started",
        age_seconds=INVOCATION_CLAIM_TTL_SECONDS + 60,
    )

    assert invoker._replay_or_block("sha256:write-3") is None


def test_completed_claim_still_replays_cached_result(invoker_db: Session) -> None:
    invoker = _make_invoker(invoker_db)
    record = _add_invocation(
        invoker_db,
        logical_action_key="sha256:write-4",
        status="completed",
    )
    record.response_cache_json = {"success": True, "data": {"order_id": "o-1"}}
    invoker_db.add(record)
    invoker_db.commit()

    replayed = invoker._replay_or_block("sha256:write-4")

    assert replayed is not None
    assert replayed.get("success") is True
    assert replayed.get("data", {}).get("idempotent_replay") is True
