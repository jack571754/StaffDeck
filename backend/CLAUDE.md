[根目录](../CLAUDE.md) > **backend**

# backend — FastAPI 单端口服务

## 模块职责

StaffDeck 的全部服务端逻辑：单端口 FastAPI 应用同时提供企业工作台 UI（托管 `frontend-enterprise/dist` SPA）、内部 API（`app/api/`，23 个路由模块）、版本化开放 API 子应用（`app/public_api/`，`/api/v1`）与 Swagger（`/docs`）。承载 Harness v2 会话执行内核、工具沙箱、IM 渠道接入、定时任务、多员工协作与 A2A 协议适配。支持桌面单端口运行（PyInstaller 打包，见 `../packaging/`）。

## 入口与启动

| 入口 | 文件 | 说明 |
|---|---|---|
| ASGI 应用 | `app/main.py` | FastAPI app：挂 23 个 `app/api/*` 路由、`public_api` 子应用、`a2a` router；startup 钩子启动异步作业、渠道服务、Harness 恢复清扫、定时任务 worker、teams 超时清扫、外部任务 worker、public_api 维护与 webhook 投递，并获取运行时实例锁 |
| 单端口入口 | `single_port_app.py` | 托管前端 SPA（dev 从仓库根找 `frontend-enterprise/dist`，frozen 从 `_MEIPASS`）、site-chat 上游反代（`STAFFDECK_SITE_CHAT_UPSTREAM`，默认 127.0.0.1:10187）、PilotDeck / LLM Relay / Anthropic Relay 反代（环境变量驱动） |
| 桌面启动器 | `desktop_launcher.py` | 桌面单端口运行（PyInstaller 打包目标） |
| 飞书连接器 | `feishu_connector_worker.py` | 飞书渠道 worker 进程 |

```bash
# 推荐（仓库根）
scripts/dev_up.sh
# 仅后端调试
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" && cp .env.example .env
.venv/bin/uvicorn single_port_app:app --host 127.0.0.1 --port 5173
# Swagger: http://localhost:5173/docs
```

## 对外接口

### 内部 API（`app/api/`，23 个路由模块）

`agents`、`app_updates`、`auth`、`channels`、`chat`、`evolution`、`external_business_tasks`、`feedback`、`general_skills`、`knowledge`、`knowledge_bases`、`memories`、`mock`、`model_configs`、`persona`、`scheduled_tasks`、`sessions`、`skills`、`teams`、`tools`、`traces`、`ui_config`、`wechat_kf`。

**近期新增端点摘要：**
- `general_skills`：新增技能市场 3 端点 `GET /market/items`、`GET /market/skills/{slug}/preview`、`POST /market/install`（skills.sh 注册表数据源，一键安装开源技能包）；另有 `/import`、`/import-skillhub`、`/import-clawhub`、`/import-package` 多源导入
- `chat`：新增产物签名分享 `POST /artifact-share`（`mint_artifact_share`）、`GET /published-artifacts/{token}`（`view_published_artifact`），匿名 HMAC 签名链接，默认 7 天 TTL

### 开放 API v1（`app/public_api/`）

独立 FastAPI 子应用（`create_public_api_app()`），启用 `/docs`、`/redoc`、`/openapi.json`；`X-Request-ID` 中间件 + 请求审计。路由：`credentials`、`gallery`、`agents`、`sessions`、`runs`、`jobs`、`sops`、`resources`、`operations`、`examples`、`webhooks`。鉴权：Bearer `sd_live_*` 凭证（服务端推导租户/Client/scope/员工边界）；支持幂等键与 webhook 投递。文档：`../docs/open-api-v1.md`。

### A2A

`app/a2a/codex_adapter.py`：Codex A2A 适配（`CODEX_A2A_*` 配置开关），任务恢复/停止由 `app/main.py` 生命周期管理。

## 子包结构（app/ 下 24 个包）

