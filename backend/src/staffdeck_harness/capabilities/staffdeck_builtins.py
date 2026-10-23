"""Custom StaffDeck capabilities behind the v3 host policy and invocation ledger."""
from staffdeck_harness.contracts.errors import PermissionDenied
from staffdeck_harness.contracts.invocation import ModuleResult
from staffdeck_harness.contracts.security import ResourceRef
from staffdeck_harness.contracts.sources import ResourceDescriptor

NAMES = frozenset({"data_query_search", "data_query_execute", "report_generate"})


def descriptor(context, name):
    from app.core.capability_manifest import _internal_capability_descriptors
    cap = next(c for c in _internal_capability_descriptors() if c.name == name)
    return ResourceDescriptor(
        ref=ResourceRef(type="tool", id=name, tenant_id=context.tenant_id,
                        attributes={"binding_status": "active", "private_to_agent": True}),
        name=name, operation="tool.invoke/v1", description=cap.description,
        input_schema=cap.input_schema, side_effecting=name == "report_generate",
        replayable=name != "report_generate", metadata={"tool_type": "builtin"})


def invoke(host, inv):
    from app.core.capability_manifest import _internal_capability_descriptors
    from app.core.harness_capability_invoker import HarnessCapabilityInvoker
    from app.core.task_request_compiler import CapabilityManifest
    from app.db.models import ChatSession
    ctx = inv.context
    session = host.db.get(ChatSession, ctx.session_id)
    if session is None or (session.tenant_id, session.user_id) != (ctx.tenant_id, ctx.user_id):
        raise PermissionDenied("不能使用其他用户会话的数据查询或报告能力")
    cap = next(c for c in _internal_capability_descriptors() if c.name == inv.binding_id)
    executor = HarnessCapabilityInvoker(
        host.db, tenant_id=ctx.tenant_id, session=session, task_frame_id=ctx.task_frame_id,
        model_config=host.model_config, manifest=CapabilityManifest(available=[cap]),
        active_skill=None, active_step_id=None,
        agent_id=None if ctx.agent_id.endswith(":overall") else ctx.agent_id,
        run_id=ctx.run_id, trace_sink=host.trace,
        scheduled_task_id=ctx.scheduled_task_id,
        ensure_execution_lease=lambda: host.fence.check(host.slot))
    result = executor.invoke(inv.binding_id, {k: v for k, v in inv.arguments.items() if k != "tool_id"})
    return ModuleResult(success=bool(result.get("success")), data=result.get("data"),
                        error=result.get("error"), artifacts=tuple(result.get("artifacts") or ()))
