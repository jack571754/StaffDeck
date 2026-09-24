[根目录](../CLAUDE.md) > **backend**

# backend — FastAPI 运行时与存储

## 模块职责

StaffDeck 的单体后端：内部路由、Agent 运行时（Harness v2）、渠道接入、技能/知识/工具/MCP、定时任务、数据查询中心、记忆/反馈/演化、`public_api` 开放 API v1 子应用。SQLModel + SQLite（`skill_agent_loop.db`）持久化，OpenAI 兼容模型为推理后端。

## 入口与启动

| 入口 | 说明 |
|---|---|
| `app/main.py` | FastAPI 应用工厂与生命周期：初始化 DB、种子数据、恢复孤点 Harness（`recover_orphan_harness_runs`）、拉起后台 worker（scheduled / external-task / channels / sweeper / `start_public_api_maintenance`）；`app.include_router(...)` 挂载全部内部路由；`settings.public_api_enabled` 时 `app.mount("/api/v1", create_public_api_app())` |
| `single_port_app.py` | 单端口（5173）同时提供 UI/API 的包装入口 |
| `desktop_launcher.py` | 桌面端启动器（PyInstaller 打包）|
| `feishu_connector_worker.py` | 飞书/渠道连接器独立 worker |
| `scripts/dev.py` | 生命周期入口（自动调用上方逻辑）|

关键配置类：`app/config.py`（`get_settings()`，读取 `backend/.env`）。

## 对外接口

- 内部路由：`app/api/*.py`（agents、chat、sessions、skills、knowledge、knowledge_bases、memories、persona、model_configs、channels、teams、tools、traces、evolution、feedback、general_skills、external_business_tasks、scheduled_tasks、app_updates、auth、ui_config、mock、wechat_kf、data_query）。
- 开放 API：`app/public_api/` 独立子应用，挂载于 `/api/v1`（见下）。
- A2A 协议：`app/a2a/codex_adapter.py`（Codex A2A 任务适配与恢复）。
- 健康检查：`GET /api/health` → `{"status":"ok"}`。

## 子包结构（后端内部模块）

| 子包 | 职责 | 文档 |
|---|---|---|
| `core/` | **Harness v2 运行时内核**：Turn 规划、TaskFrame 持久化/租约、能力清单与渐进披露、单任务 Agent 循环、能力调用与重放、上下文投影、取消/恢复、反射、人工接管、slash 命令 | [core/CLAUDE.md](app/core/CLAUDE.md) |
| `harness/` | Harness v2 沙箱执行引擎：executor / sandbox / command / skill_script / registry / contracts / filesystem / artifacts / execution_context | — |
| `capabilities/` | 能力契约体系（端口/适配器、注册表与快照、本地知识/技能包实现、testkit、JSON Schema） | [capabilities/CLAUDE.md](app/capabilities/CLAUDE.md) |
| `channels/` | IM 渠道内核与适配器（base + dingtalk/wecom/wechat_kf/feishu_manager），渠道无关的 autoroute / identity / inbox / outbox / routing / session | — |
| `tools/` | 工具执行：`tool_executor`、`tool_schema`、MCP（`mcp_client`/`mcp_builtin`）、HTTP 请求、A2A 客户端、外部任务 worker | — |
| `knowledge/` | 文档结构感知知识检索：OKF、parser、service、schema、citations | — |
| `general_skills/` | 通用技能包运行时：runner / runtime_env / schema | — |
| `llm/` | 模型接入：`client`（LLMClient）、`protocol_drivers`（anthropic/gemini/openai-responses 等）、`model_config_resolver`、提示词 `prompts/*.md` | — |
| `skills/` | 技能/skill 生命周期：skill_distiller、skill_editor、skill_reflection、skill_schema、step_ids、nesting（SOP 嵌套）、llm_limits、tool_authorization | — |
| `scheduled_tasks/` | 定时/周期任务引擎（含 `renderers/` 飞书卡片渲染器注册表） | [scheduled_tasks/CLAUDE.md](app/scheduled_tasks/CLAUDE.md) |
| `data_query/` | 数据查询中心 | [data_query/CLAUDE.md](app/data_query/CLAUDE.md) |
| `teams/` | 多员工团队协作：service / wakeup / sweeper / schema | — |
| `memory/` | 长期记忆：service / jobs | — |
| `feedback/` | 用户反馈：service / jobs | — |
| `evolution/` | 能力演化：schema / service | — |
| `session/` | 会话模型与 ChatTurn 请求/响应（`session_schema.py`：`ChatTurnRequest`、`TurnPlan`、`PlannedTaskFrame`、`StepAgentResult`） | — |
| `security/` | 权限 / 租户 / 加密 / 内部服务令牌 / 托管子进程（见下） | — |
| `observability/` | 事件日志与 spans（`llm_operation` 等） | — |
| `db/` | 引擎与模型：`models.py`（约 67 表）、`seed.py`、`staffdeck_seed.py`、`database.py` | — |
| `public_api/` | 开放 API v1 子应用（见下） | — |
| `a2a/` | Codex A2A 协议适配（见下） | — |
| `lark_cli/` | 飞书 CLI 集成（见下） | — |

