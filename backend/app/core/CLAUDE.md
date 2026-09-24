[根目录](../../../CLAUDE.md) > [backend](../../) > [app](../) > **core**

# core — Harness v2 运行时内核

## 模块职责

StaffDeck Agent 运行时的内核（Harness v2）：把一条 `ChatTurnRequest` 变成 `ChatTurnResponse`。负责 Turn 规划、TaskFrame 持久化与租约、能力授权清单（capability manifest）与渐进披露、单任务 Agent 循环、能力调用与重放、上下文投影/压缩、取消与恢复、反射、人工接管、slash 命令。

**边界**：`core/` 是“调度 + 决策 + 授权”层；真正的沙箱执行在 `../harness/`（executor/sandbox/command/skill_script），能力契约定义在 `../capabilities/`，数据表在 `../db/models.py`。改执行语义去 `harness/`，改契约/注册去 `capabilities/`。

## 入口与主链路

| 环节 | 文件 / 符号 |
|---|---|
| 引擎入口 | `harness_v2_engine.py` → `HarnessV2Engine.run(request)`（`__init__` 注入 owner 上下文；`close()` 释放） |
| Turn 规划 | `turn_planner.py` → `TurnPlanner`、`turn_plan_router_decision(plan)`（提示词 `app/llm/prompts/turn_planner_prompt.md`，`SCHEMA_REPAIR_ATTEMPTS=1`） |
| Frame 持久化 | `task_frame_store.py` → `TaskFrameStore`、`planned_frame_from_record(row)`；表 `harness_task_frames`（`FRAME_LEASE_SECONDS=900`，`MAX_TASK_FRAMES_PER_TURN=8`，`TERMINAL_FRAME_STATUSES={completed,cancelled,failed}`） |
| 任务编译 | `task_request_compiler.py` → `TaskRequestCompiler.compile(...)` 产出 `TaskRequirement`；`TaskExecutionResult` 是执行结果契约 |
| 能力清单 | `capability_manifest.py` → `CapabilityManifestBuilder.build(tenant_id, agent_id, skill, step_id)`；`RESERVED_HARNESS_CAPABILITY_NAMES` |
| 清单投影 | `capability_discovery.py` → `project_capability_manifest(...)`、`search_capability_descriptors(...)`；`ALWAYS_EXPANDED_CAPABILITIES`、`CAPABILITY_CATALOG_BUDGET_CHARS=8000` |
| 单任务 Agent | `harness_agent.py` → `HarnessTaskAgent`、`HarnessAction`（`MAX_SUCCESSFUL_KNOWLEDGE_SEARCHES_PER_TASK=2`） |
| 能力调用 | `harness_capability_invoker.py` → `HarnessCapabilityInvoker.invoke(name, arguments)`（重放/审计/大结果句柄） |
| Turn 收口 | `turn_finalizer.py` → `TurnFinalizer`；回复生成 `response_generator.py` → `ResponseGenerator` |
| 取消 | `cancellation.py` → `cancel_chat_turn` / `is_chat_turn_cancelled` / `clear_chat_turn_cancelled` |
| 恢复 | `harness_recovery.py` → `recover_orphan_harness_runs`、`start_harness_recovery_sweeper`（`SWEEP_INTERVAL_SECONDS=60`） |

主链路（自上而下）：`HarnessV2Engine.run` → `TurnPlanner` 出 `TurnPlan` → `TaskFrameStore` 落 `HarnessTaskFrameRecord` → 逐 frame `TaskRequestCompiler.compile` → `CapabilityManifestBuilder.build` + `project_capability_manifest` → `HarnessTaskAgent` 循环 → `HarnessCapabilityInvoker.invoke` → `TaskExecutionResult` → `TurnFinalizer` → `ChatTurnResponse`。

## 关键机制与不变量

