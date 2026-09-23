[根目录](../CLAUDE.md) > **backend**

# backend — FastAPI 运行时与存储

## 模块职责

StaffDeck 的单体后端：提供内部路由、Agent 运行时（Harness v2）、渠道接入、技能/知识/工具/MCP、定时任务、记忆/反馈/演化、`public_api` 开放 API v1 子应用。SQLModel + SQLite（`skill_agent_loop.db`）持久化，OpenAI 兼容模型为推理后端。

## 入口与启动

| 入口 | 说明 |
|---|---|
| `app/main.py` | FastAPI 应用工厂与生命周期：启动时初始化 DB、种子数据、恢复孤点 Harness、拉起后台 worker（scheduled / external-task / channels / sweeper）|
| `single_port_app.py` | 单端口（5173）同时提供 UI/API 的包装入口 |
| `desktop_launcher.py` | 桌面端启动器（PyInstaller 打包）|
| `feishu_connector_worker.py` | 飞书/渠道连接器独立 worker |
| `scripts/dev.py` | 生命周期入口（自动调用上方逻辑）|

关键配置类：`app/config.py`（`get_settings()`，读取 `backend/.env`）。

## 对外接口

- 内部路由：`app/api/*.py`（23+ 模块：agents、chat、sessions、skills、knowledge、knowledge_bases、memories、persona、model_configs、channels、teams、tools、traces、evolution、feedback、general_skills、external_business_tasks、scheduled_tasks、app_updates、auth、ui_config、mock、wechat_kf、data_query）。
- 开放 API：`app/public_api/` 独立子应用，Base `/api/v1`，Bearer `sd_live_*` 密钥模型。
- A2A 协议：`app/a2a/`（Codex A2A 任务适配与恢复）。
- 健康检查：`GET /api/health` → `{"status":"ok"}`。

## 子包结构（后端内部模块）

| 子包 | 职责 |
|---|---|
| `core/` | Agent 运行时内核：Router、TurnPlanner、TaskFrame、Harness v2 会话/租约/恢复、反射、人工接管、slash 命令 |
| `harness/` | Harness v2 沙箱执行引擎：executor / sandbox / command / skill_script / registry / contracts / filesystem / artifacts |
| `capabilities/` | 能力契约体系（contracts / registry / local_registry / local_knowledge / local_general_skill / scope / testkit）与 JSON Schema 目录 `schemas/` |
| `channels/` | IM 渠道内核与适配器（base + dingtalk/wecom/wechat_kf/feishu_manager），渠道无关的 autoroute / identity / inbox / outbox / routing / session |
| `tools/` | 工具执行：`tool_executor`、`tool_schema`、MCP（`mcp_client`/`mcp_builtin`）、HTTP 请求、A2A 客户端、外部任务 worker |
| `knowledge/` | 文档结构感知知识检索：OKF、parser、service、schema、citations |
| `general_skills/` | 通用技能包运行时：runner / runtime_env / schema |
| `llm/` | 模型接入：`client`（LLMClient）、`protocol_drivers`（anthropic/gemini/openai-responses 等）、`model_config_resolver`、提示词 `prompts/*.md` |
| `skills/` | 技能/skill 生命周期：skill_distiller、skill_editor、skill_reflection、skill_schema、step_ids、nesting（SOP 嵌套）、llm_limits、tool_authorization |
| `scheduled_tasks/` | 定时/周期任务引擎（见模块文档）|
| `data_query/` | 数据查询中心（见模块文档）|
| `teams/` | 多员工团队协作：service / wakeup / sweeper / schema |
| `memory/` | 长期记忆：service / jobs |
| `feedback/` | 用户反馈：service / jobs |
| `evolution/` | 能力演化：schema / service |
| `session/` | 会话模型与 ChatTurn 请求/响应（session_schema）|
| `security/` | 权限 / 租户 / AES-GCM 加密 / artifact_share / managed_subprocess 等 |
| `observability/` | 事件日志与 spans（`llm_operation` 等）|
| `db/` | 引擎与模型：`models.py`（约 67 表）、`seed.py`、`staffdeck_seed.py`、`database.py` |

## 数据模型

集中在 `app/db/models.py`（SQLModel，67 个表模型）。关键上下文：`ScheduledTask` / `ScheduledTaskRun`、`HarnessTurnRecord` / `HarnessTaskFrameRecord` / `HarnessRunRecord` / `HarnessInvocationRecord`、`AgentEvent`、`ChatSession`、`AgentProfile`、`User`。主库 `skill_agent_loop.db`（部分 `sop_agent_loop.db`）。

## 测试与质量

- `backend/tests/`：157+ 个 `pytest` 文件，按域命名（channel_*/harness_*/knowledge_*/tools_*/scheduled_task 等）。
- **Windows 已知本机失败基线**：详见 `backend/AGENTS.md`（2026-09-16 快照 48 failed / 2198 passed；先读此文件再判断失败归属）。
- 规范：`ruff`，line-length 100；`pyproject.toml` 配置 `testpaths=["tests"]`。
- 其他：`mock_servers/` 提供 MCP stdio server 与鉴权矩阵 mock，供测试/演示。

## 常见问题 (FAQ)

- 员工不回答：查模型配置/API Key/网络 + `.dev/logs/app.log`。
- 定时任务“假成功”：已在 `scheduled_tasks/service.py` 改为依赖 Harness v2 持久化状态与业务失败判定，绝不只信答复文本（详见该模块文档）。

## 相关文件清单

`app/main.py`、`app/config.py`、`app/db/models.py`、`app/harness/*`、`app/core/*`、`app/public_api/*`、`app/env.sh`（若存在）、`pyproject.toml`、`AGENTS.md`。

## 变更记录 (Changelog)

- 2026-09-23T18:06 — 初始化架构师（增量）重建模块文档；新增 `data_query` 子模块页与顶层面包屑。