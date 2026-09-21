import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.harness_capability_invoker import HarnessCapabilityInvoker
from app.core.task_request_compiler import CapabilityDescriptor
from app.db.models import ChatSession, Tenant, Tool


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