## security/ 关键锚点

| 文件 | 符号 | 说明 |
|---|---|---|
| `auth.py` | `create_access_token`、`get_current_user`、`ensure_current_user_tenant`、`require_current_tenant`、`hash_password`/`verify_password`、`_decode_token` | 自签 token（HMAC），`TOKEN_TTL_SECONDS = 14 天` |
| `permissions.py` | `ensure_tenant_admin`、`require_tenant_admin`、`require_agent_scope_viewer`、`ensure_agent_scope_manager`、`ensure_open_gallery_admin`、`agent_owned_by_user`、`is_admin_user` | 角色 `admin`/`member`（`ADMIN_ROLE`/`MEMBER_ROLE`/`USER_ROLES`） |
| `tenant.py` | `ensure_tenant(session, tenant_id)` | 租户存在性校验（跨租户 403 的前置） |
| `encryption.py` | `encrypt_secret` / `decrypt_secret` / `mask_secret`（Fernet） | 渠道凭证等敏感串加密 |
| `internal_service.py` | `INTERNAL_SERVICE_HEADER = "X-UltraRAG-Internal-Token"`、`internal_service_token`、`require_internal_service` | 内部服务间 HMAC 头（mock 数据查询接口用） |
| `managed_subprocess.py` | `ManagedProcess`、`ManagedProcessError`、`_WindowsJob`、`_terminate_process_tree` | 跨平台托管子进程与进程树终止（Windows Job Object） |

## public_api/ 开放 API v1

- 装配：`app.py` → `create_public_api_app()`；挂载前缀 `/api/v1`；健康检查 `GET /api/v1/health`（`engine: harness_v2`）。
- 鉴权：`auth.py` → `PublicPrincipal`、`get_public_principal`、`get_public_or_admin_principal`、`require_scopes(*scopes)`、`enforce_agent_access`；密钥前缀 `PUBLIC_KEY_PREFIX = "sd_live_"`，摘要用 HMAC（pepper = `public_api_key_pepper` 或 `app_secret`）。
- 作用域档案：`credential_profiles.py` → `AGENT_RUNTIME_SCOPES` / `AGENT_FULL_ACCESS_SCOPES` / `USER_FULL_ACCESS_SCOPES`、`scopes_for_agent_access` / `agent_access_for_scopes`。
- 路由模块：`credentials.py`（api-clients、credentials `:rotate`/`:revoke`）、`gallery.py`（`/{agent_id}:add`）、`agents.py`（CRUD、`:archive`、resources/models/capabilities）、`sessions.py`、`runs.py`（`runs`、`runs:stream`、`:cancel`、`/artifacts`）、`jobs.py`（异步 job、SSE `/events`、`:cancel`、`recover_public_jobs`、`cleanup_public_api_records`、`register_job_handler`、`JOB_LEASE_SECONDS=900`）、`sops.py`、`resources.py`（knowledge-bases、general-skills、tools、mcp-servers、scheduled-tasks）、`operations.py`（audit-logs / usage / handoffs / feedback）、`webhooks.py`、`examples.py`。
- 机制：`idempotency.py`（`request_fingerprint` / `replay_idempotent_response` / `store_idempotent_response`，表 `api_idempotency_records`）、`json_patch.py`（`apply_json_patch`，RFC6902）、`errors.py`（`PublicAPIError` → problem+json）、`utils.py`（`audit_request` → 表 `api_audit_logs`）、`maintenance.py`（`start_public_api_maintenance`）。

## a2a/ 与 lark_cli/