- **渐进披露（progressive disclosure）**：服务端 `CapabilityManifest` 是**冻结的完整授权快照**；`project_capability_manifest` 只裁剪模型可见的上下文（扩展开 `ALWAYS_EXPANDED_CAPABILITIES` 内核能力 + SOP 显式引用，其余进 8K 预算目录），**绝不新增授权**。想改“模型能看到哪些能力 schema”改这里；想改“能力是否存在/可用”改 `capability_manifest.py`。
- **保留能力名**：`RESERVED_HARNESS_CAPABILITY_NAMES`（含 `capability_search`/`capability_describe`/`exec_command`/`run_skill_script`/`knowledge_search`/`lark_cli`/`data_query_search`/`data_query_execute`/`external_task_status` 等）。新增内建能力必须同步此集合与 `ALWAYS_EXPANDED_CAPABILITIES`。
- **调用重放与幂等**：`harness_capability_invoker.py` 用 `_logical_action_key` / `_request_digest` 去重，`_replay_or_block` 命中历史返回 `_replayed_result`；`tool_replay_policy.py` 定义 `TOOL_CALL_HISTORY_SLOT` / `TOOL_RESULTS_SLOT`。
- **大结果句柄**：`_persist_large_json_result`（阈值 `_INLINE_JSON_TOOL_RESULT_MAX_CHARS=2000`）把大 JSON 落盘到 `.harness/tool-results/`，回给模型一个句柄；`_resolve_json_tool_result_references` / `_read_json_tool_result_reference` 负责回读。技能包物化到 `.harness/skill-packages/{slug}-{digest}/`（`_materialize_general_skill_package`）。
- **租约（三重 900s）**：Turn `TURN_LEASE_SECONDS`、Session `SESSION_LEASE_SECONDS`、Frame `FRAME_LEASE_SECONDS`；会话互斥锁 `harness_session_lock.py`（`acquire_harness_session`，冲突抛 `HarnessSessionBusy`）。
- **会话与存储布局**：`harness_session_cleanup.py` → `harness_storage_root` / `harness_session_workspace_path` / `harness_task_workspace_path` / `remove_harness_session_workspace`。
- **上下文预算**：`context_projection.py`（`CONTROL_CONTEXT_TOKEN_BUDGET=32000`）压缩模型可见上下文；`conversation_context.py`（`DEFAULT_CONTEXT_TOKEN_BUDGET=32000`，`COMPACTION_TRIGGER_RATIO=0.70`，`RECENT_ROUND_LIMIT=6`）做历史摘要。
- **附件隔离**：`harness_attachments.py` → `materialize_task_attachments` / `validated_task_image_payloads` / `isolated_attachment_context`。
- **Slash 命令**：`slash_commands.py` → `parse_slash_command` / `resolve_sop` / `resolve_capability` / `build_slash_turn_plan` / `force_capability_for_requirement` / `slash_command_catalog`。
- **人工接管**：`human_handoff_service.py` → `HumanHandoffService`。
- **反射**：`reflection_agent.py` → `ReflectionAgent`、`action_needs_reflection`、`tool_result_needs_reflection`（提示词 `reflection_prompt.md`）。
- **已发布交付物**：`published_deliverables.py` → `list_published_deliverables` / `find_published_deliverable`（`MAX_PUBLISHED_DELIVERABLES=20`）。

## 内建能力实现锚点（`harness_capability_invoker.py`）

`_invoke_internal` 是内建能力的分发中心：

| 能力名 | 方法 |
|---|---|
| `capability_search` / `capability_describe` | `_search_capabilities` / `_describe_capabilities` |
| `data_query_search` / `data_query_execute` | `_search_data_queries` / `_execute_data_query`（走 `app.data_query.service.list_query_templates` / `execute_query_by_id`；返回 `table_markdown`） |
| `external_task_status` | `_external_task_status` |
| `list_published_deliverables` / `read_published_deliverable` | `_list_published_deliverables` / `_read_published_deliverable` |
| `lark_cli` | 转 `app.lark_cli.service.invoke_lark_cli` |
| 文件/命令类 | `_invoke_file`（含 `exec_command`、`run_skill_script`） |
| 通用技能 | `_invoke_general_skill` / `_read_general_skill_package` / `_general_skill_artifacts` |
| 知识检索 | `_search_knowledge` |
| 外部工具 / A2A | `_invoke_external_tool` / `_materialize_a2a_artifacts` |