| 子包 | 关键文件 | 职责 |
|---|---|---|
| `api/` | 23 个路由模块（见上） | 内部 REST API |
| `core/` | `harness_v2_engine.py`、`harness_agent.py`、`harness_capability_invoker.py`、`turn_planner.py`、`task_frame_store.py`、`capability_manifest.py`、`slash_commands.py`、`harness_session_{lease,lock}.py`、`reflection_agent.py`、`step_agent.py`、`human_handoff_service.py` 等 | Harness v2 引擎与编排：TurnPlanner、TaskFrame、能力清单/调用、会话租约与锁、slash 命令、槽位注水、记忆读取、知识引用 |
| `harness/` | `artifacts`、`command`、`contracts`、`errors`、`execution_context`、`executor`、`filesystem`、`registry`、`sandbox`、`skill_script` | 沙箱执行器：OS 进程沙箱（首选 Anthropic Sandbox Runtime，Linux 备选 bwrap，无未沙箱回退），仅 TaskFrame 工作区可写，网络策略 allowlist/deny 由 SRT fail-closed 强制 |
| `channels/` | `adapters/{base,wechat,wechat_kf,wecom,feishu,dingtalk}.py`、`service_{intake,routing,outbox,durable_inbox,identity,session,autoroute}.py`、`crypto`、`media`、`markdown_render`、`attachment_bridge` | IM 渠道：5 适配器 + 入站/路由/出站/持久收件箱服务；`CHANNEL_TEXT_LIMIT=2000` |
| `general_skills/` | `runner.py`、`runtime_env.py`、`schema.py` | 通用技能包运行时（SKILL.md + run.py，运行时 Python/venv 可配置，自动装依赖可关）；含市场安装 schema（`SkillMarketInstallRequest`、`GeneralSkillClawHubImportRequest` 等） |
| `knowledge/` | `citations`、`okf`、`parser`、`schema`、`service` | 文档结构感知知识检索（分层索引/知识桶/引用） |
| `tools/` | `tool_executor`、`tool_schema`、`http_request`、`mcp_{builtin,client}`、`a2a_client`、`a2a_recovery`、`external_tasks`、`external_task_worker` | 工具执行、MCP 客户端、A2A 客户端与外部异步任务生命周期（近期提交热点） |
| `scheduled_tasks/` | `worker`、`service`、`schema` | 定时任务后台 worker；支持 5 种调度类型 `once/daily/weekly/monthly/interval`；草稿系统（ScheduledTaskDraftRead，由 LLM 从对话中检测生成）；SOP 版本策略（latest/pinned）；并发/失火策略 |
| `teams/` | `schema`、`service`、`sweeper`、`wakeup` | 多员工协作（认领赛制、黑板、HITL，见根目录 `design-multi-agent-team-decisions.md`） |
| `public_api/` | 见上 | 开放 API v1 |
| `capabilities/` | `contracts`、`registry`、`local_registry`、`local_knowledge`、`local_general_skill`、`scope`、`testkit`、`schemas/*.json` | 能力契约与注册表（JSON Schema 契约、scope、本地实现、测试工具包） |
| `db/` | `models.py`、`database.py`、`database_path.py`、`seed.py`、`staffdeck_seed.py` | SQLModel 模型（约 67 张表）与引擎、演示数据种子 |
| `llm/` | 驱动（anthropic/openai/gemini，见 tests 对应）+ `prompts/` | LLM 客户端与提示词 |
| `security/` | `auth`、`permissions`、`tenant`、`encryption`、`internal_service`、`artifact_share`、`managed_subprocess` | 认证/权限（角色、企业会话隔离）、加密、产物签名分享链接、受管子进程 |
| `session/` | `session_schema.py`、`helpers.py` | 会话 schema 与辅助 |
| `memory/` | `service.py` | 记忆服务 |
| `observability/` | — | 观测（trace/事件日志） |
| `evolution/` | — | 能力成长 |
| `feedback/` | — | 反馈分析 |
| `skills/` | — | SOP 流程型技能（嵌套、版本快照） |
| `agents/` | `branching.py`、`schema.py` | 数字员工档案与 SOP 分支 schema |
| `lark_cli/` | 含 `provision.py`、`runner.py`、`background.py` | 飞书官方 lark-cli 集成（懒加载二进制安装） |
| `a2a/` | `codex_adapter.py` | Codex A2A 适配 |
| 根级散文件 | `config.py`、`async_jobs.py`、`capability_scope.py`、`runtime_lock.py`、`paths.py`、`version.py` | 配置、异步作业、能力 scope、运行时实例锁、资源路径 |

另有 `mock_servers/`（auth_matrix_service、mcp_apps_stdio_server、mcp_stdio_server，测试用 mock）。

## 关键依赖与配置

- `pyproject.toml`：包名 `skill-agent-loop-backend`；核心依赖 fastapi、sqlmodel、uvicorn、anthropic、openai、httpx、lark-channel-sdk、wecom-aibot-python-sdk、dingtalk-stream、pypdf、python-docx、beautifulsoup4；dev 依赖 pytest、pytest-asyncio、ruff、jsonschema；packaging 依赖 pyinstaller（+ macOS pyobjc）。
- `app/config.py`：pydantic-settings，`.env`（可用 `ULTRARAG_DOTENV` 重定向）。重要项：`DATABASE_URL`（默认 `sqlite:///./skill_agent_loop.db`）、`APP_SECRET`、`PUBLIC_API_ENABLED`、`GENERAL_SKILL_RUNTIME_*`（auto_install、packages 默认 `requests,httpx`）、`LARK_CLI_*`、`CHANNEL_*`（rich_render、feishu_trace 等）、`CODEX_A2A_*`。

## 数据模型

约 67 张 SQLModel 表（`app/db/models.py`），全部带 `tenant_id` 多租户隔离。主要分组：