- `a2a/codex_adapter.py`：Codex A2A JSON-RPC（`codex_agent_card`、`codex_a2a_rpc`、`codex_a2a_artifact`、`recover_codex_a2a_tasks`）；终端状态集 `_TERMINAL = {completed, failed, canceled, rejected, input-required}`；表 `a2a_task_runs`；`_authorize` 校验 A2A 令牌。
- `lark_cli/`：`service.py`（`TOOL_NAME="lark_cli"`、`invoke_lark_cli`、审批表单校验 `_validate_form_structure`）、`policy.py`（命令白名单/风险分级 `resolve`、`logical_write_signature`、`submission_digest`）、`runner.py`（`run_lark_cli`、`user_home_dir`、`_kill_process_tree`、输出脱敏 `_redact`）、`provision.py`（`PINNED_VERSION="1.0.89"`、`ensure_lark_cli`）、`background.py`（`config_init_new_status`）。

## 数据模型

集中在 `app/db/models.py`（SQLModel，约 67 表）。关键表名与模型：

| 域 | 表名 | 模型 |
|---|---|---|
| 定时任务 | `scheduled_tasks` / `scheduled_task_runs` | `ScheduledTask` / `ScheduledTaskRun` |
| Harness v2 | `harness_turns` / `harness_task_frames` / `harness_runs` / `harness_invocations` / `harness_session_leases` / `harness_agent_loops` | `HarnessTurnRecord` / `HarnessTaskFrameRecord` / `HarnessRunRecord` / `HarnessInvocationRecord` / `HarnessSessionLeaseRecord` |
| 技能 | `skills` / `skill_versions` / `general_skills` / `skill_feedback` | `Skill` / `GeneralSkill` |
| 开放 API | `api_clients` / `api_credentials` / `api_jobs` / `api_job_events` / `api_audit_logs` / `api_idempotency_records` / `api_sop_drafts` | `APIClient` / `APICredential` / `APIJob` |

主库 `skill_agent_loop.db`（部分 `sop_agent_loop.db`）；两者均在 `.gitignore` 中。

## 测试与质量

- `backend/tests/`：169 个 `pytest` 文件，按域命名（channel_*/harness_*/knowledge_*/tools_*/capability_*/public_api_*/scheduled_task 等）。
- **Windows 已知本机失败基线**：详见**仓库根** `../AGENTS.md`「Known environment-specific test failures (Windows)」（2026-09-16 快照 48 failed / 2198 passed；lark_cli / 沙箱 / 符号链接相关；先读此文件再判断失败归属）。
- 规范：`ruff`，line-length 100；`pyproject.toml` 配置 `testpaths=["tests"]`。
- 其他：`mock_servers/` 提供 MCP stdio server 与鉴权矩阵 mock，供测试/演示。

## 常见问题 (FAQ)

- 员工不回答：查模型配置/API Key/网络 + `.dev/logs/app.log`。
- 定时任务“假成功”：`scheduled_tasks/service.py` 依赖 Harness v2 持久化状态与业务失败判定，绝不只信答复文本（见该模块文档）。
- 定时任务 pipeline 任务却出销售播报卡：见 `scheduled_tasks/renderers/` 默认渲染器为 `sales_card`（模块文档「Pipeline 通道」一节）。
- 模型看不到某能力：`core/capability_manifest.py`（是否授权）与 `core/capability_discovery.py`（是否投影进 8K 目录）。
- 开放 API 401/403：`public_api/auth.py` 的 scope 与 `credential_profiles.py` 档案不匹配；`sd_live_*` 密钥摘要 pepper 变化会使旧密钥失效。

## 相关文件清单

`app/main.py`、`app/config.py`、`app/db/models.py`、`app/harness/*`、`app/core/*`、`app/capabilities/*`、`app/scheduled_tasks/*`、`app/security/*`、`app/public_api/*`、`app/a2a/codex_adapter.py`、`app/lark_cli/*`、`pyproject.toml`、`../AGENTS.md`。

## 变更记录 (Changelog)

- 2026-09-24T16:45 — 增量更新（聚焦定时任务 pipeline 通道）：`scheduled_tasks/` 子包职责补注 `renderers/` 飞书卡片渲染器注册表；FAQ 新增「pipeline 任务却出销售播报卡」条目（默认渲染器为 `sales_card`）；相关文件清单补 `app/scheduled_tasks/*`。
- 2026-09-24T09:46 — 增量更新：新增 `core/`、`capabilities/` 子模块文档链接；补 `security/`、`public_api/`、`a2a/`、`lark_cli/` 关键符号与表名；数据模型改为「表名 + 模型」对照表；**修正 Windows 失败基线路径为仓库根 `../AGENTS.md`**（原文误写 `backend/AGENTS.md`）。
- 2026-09-23T18:06 — 初始化架构师（增量）重建模块文档；新增 `data_query` 子模块页与顶层面包屑。