## 任务 → 文件对照（AI 定位用）

| 想改什么 | 去看 |
|---|---|
| 模型“看到”的能力清单 / 8K 目录预算 / 搜索排序 | `capability_discovery.py` |
| 某能力是否可用、授权范围、保留名、快照摘要 | `capability_manifest.py`（`tool_snapshot_digest` / `general_skill_snapshot_digest` / `_snapshot_revision`） |
| 内建能力行为（含 data_query 两个能力） | `harness_capability_invoker.py` `_invoke_internal` 及各 `_*` 方法 |
| 单任务 ReAct 循环 / 动作解析 / 预算与超时 | `harness_agent.py`（`MAX_*`、`_bounded_capability_result`、`_step_timeout_result`） |
| Turn 如何拆成多个 TaskFrame | `turn_planner.py`、`task_frame_store.py` |
| 每个 Task 收到什么（slots / SOP 节点 / 记忆 / 附件） | `task_request_compiler.py` `TaskRequestCompiler.compile` |
| 任务完成 / 失败 / handoff 判定与回复 | `harness_v2_engine.py`（`_combine_results`、`_single_task_reply`、`_structured_reply_requires_synthesis`）、`turn_finalizer.py` |
| 进程崩溃后恢复孤点 run | `harness_recovery.py` |
| 上下文太大 / 模型失忆 | `context_projection.py`、`conversation_context.py` |

## 测试与质量

- 相关测试：`backend/tests/test_capability_discovery.py`、`test_capability_contracts.py`、`test_capability_registry.py`、`test_capability_scope.py`、`test_context_projection.py`、`test_conversation_context.py`、`test_harness_turn_store.py`、`test_harness_session_lease.py`、`test_harness_recovery.py`、`test_harness_v2_schema_migration.py`、`test_reflection_agent.py`、`test_graph_rules.py`。
- Windows 已知失败基线见仓库根 `AGENTS.md`（沙箱/符号链接/lark-cli 相关）。

## 常见问题 (FAQ)

- 模型说“没有这个能力”：检查该能力是否在 `CapabilityManifestBuilder.build` 的 `available` 中，再检查是否被 `project_capability_manifest` 收进目录（未扩展时需模型主动 `capability_search`/`capability_describe`）。
- 大 JSON 结果被模型当成普通参数塞进 `argv`：见记忆沉淀 `run-skill-script-sandbox-json-handle-break`；句柄解析只在外部工具路径注册，内建工具路径需自行回读。
- 定时任务“假成功”：成功判定不在 `core/`，在 `../scheduled_tasks/service.py` 的 `_scheduled_harness_outcome`。

## 相关文件清单

`harness_v2_engine.py`、`turn_planner.py`、`task_frame_store.py`、`task_request_compiler.py`、`capability_manifest.py`、`capability_discovery.py`、`harness_agent.py`、`harness_capability_invoker.py`、`turn_finalizer.py`、`response_generator.py`、`context_projection.py`、`conversation_context.py`、`harness_recovery.py`、`harness_session_lease.py`、`harness_session_lock.py`、`harness_session_cleanup.py`、`harness_turn_store.py`、`harness_attachments.py`、`cancellation.py`、`reflection_agent.py`、`slash_commands.py`、`human_handoff_service.py`、`published_deliverables.py`、`tool_replay_policy.py`、`slot_hydration_policy.py`、`task_frame_policy.py`、`skill_runtime.py`、`agent_loop.py`、`step_agent.py`、`router.py`、`graph_rules.py`、`agent_identity_prompt.py`、`conversation_projection.py`。

## 变更记录 (Changelog)

- 2026-09-24T09:46 — 初始化架构师新建；登记 Harness v2 主链路、渐进披露不变量、内建能力分发锚点与任务→文件对照表。