- **租户与认证**：`Tenant`、`User`（角色 admin/member 等、来源 web/渠道懒建）、`UserAvatar`、`APIClient`、`APICredential`（哈希凭证）、`APIIdempotencyRecord`
- **开放 API**：`APIJob`、`APIJobEvent`、`WebhookEndpoint`、`WebhookDelivery`、`APISOPDraft`、`APIAuditLog`、`ExternalSessionBinding`
- **A2A / 外部任务**：`A2ATaskRun`、`A2ATaskEvent`、`ExternalBusinessTask`、`ExternalBusinessTaskEvent`
- **数字员工**：`AgentProfile`、`AgentUsage`、`AgentModelBinding`、`AgentResourceBinding`、`AgentEvent`
- **技能 / SOP**：`Skill`、`SkillVersion`、`AgentSkillBranch`、`AgentSkillBranchVersion`、`GeneralSkill`（通用技能包）
- **知识库**：`KnowledgeBase`、`KnowledgeBaseVersion`、`AgentKnowledgeBranch`、`KnowledgeDocument`、`KnowledgeBucket`、`KnowledgeChunk`、`KnowledgeConcept`、`KnowledgeDiscoverySuggestion`、`KnowledgeIngestJob`
- **工具 / MCP**：`Tool`、`MCPServer`
- **渠道**：`ChatSession`、`ChannelBinding`、`ChannelBindingAgent`、`ChannelBindingManager`、`ChannelConvState`、`ChannelBindCode`、`ChannelIdentity`、`ChannelInboundEvent`、`ChannelDelivery`、`WeChatKfAccount`
- **Harness 执行**：`HarnessAgentLoopRecord`、`HarnessTaskFrameRecord`、`HarnessRunRecord`、`HarnessTurnRecord`、`HarnessSessionLeaseRecord`、`HarnessInvocationRecord`
- **消息 / 反馈**：`Message`、`MessageFeedback`、`SkillFeedback`
- **定时任务**：`ScheduledTask`、`ScheduledTaskRun`
- **人工接管**：`HumanHandoffRequest`
- **记忆 / 成长**：`MemoryRecord`、`EvolutionProposal`
- **多员工协作**：`Team`、`TeamMember`、`TeamRun`、`TeamTask`、`TeamTaskEvent`、`TeamWakeEvent`、`TeamBlackboardEntry`、`TeamTaskBid`
- **配置**：`ModelConfig`、`PersonaConfig`、`UIConfig`
- **Mock 数据**：`MockOrder`

迁移策略：`create_all` 建表，**生产迁移路径仅支持 SQLite**；非 SQLite URL 不是受支持部署。

---

## harness 沙箱执行内核（深度）

### 模块构成（11 个文件）

| 文件 | 职责 |
|---|---|
| `sandbox.py` | 沙箱后端探测与诊断：SRT / Bubblewrap / macOS Seatbelt / Windows unsandboxed 降级 |
| `command.py` | `exec_command` 工具实现 + `run_sandboxed_process` 公共函数 + 命令验证 + 有界进程执行 |
| `executor.py` | `HarnessExecutor`：注册工具的验证、调用、结果封装（JSON 对象、大小限制、异常映射） |
| `skill_script.py` | `run_skill_script`：通用技能包脚本运行（仅允许执行已物化的技能包内文件，路径白名单） |
| `registry.py` | `HarnessRegistry`：工具注册表（命名约束、Pydantic schema 注册、side_effect 分级） |
| `contracts.py` | 核心数据契约：`HarnessToolContext`、`HarnessLimits`、`HarnessToolCall/Result/Error/Spec` |
| `execution_context.py` | `SandboxExecutionContext`：主机路径与沙箱内路径映射（/workspace 别名、argv/env/payload 映射） |
| `filesystem.py` | 工作区文件操作工具集（read/write/edit/list/grep/move/copy/publish_artifact 等） |
| `artifacts.py` | 产物安全访问：路径归一化、符号链接拒绝、race-safe 文件描述符流式读取 |
| `errors.py` | `HarnessExecutionError` 与错误码体系 |

### 沙箱后端优先级

```
最强 → 最弱
 1. SRT (Anthropic Sandbox Runtime) — 首选，跨平台，支持域名 allowlist
 2. Bubblewrap (Linux) — bwrap user namespace 隔离
 3. Seatbelt (macOS) — sandbox-exec 配置文件
 4. unsandboxed (Windows 降级) — 仅 Windows，无沙箱时的高风险降级模式
```

- Linux/macOS：fail-closed，**无未沙箱回退**（不可用时直接抛 `SANDBOX_UNAVAILABLE`）
- Windows：SRT 不可用时降级为 unsandboxed，报 `SANDBOX_UNSANDBOXED_FALLBACK` 诊断，高风险部署需放隔离容器
- 探测函数 `available_backend()` 返回当前最强可用后端；`diagnostics()` 返回人类可读诊断

### 核心安全机制

**资源限制（HarnessLimits）**

| 限制项 | 默认值 | 说明 |
|---|---|---|
| `max_read_bytes` | 1 MB | 单次文件读取上限 |
| `max_file_bytes` | 10 MB | 单文件大小上限 |
| `max_workspace_bytes` | 100 MB | 整个工作区上限 |
| `max_entries` | 1000 | 目录条目数上限 |
| `max_result_bytes` | 200 KB | 工具调用结果 JSON 大小上限 |

**进程执行边界**

