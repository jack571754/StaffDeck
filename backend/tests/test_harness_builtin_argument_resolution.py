"""内建工具(internal/file)参数中的 sandbox_json_file 句柄解析回归测试。

背景:query_realtime_sales_summary 等外部工具的大结果(>2000 字符)会被
_persist_large_json_result 落盘并以 {"kind": "sandbox_json_file", ...} 句柄
形式返回给模型。句柄自动解析此前只挂在外部工具路径(_invoke_external_tool),
LLM 把句柄 dict 塞进 run_skill_script 的 argv 时,Pydantic 严格模型
(argv: list[str]) 校验失败报 INVALID_ARGUMENTS。本组测试锁定:
1. 大结果句柄必须附带 usage 使用说明(模型可自解释地正确使用);
2. _resolve_json_tool_result_references 对 argv 这类 array-of-string 的
   item schema 场景应把句柄解析为 JSON 字符串;
3. invoke() 全链对 file 类内建工具的参数先行解析,句柄不再导致 schema 拒绝。
"""

import json
from pathlib import Path

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.capability_manifest import CapabilityManifestBuilder
from app.core.harness_capability_invoker import HarnessCapabilityInvoker
from app.db.models import ChatSession, ModelConfig
from app.harness.skill_script import RunSkillScriptArguments


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _chat_session() -> ChatSession:
    return ChatSession(id="session-handle", tenant_id="tenant-demo")


def _model_config() -> ModelConfig:
    return ModelConfig(
        id="model-test",
        tenant_id="tenant-demo",
        name="测试模型",
        api_key_encrypted="test",
        model="test-model",
    )


def _big_rows() -> list[dict]:
    """形状贴合销售播报查询结果且序列化后 > 2000 字符。"""

    return [
        {
            "category": "大盘",
            "item": f"店铺{i}",
            "net_sales": 1000 + i,
            "deal_amount": 1200 + i,
            "refund_amount": 200 + i,
            "note": "x" * 40,
        }
        for i in range(24)
    ]


def _build_invoker(tmp_path: Path, monkeypatch) -> HarnessCapabilityInvoker:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _engine()
    db = Session(engine)
    manifest = CapabilityManifestBuilder(db).build("tenant-demo", None, None, None)
    return HarnessCapabilityInvoker(
        db,
        tenant_id="tenant-demo",
        session=_chat_session(),
        task_frame_id="task-handle",
        model_config=_model_config(),
        manifest=manifest,
        active_skill=None,
        active_step_id=None,
        agent_id=None,
    )


def test_persist_large_json_result_reference_carries_usage_hint(
    tmp_path, monkeypatch
) -> None:
    invoker = _build_invoker(tmp_path, monkeypatch)
    stored = invoker._persist_large_json_result(
        {"success": True, "data": {"rows": _big_rows()}},
        call_id="hcall_usage",
    )

    assert stored["success"] is True
    reference = stored["data"]
    assert reference["kind"] == "sandbox_json_file"
    usage = str(reference.get("usage") or "")
    assert "read_file" in usage, "usage 应指引模型用 read_file 读取完整内容"
    assert reference["sandbox_path"] in usage, "usage 应包含可复制的沙箱路径"


def test_resolve_json_handle_inside_argv_array_becomes_json_string(
    tmp_path, monkeypatch
) -> None:
    invoker = _build_invoker(tmp_path, monkeypatch)
    expected = {"rows": _big_rows()}
    stored = invoker._persist_large_json_result(
        {"success": True, "data": expected},
        call_id="hcall_argv",
    )
    handle = stored["data"]

    resolved = invoker._resolve_json_tool_result_references(
        {"argv": ["--text", handle]},
        schema=RunSkillScriptArguments.model_json_schema(),
    )

    assert isinstance(resolved["argv"][1], str), "argv item 应解析为字符串"
    assert json.loads(resolved["argv"][1]) == expected
    assert resolved["argv"][0] == "--text"


def test_invoke_resolves_handle_in_builtin_file_tool_arguments(
    tmp_path, monkeypatch
) -> None:
    invoker = _build_invoker(tmp_path, monkeypatch)
    expected = {"rows": _big_rows()}
    stored = invoker._persist_large_json_result(
        {"success": True, "data": expected},
        call_id="hcall_invoke",
    )
    handle = stored["data"]

    result = invoker.invoke(
        "write_file",
        {"path": "echo.json", "content": handle},
    )

    assert result["success"] is True, result
    written = (Path(invoker.workspace_root) / "echo.json").read_text(encoding="utf-8")
    assert json.loads(written) == expected