| 维度 | 机制 |
|---|---|
| 超时 | 默认 30s，最大 120s（exec_command）/ 600s（skill_script）；超时后 kill 进程 |
| 输出截断 | 默认 32 KB，最大 128 KB；双线程异步 drain + `_CaptureBudget` 共享预算池（stdout+stderr 合计） |
| 命令长度 | 最大 8192 字符 |
| 符号链接 | 工作区内全局禁止（`_reject_workspace_symlinks` 递归扫描） |
| 命令验证 POSIX | 禁止 `$`/`` ` ``（shell 展开/命令替换）、禁止 `<(`/`>(` 进程替换、禁止 `&` 后台、禁止 `<<`/`<<<`/`|&` 重定向 |
| 命令验证 Windows | 禁止环境变量路径逃逸（profile 展开检测） |
| 工作区根 | 必须是绝对路径、真实目录、非符号链接 |

**SRT 文件系统策略**

- `denyRead`：`~/.ssh`、`~/.aws`、`~/.config`、SQLite 数据库文件（含 wal/shm/journal/bak）、`user_data_dir`
- `allowRead`：工作区目录 + 捆绑运行时根 + 临时目录
- `allowWrite`：`.`（工作区）+ 临时目录 + 额外可写路径
- 网络：`strictAllowlist: true`；三档 `all` / `allowlist` / `deny`，由租户配置

**Bubblewrap 隔离参数**

- `--unshare-user/ipc/pid/uts/cgroup-try` + `--cap-drop ALL` + `--clearenv`
- 网络 deny 时 `--unshare-net`（bubblewrap 不支持域名 allowlist，需 SRT）
- 只读绑定系统路径（`/usr`、`/bin`、`/sbin`、`/lib`、`/etc/{alternatives,hosts,ld.so.cache,localtime,nsswitch.conf,resolv.conf,ssl/certs}`）
- 工作区 bind 到 `/workspace`，根目录 remount-ro
- 仅白名单环境变量透传（`ARGUMENTS`、`QUERY`、`SKILL_WORKSPACE`、`ARTIFACT_DIR` 等 10 个）

### 工具注册与执行流程

```
HarnessRegistry.register(name, description, argument_model, handler, side_effect)
    ↓ 注册时校验
    ↓ - 命名: ^[a-z][a-z0-9_.-]{0,127}$
    ↓ - argument_model: Pydantic BaseModel
    ↓ - side_effect: read / write / delete
    ↓
HarnessExecutor.execute(context, call)
    ↓
    1. registry.get(call.name) 查找注册工具
    2. argument_model.model_validate(call.arguments)  schema 校验
    3. handler(context, arguments)  调用处理器
    4. _json_object()  确保返回 JSON 对象 + 序列化校验
    5. 大小检查 < max_result_bytes
    6. 异常映射: HarnessExecutionError → TOOL_ERROR；OSError → IO_ERROR；其他 → INTERNAL_ERROR
```

**已注册工具族**

- `exec_command` — 有界 shell 命令执行（side_effect: write）
- `run_skill_script` — 运行通用技能包脚本（side_effect: write）
- 文件操作族：`read_file`、`write_file`、`edit_file`、`list_directory`、`glob`、`grep`、`file_info`、`make_directory`、`move_file`、`copy_file`、`delete_file`
- 产物族：`publish_artifact`、`extract_document_text`

---

## tools 工具与异步任务（深度）

### 工具类型与执行模式

| tool_type | 说明 | 执行入口 |
|---|---|---|
| `http` | HTTP 请求工具（默认） | `ToolExecutor._execute_http_tool` → httpx |
| `mcp` | MCP 工具（stdio / http / sse / builtin） | `execute_mcp_tool_result` → MCP 客户端 |
| `a2a` | A2A 协议外部代理工具 | `ToolExecutor._execute_a2a_tool` → A2AClient |

**工具执行策略（ToolExecutionPolicy）**

| 字段 | 默认 | 说明 |
|---|---|---|
| `execution_mode` | `sync` | `sync` 同步阻塞 / `detached` 分离异步 |
| `async_strategy` | `staffdeck_worker` | `staffdeck_worker` 内部轮询 / `provider_task` 服务端任务跟踪 |
| `timeout_seconds` | （必填） | 同步模式超时 / 分离模式首次提交超时，1-3600s |
| `poll_interval_seconds` | 5 | provider_task 轮询间隔 |
| `max_tracking_seconds` | 86400 | 最大跟踪时长（24h 默认，30 天上限） |
| `task_id_field` / `status_field` / `result_field` | `taskId` / `status` / `result` |  provider 响应字段映射 |
| `status_mapping` | `{}` | 服务端状态 → StaffDeck 状态的映射表 |

### 异步任务生命周期（detached execution）

```
用户调用 detached 工具
    ↓
创建 ExternalBusinessTask (status=accepted)
    ↓ 提交首次请求到工具 URL，携带
    ↓   X-StaffDeck-Callback-URL (回调地址)
    ↓   X-StaffDeck-Callback-Token (HMAC 验证令牌)
    ↓   Idempotency-Key (幂等键)
    ↓
┌──────────────────────┬──────────────────────────────┐
│ staffdeck_worker 模式    │ provider_task 模式             │
│ 内部轮询完成          │ 依赖服务端任务跟踪          │
│ - poll_due_external_tasks  │ - status_url + 定期轮询       │
│ - external_task_worker 线程 │ - task_id_field 提取 ID      │
│ - 检查超时/完成        │ - status_field 解析状态        │
└──────────────────────┴──────────────────────────────┘
    ↓
任务完成 (completed/failed/cancelled/expired)
    ↓
 _prepare_sop_resume → HarnessTaskFrame 状态更新为 ready_to_resume
    ↓
 Harness 引擎恢复执行，把结果注入 frame slots
```

**核心数据模型**

- `ExternalBusinessTask`：业务任务主表（status、result_json、error_json、callback_token_hash、external_task_id、task_frame_id）
- `ExternalBusinessTaskEvent`：事件溯源（event_id 幂等、event_type、data_json）
- 状态归一化 `normalize_status()`：`succeeded/success/completed` → `completed`；`accepted/submitted/queued/pending` → `accepted`；终态集合 6 种

**回调安全**

- `new_callback_token()` 生成 32 字节 URL-safe 随机令牌
- `callback_token_hash()` SHA-256 哈希后存储
- `verify_callback_token()` 使用 `hmac.compare_digest` 恒定时间比较

### MCP 客户端

**Transport 归一化**：`normalize_transport()` 从配置推断 transport 类型
- `builtin` → `builtin.demo` 内置服务器
- `stdio` → command 配置 → 子进程 stdio 通信
- `http` / `streamable_http` → url 配置 → HTTP 流式
- `sse` → SSE 传输

**会话实现**：`_StdioSession`、`_HttpSession`、`_SseSession` 统一接口 `call_tool_envelope()` / `list_tools_with_capabilities()`

**MCP Apps 支持**：扩展 ID `io.modelcontextprotocol/ui`，MIME 类型 `text/html;profile=mcp-app`，返回 `MCPAppDescriptor` 供前端渲染嵌入式 UI

**内置 MCP 服务器**（`mcp_builtin.py`）：`builtin.demo`，含 `echo`、`sum`、`product_lookup` 三个工具，用于演示和测试

### 工具授权

`ToolExecutor.execute()` 中的多层授权检查：

1. 工具存在性 + 启用状态
2. 员工可见性（`visible_tool_rows`）
3. SOP 授权上下文匹配（`sop_authorization`）
   - 父技能 ID 匹配
   - 技能允许列表
   - `sop_specific` scope 节点显式授权
4. 活跃技能允许列表（`allowed_skills_json`）

### HTTP 工具细节

- GET 请求：`prepare_get_request()` 合并 URL query 参数与调用参数
- 非 GET 请求：JSON body 提交
- 密钥替换：`${secret.NAME}` 模式在 headers 中替换为实际密钥值
- 错误分类：TIMEOUT / HTTP_ERROR / EXECUTION_ERROR

---

## knowledge 分层知识检索（深度）

### 知识分层架构

```
KnowledgeBase (知识库, 多版本)
  ├── KnowledgeDocument (源文档)  — 上传的原始文件(txt/md/html/pdf/docx)
  │     ├── KnowledgeBucket (知识桶 / Wiki 页面)  — LLM 归纳的主题页
  │     │     └── KnowledgeChunk (引用证据块)  — 900 字符粒度的原文片段
  │     └── KnowledgeConcept (概念索引)  — OKF 格式，分层语义索引
  └── KnowledgeDiscoverySuggestion (发现建议)  — 从知识中发现的 SOP/工具草稿
```

### 文档解析（parser.py）

支持格式：`.txt`、`.md`、`.markdown`、`.html`、`.htm`、`.pdf`、`.docx`（旧版 `.doc` 不支持）

| 格式 | 解析方式 |
|---|---|
| txt/md/markdown | 直接多编码解码（utf-8 → utf-8-sig → gb18030 → latin-1） |
| html/htm | BeautifulSoup（首选）或内置 HTMLParser，剔除 script/style/noscript |
| pdf | pypdf PdfReader，按页提取，加 `## 第 N 页` 标题 |
| docx | python-docx（首选，保留标题层级），回退 zip+xml 原始提取 |

### OKF 格式与概念索引（okf.py）

OKF（Open Knowledge Format）版本 0.1，是一种带 frontmatter 的 Markdown 概念卡片格式。

**概念类型（CONCEPT_TYPES）**

| 类型 | 说明 |
|---|---|
| `Source Document` | 源文档级概念卡片 |
| `Source Section` | 文档章节级概念（最多 80 个） |
| `Topic` | 主题概念 |
| `Playbook` | 操作手册型概念 |
| `Business Rule` | 业务规则型概念 |
| `Query Analysis` | 查询分析型概念 |

**概念卡片结构**：`concept_id` + frontmatter（type/title/description）+ Markdown body + links + citations + source_refs

**upsert_concepts()**：批量写入概念，按 `(tenant_id, version_id, concept_id)` 唯一键 upsert

### 检索流程（service.py）

```
用户查询
  ↓
1. 噪声短语清洗（QUERY_NOISE_PHRASES 去掉"我想知道""请问"等）
  ↓
2. 文档路由（document route） — SEARCH_DOCUMENT_ROUTE_LIMIT=120 字符
   从文档索引中筛选相关文档（分数 > SEARCH_MIN_DOCUMENT_SCORE=2.0）
  ↓
3. 桶路由（bucket route） — SEARCH_BUCKET_ROUTE_LIMIT=160 字符
   从知识桶索引中筛选相关桶（分数 > SEARCH_MIN_BUCKET_SCORE=2.0）
  ↓
4. 概念检索（search_concepts） — OKF 概念级语义搜索
   分数 > MIN_CONCEPT_SEARCH_SCORE=4.0
  ↓
5. 证据块检索 — 从 chunk 中找 900 字符粒度原文证据
   分数 > SEARCH_MIN_CHUNK_SCORE=2.0
  ↓
6. 相关块扩充 — 最多 RELATED_CHUNK_MAX_COUNT=6 个
   总字符 ≤ RELATED_CHUNK_MAX_CHARS=4800
  ↓
返回 KnowledgeSearchResponse（含文档/桶/概念/块 + 引用）
```

### 知识入库流水线（10 阶段）

| 阶段 | 进度 | 说明 |
|---|---|---|
| queued | 0% | 排队中 |
| parsing | 8% | 解析原始资料 |
| normalizing | 16% | 规范化 Source |
| documenting | 24% | 写入 Source Document |
| bucketing | 36% | 规划 Wiki 页面（LLM 生成桶结构） |
| bucket_writing | 48% | 写入 OKF Wiki |
| chunking | 62% | 生成引用来源（证据块） |
| summarizing | 74% | 刷新 PageIndex |
| discovering | 88% | 发现 SOP/工具（KnowledgeDiscoverySuggestion） |
| done | 100% | 完成入库 |

### 知识发现

LLM 从文档中自动发现可转化为 SOP 技能和工具的流程，写入 `KnowledgeDiscoverySuggestion` 待用户确认。发现的技能草稿需通过 `validate_discovered_skill()` 严格校验：
- 字段白名单校验（SkillCard / SkillGraphNode / SkillGraphEdge）
- 图可达性校验（从 start_node 到所有节点可达，从 terminal_node 反向可达）
- 节点完整性校验（node_id / name / instruction 非空）

---

## teams 多员工协作（深度）

### 核心实体

| 实体 | 说明 |
|---|---|
| `Team` | 团队，含 leader + members，有 config（task_timeout_minutes 等） |
| `TeamMember` | 团队成员，role: `leader` / `member` |
| `TeamTask` | 团队任务，状态机驱动 |
| `TeamTaskBid` | 竞标投标，多轮 HP 制 |
| `TeamTaskEvent` | 任务事件溯源 |
| `TeamBlackboardEntry` | 黑板条目（共享信息空间） |
| `TeamWakeEvent` | 唤醒事件（异步驱动员工执行） |
| `TeamRun` | 团队运行实例 |

### 任务状态机（9 状态）

```
blocked → pending → bidding → in_progress → review → done
                ↘           ↘           ↓        ↗
                 →→→  escalated  ←←←←←←←←↙  rework
```

| 状态 | 说明 |
|---|---|
| `blocked` | 被依赖阻塞，等待前置任务完成 |
| `pending` | 待派发/待竞标 |
| `bidding` | 竞标中（HP 血条淘汰制） |
| `in_progress` | 执行中（员工 Harness 会话） |
| `review` | 等待 TL 验收 |
| `rework` | 退回重做 |
| `done` | 完成 |
| `escalated` | 升级给人（含等待用户输入） |

**依赖激活条件**：`task_activation_state()` 计算
- `all_succeeded`（默认）：所有依赖完成才激活
- `any_succeeded`：任一依赖完成即激活
- `minimum_succeeded`：至少 N 个依赖完成
- `all_terminal`：所有依赖进入终态（含失败）即激活

### 竞标机制（bidding）

**HP 血条淘汰制**：
- 初始 HP：`BID_HP_INITIAL`
- 每轮 TL 给每位候选打分（0-10），分数扣减 HP
- HP 归零即淘汰
- 多轮反驳（bid_rebuttal_rounds）：候选可针对他人方案反驳
- 最终裁决：TL 输出 `bid_award` JSON 块，选出 winner

**控制代码块**（LLM 输出 ```json 围栏代码块才生效）：
- `team_tasks` — TL 派发任务
- `team_review` — TL 验收结论（approve / rework / escalate）
- `bid` — 成员竞标陈述（plan / estimated_cost / confidence）
- `bid_scores` — TL 打分
- `bid_award` — TL 裁决中标者
- `blackboard_suggestions` — 建议写入黑板

### 黑板模式（blackboard）

- `TeamBlackboardEntry`：共享信息空间，带 tags、pinned、citation
- 来源：人工写入 / 员工建议（blackboard_suggestions 代码块）/ 任务产物自动写入
- 员工执行时读取黑板上下文（`blackboard_context_lines`）作为背景信息

### 唤醒机制（wakeup.py）

`TeamWakeEvent` 是异步派发单元：
- **租约模式**：`claimed` 状态带 180s 租约，worker 每 30s 心跳更新
- **并发控制**：`member_concurrency()` 控制单员工同时执行的任务数
- **队列机制**：成员有执行额度时从队列出队唤醒
- **恢复机制**：sweeper 扫描超时事件，孤儿事件（进程崩了导致租约过期）重新派发

**唤醒流程**：
```
pending wake event → 选空闲成员 → claimed (租约) → 启动 Harness 会话
                                                        ↓
                                                   执行完成 → done / failed
                                                   写任务报告 → review
```

### 清扫器（sweeper.py）

- 周期：60 秒一轮
- 扫描状态：`bidding` / `in_progress` / `review`（默认超时 30 分钟，可配置）
- 超时动作：任务升级为 `escalated`，关联唤醒事件标记 `failed`，释放成员并发额度

---

## general_skills 通用技能包运行时（深度）

### 运行时架构

```
用户查询
  ↓
GeneralSkillSelector (LLM 路由)  —  decide()
  ↓ 判断是否使用通用技能
  ├─ use_general_skill=false → 不使用
  └─ use_general_skill=true  → 选中某 slug
       ↓
GeneralSkillReader / GeneralSkillRunner
       ├─ read 模式：仅说明技能内容，不执行
       └─ execute 模式：生成 runner 代码 → 沙箱执行 → 审查 → 回复
```

### 执行阶段（execute 模式）

| 阶段 | 说明 | 提示词文件 |
|---|---|---|
| selector | LLM 路由判断使用哪个技能 | `general_skill_selector_prompt.md` |
| reader | 只读说明技能内容 | `general_skill_read_prompt.md` |
| runner | 生成执行代码（Python/Bash） | `general_skill_runner_prompt.md` |
| repair | 执行失败后修复代码 | `general_skill_repair_prompt.md` |
| review | 审查结果是否充分 | `general_skill_review_prompt.md` |
| reply | 生成最终自然语言回复 | `general_skill_reply_prompt.md` |

**参数限制**：
- 运行超时：`RUN_TIMEOUT_SECONDS = 12`（单轮执行）
- 最大输出：`MAX_OUTPUT_CHARS = 20000`
- 最大尝试：`GENERAL_SKILL_MAX_ATTEMPTS = 10`
- 最大产物声明：`MAX_DECLARED_ARTIFACTS = 20`

### 运行时环境（runtime_env.py）

**Python 运行时解析优先级**：
1. 显式 `general_skill_runtime_python` 配置
2. 显式 `general_skill_runtime_venv` 配置
3. 打包态捆绑 Python（`runtime/` 目录）
4. 后端 `.venv`（开发模式）
5. 后端 `.runtime_venv`（自动创建）

**依赖自动安装**（`auto_install + network_install` 都开启时）：
- 默认包列表：`requests, httpx`
- 检查方式：pip 已安装 + 实际可 import（双保险）
- 支持自定义 PyPI 镜像（`general_skill_pip_index_url`）
- pip 超时：`general_skill_pip_timeout_seconds`

### 技能包结构

每个通用技能包（GeneralSkill）核心字段：
- `slug`：唯一标识
- `name` / `description` / `homepage`
- `skill_markdown`：SKILL.md 内容（技能说明、输入输出、使用示例）
- `package_zip` / `package_json`：可选的代码包（run.py + 依赖等）
- `status`：`draft` / `published` / `archived`
- `capability_scope`：能力范围
- `runtime_python` / `runtime_venv`：可选的独立运行时配置

### 技能市场安装流程

`GET /api/general_skills/market/items` — 从 skills.sh 注册表搜索
`GET /api/general_skills/market/skills/{slug}/preview` — 预览技能详情
`POST /api/general_skills/market/install` — 一键安装
- 下载技能包 zip
- 解析 SKILL.md
- 写入 GeneralSkill 表
- 支持企业级或私有安装

---

## mock_servers 测试基础设施（深度）

### 三个 Mock 服务

| 服务 | 文件 | 用途 |
|---|---|---|
| MCP stdio server | `mock_servers/mcp_stdio_server.py` | MCP 标准工具 mock（echo/sum/product_lookup），stdio JSON-RPC |
| MCP Apps stdio server | `mock_servers/mcp_apps_stdio_server.py` | MCP Apps (UI 扩展) mock，测试 `io.modelcontextprotocol/ui` 扩展 |
| Auth matrix service | `mock_servers/auth_matrix_service.py` | HTTP 工具鉴权矩阵测试（4 种 auth scheme） |

### MCP stdio mock server

- 协议：JSON-RPC 2.0 over stdio（行分隔）
- 工具：`echo` / `sum` / `product_lookup`（与内置 MCP 工具相同）
- 能力声明：`tools.listChanged: false`
- 用于：MCP 客户端集成测试（stdio transport）

### MCP Apps mock server

- 在基础 MCP 工具之上增加 `io.modelcontextprotocol/ui` 扩展
- `render_card` 工具：返回带 `_meta.ui.render` 的结构化结果
- `resources/read` 端点：返回 MCP App HTML 资源（`text/html;profile=mcp-app`）
- 用于：MCP App 视图集成测试

### Auth matrix mock service

FastAPI 服务（默认 18081 端口），提供 4 种鉴权方案的端点：

| 端点 | 鉴权方式 | 凭证 |
|---|---|---|
| `POST /basic` | HTTP Basic | demo:secret |
| `POST /bearer` | Bearer Token | bearer-secret |
| `POST /api-key` | X-API-Key header | api-key-secret |
| `POST /custom` | 自定义 header | x-customer-token + X-Tenant-ID |

用于：工具认证配置的端到端测试（验证各种 auth 模式正确传递）。

---

## 测试与质量

- `tests/`（157 个 `test_*.py`）：按前缀覆盖 harness（v2/sandbox/skill_script/session lease/command/artifact/recovery 等）、channels（各适配器、intake/routing/outbox/durable inbox/团队绑定）、capability（contracts/registry/scope/testkit）、knowledge、public_api、teams、enterprise 鉴权与可见性、模型驱动（anthropic/gemini/openai_responses）、定时任务（interval/草稿/turn_receipt）、通用技能（市场/并发/产物/运行时）、产物分享预览、工具执行/MCP、迁移等。
- 运行：`backend/.venv/bin/python -m pytest backend/tests`；风格：`ruff check backend`（py311、行宽 100）。
- Windows 环境下约 48 例环境性测试失败（lark_cli/POSIX 沙箱/symlink 等），见记忆文件 `backend-env-preexisting-test-failures.md`。

## 常见问题 (FAQ)

- **前端从哪里来？** `single_port_app.py` 托管 `frontend-enterprise/dist`，dev 模式回退到仓库根查找；先 `npm --prefix frontend-enterprise run build`。
- **沙箱如何就绪？** 源码部署执行 `python3 packaging/fetch_sandbox_runtime.py packaging/sandbox_runtime`；`scripts/dev_up.sh` 会自动准备；无可用后端时 `exec_command` 能力报不可用（无未沙箱回退）。
- **Swagger 在哪？** 主应用关闭了 docs；开放 API 子应用提供 `/docs`、`/redoc`、`/openapi.json`。
- **技能市场是什么？** `GET /api/general_skills/market/items` 对接 skills.sh 注册表搜索 API，返回开源通用技能包列表；`POST /market/install` 一键下载安装为企业或私有技能。
- **产物分享链接安全吗？** `app/security/artifact_share.py` 使用 APP_SECRET HMAC 签名，绑定 tenant/session/task_frame/path，路径归一化防止遍历，默认 7 天 TTL，未知/无效 token 一律 404 不做存在性预言。

## 相关文件清单（高信号）

- `app/main.py`、`single_port_app.py`、`app/config.py`、`app/db/models.py`
- `app/core/harness_v2_engine.py`、`app/harness/sandbox.py`、`app/harness/command.py`、`app/harness/skill_script.py`
- `app/channels/adapters/base.py`、`app/public_api/app.py`
- `app/tools/tool_executor.py`、`app/tools/external_tasks.py`、`app/tools/mcp_client.py`
- `app/general_skills/runner.py`、`app/general_skills/runtime_env.py`、`app/general_skills/schema.py`
- `app/knowledge/service.py`、`app/knowledge/okf.py`、`app/knowledge/parser.py`
- `app/teams/service.py`、`app/teams/wakeup.py`、`app/teams/sweeper.py`
- `app/security/artifact_share.py`
- `app/scheduled_tasks/schema.py`、`app/scheduled_tasks/service.py`
- `mock_servers/mcp_stdio_server.py`、`mock_servers/auth_matrix_service.py`
- `pyproject.toml`、`README.md`、`.env.example`

## 变更记录 (Changelog)

- 2026-09-17T10:09:30+08:00 深度补扫：新增 harness 沙箱执行内核深度章节（11 文件/4 沙箱后端/资源限制/安全机制/工具注册流程）、tools 工具与异步任务深度章节（3 工具类型/detached 生命周期/MCP 客户端/授权层次）、knowledge 分层知识检索深度章节（分层架构/OKF 格式/检索 6 步流程/入库 10 阶段/知识发现）、teams 多员工协作深度章节（9 状态机/HP 竞标制/黑板/唤醒租约/清扫器）、general_skills 运行时深度章节（6 阶段/venv 运行时/市场安装）、mock_servers 测试基础设施章节（3 个 mock 服务）。
- 2026-09-17T10:09:30+08:00 增量更新：数据模型 67 张表全清单、测试 152→157、新增技能市场 API（3 端点 + 多源导入）、产物签名分享（artifact_share.py）、定时任务 interval 调度+草稿系统+SOP 版本策略、security 子包细化（8 模块）、新增 FAQ。
- 2026-09-11T15:42:50 — 初始化架构师首次生成。
