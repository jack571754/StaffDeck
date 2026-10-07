# StaffDeck — 仓库级 AI 上下文（根）

> 当前分支：`feat/clean-data-query-and-interval`。**在途未提交改动**（2026-09-30 核实）聚焦四块：产物归属泛化 + 可分享 HTML 报告生成（`reporting/`）、飞书出站消息观测与撤回、定时任务孤儿回收/租约自愈与 next_run 推进、data_query 日期宏本地时区化。
> 本文件由“初始化架构师”生成/增量更新，配套 `.claude/index.json`。
> 另有一份英文 `AGENTS.md`（含 Windows 测试基线、提交规范）与 `README.md` / `README.zh.md`。

## 项目愿景

StaffDeck 由 OpenBMB / 清华-THUNLP / 东北大学-面壁联合实验室 / AI9Stars 联合研发，是一套**面向企业的数字员工（Digital Employee）构建与管理平台**，AGPL-3.0 开源。目标是把专业员工的经验、业务流程与判断标准固化为可持续工作的数字员工：支持状态机驱动的 SOP 流程技能、文档结构感知的知识检索、HTTP/MCP/定时任务自主执行，以及长期记忆、Trace、真人接管与反馈驱动的迭代闭环。

## 架构总览

单进程 FastAPI 应用（`5173` 端口同时提供 UI / API / Swagger），SQLModel+SQLite（`skill_agent_loop.db`）持久化，OpenAI Chat Completions 兼容模型服务为推理后端。

核心运行时链路（Harness v2）：用户消息 → Router 意图分发 → 能力发现/权限 → TaskFrame 规划与执行 → 技能/知识/工具/沙箱执行 → 事件流回放与 Turn 终决。IM 渠道（微信/企微/飞书/钉钉）通过渠道无关的适配器注册表接入，支持多员工调度、意图自动分发、身份合并与对话观测。

## ✨ 模块结构图（Mermaid）

```mermaid
graph TD
    A["(根) StaffDeck"] --> B["backend"];
    A --> C["frontend-enterprise"];
    A --> D["skills"];
    A --> E["scripts"];
    A --> F["packaging"];
    A --> G["docs"];

    B --> B1["app/core (Harness v2 内核)"];
    B --> B2["app/capabilities (能力契约)"];
    B --> B3["app/harness (沙箱执行)"];
    B --> B4["app/scheduled_tasks"];
    B --> B5["app/data_query"];
    B --> B6["app/security"];
    B --> B7["app/public_api"];
    B --> B8["app/channels"];
    B --> B9["app/tools / knowledge / llm"];
    B --> B10["app/reporting (可分享 HTML 报告)"];

    B4 --> B4a["scheduled_tasks/renderers (飞书卡片注册表)"];

    click B "./backend/CLAUDE.md" "查看 backend 模块文档"
    click C "./frontend-enterprise/CLAUDE.md" "查看 frontend-enterprise 模块文档"
    click D "./skills/CLAUDE.md" "查看 skills 模块文档"
    click E "./scripts/CLAUDE.md" "查看 scripts 模块文档"
    click F "./packaging/CLAUDE.md" "查看 packaging 模块文档"
    click G "./docs/CLAUDE.md" "查看 docs 模块文档"
    click B1 "./backend/app/core/CLAUDE.md" "查看 core 模块文档"
    click B2 "./backend/app/capabilities/CLAUDE.md" "查看 capabilities 模块文档"
    click B4 "./backend/app/scheduled_tasks/CLAUDE.md" "查看 scheduled_tasks 模块文档"
    click B4a "./backend/app/scheduled_tasks/CLAUDE.md" "查看 scheduled_tasks 模块文档"
    click B5 "./backend/app/data_query/CLAUDE.md" "查看 data_query 模块文档"
    click B10 "./backend/app/reporting/CLAUDE.md" "查看 reporting 模块文档"
```

## 模块索引

| 模块 | 路径 | 语言/栈 | 一句话职责 | 文档 |
|---|---|---|---|---|
| backend | `backend/` | Python / FastAPI / SQLModel / SQLite | 接口、Agent 运行时、存储、渠道与任务 Worker | [backend/CLAUDE.md](backend/CLAUDE.md) |
| — core | `backend/app/core/` | Python | Harness v2 内核：Turn 规划、TaskFrame、能力清单/渐进披露、能力调用、上下文投影、恢复、**产物归属解析**（`artifact_owners.py`） | [core](backend/app/core/CLAUDE.md) |
| — capabilities | `backend/app/capabilities/` | Python | 能力契约端口/适配器、注册表与不可变快照、本地知识/技能包实现 | [capabilities](backend/app/capabilities/CLAUDE.md) |
| — scheduled_tasks | `backend/app/scheduled_tasks/` | Python | 定时/周期任务：草稿、调度、租约、Harness v2 成功判定、pipeline 通道、**孤儿 run 回收与租约自愈** | [scheduled_tasks](backend/app/scheduled_tasks/CLAUDE.md) |
| — renderers | `backend/app/scheduled_tasks/renderers/` | Python | 飞书卡片渲染器注册表（销售卡 2.0 / 通用表）与数值格式化工具 | [renderers](backend/app/scheduled_tasks/CLAUDE.md) |
| — data_query | `backend/app/data_query/` | Python | 数据查询中心：数据源、查询模板、MySQL/HTTP 连接器、AES-GCM 加密 | [data_query](backend/app/data_query/CLAUDE.md) |
| — reporting | `backend/app/reporting/` | Python | 可分享 HTML 报告生成（generate 半边）：纯数据契约 → 渲染器注册表 → 扁平 spec → 单文件自包含 HTML → 原子落盘登记为交付物 + 签名分享链接；**生产入口 = 内建能力 `report_generate`** | [reporting](backend/app/reporting/CLAUDE.md) |
| frontend-enterprise | `frontend-enterprise/` | TS / React 18 / Vite 8 / Tailwind 4 / Vitest | StaffDeck 企业工作台（chat / dashboard / data-query / scheduled-tasks / channels）| [frontend-enterprise/CLAUDE.md](frontend-enterprise/CLAUDE.md) |
| skills | `skills/` | SKILL.md 定义 | 面向 Agent 的技能包：staffdeck-API 系列 + 固定流程模板 | [skills/CLAUDE.md](skills/CLAUDE.md) |
| scripts | `scripts/` | Python / PS / bash | 单端口服务生命周期（dev_up/down/status）入口 `dev.py` | [scripts/CLAUDE.md](scripts/CLAUDE.md) |
| packaging | `packaging/` | Python / PS / sh | macOS / Linux / Windows 桌面打包与签名 | [packaging/CLAUDE.md](packaging/CLAUDE.md) |
| docs | `docs/` | Markdown | 开放 API v1、教程与 Agent 协作约定（`.gitignore` 中，本地目录）| [docs/CLAUDE.md](docs/CLAUDE.md) |

## 任务 → 文件对照（跨模块速查）

| 我要改的功能 | 首选文件 |
|---|---|
| Agent 如何规划/执行一轮对话 | `backend/app/core/harness_v2_engine.py` → `turn_planner.py` → `task_frame_store.py` |
| 模型能看到/调用哪些能力 | `backend/app/core/capability_manifest.py`（授权）+ `capability_discovery.py`（8K 投影） |
| 某个内建能力（含 `data_query_search`/`data_query_execute`）行为 | `backend/app/core/harness_capability_invoker.py` `_invoke_internal` |
| 沙箱/命令/脚本执行语义 | `backend/app/harness/`（`sandbox.py`、`command.py`、`skill_script.py`、`executor.py`） |
| 定时任务调度/成功判定/假成功 | `backend/app/scheduled_tasks/service.py`、`worker.py` |
| 定时任务 pipeline 步骤与飞书卡片渲染 | `backend/app/scheduled_tasks/pipeline.py`、`scheduled_tasks/renderers/`（`__init__.py` 注册表 / `sales_card.py` / `generic_table.py`） |
| 数据查询模板/连接器/SQL 白名单 | `backend/app/data_query/service.py`、`executor.py`、`connectors/mysql_connector.py` |
| 「今天/昨天/前天」等日期宏（`date` 型模板参数） | `backend/app/data_query/executor.py` `_validate_params`（**本地 CST/UTC+8**，2026-09 起由 UTC 改） |
| 产物归属（谁拥有这个交付物）/ 下载·预览·分享鉴权 | `backend/app/core/artifact_owners.py`（resolver 注册表）、`backend/app/harness/artifacts.py` `artifact_owner_pair`（唯一定义）、`backend/app/security/artifact_share.py`（签名 token） |
| 生成可分享 HTML 报告（独立单页） | `backend/app/reporting/`（`spec.py` 扁平配置 → `renderers/` → `html.py` 渲染 → `writer.py` 落盘登记）；**生产入口 = 内建能力 `report_generate`**（`core/capability_manifest.py` 声明 + `core/harness_capability_invoker.py` `_generate_report`）；**前端入口 = 聊天产物卡片的「预览/分享」**（`frontend-enterprise/src/pages/chat/components/HarnessArtifactDownloads.tsx`） |
| 定时任务孤儿 run 回收 / 租约自愈 / next_run 推进 | `backend/app/scheduled_tasks/service.py`（`reap_stale_scheduled_task_runs`、`_finish_task_schedule`、`due_scheduled_tasks`）、`worker.py` |
| 飞书出站消息观测与撤回 | `backend/app/api/channels.py`（`/feishu/messages*`）、`backend/app/channels/adapters/feishu.py` `recall_message`、表 `feishu_outbound_messages`、前端 `pages/channels/FeishuMessagesTab.tsx` |
| 权限/租户/加密/内部令牌 | `backend/app/security/permissions.py`、`tenant.py`、`encryption.py`、`internal_service.py` |
| 开放 API v1 端点与鉴权 | `backend/app/public_api/`（`app.py`、`auth.py`、`credential_profiles.py`、各资源路由） |
| IM 渠道接入 | `backend/app/channels/`（`adapters/base.py` + 各平台适配器） |
| 知识检索 | `backend/app/knowledge/`（`service.py`、`okf.py`、`parser.py`） |
| 数据表/迁移 | `backend/app/db/models.py`（唯一表模型来源）、`db/seed.py`（演示数据） |
| 前端页面/路由 | `frontend-enterprise/src/enums/routes.ts` + `src/App.tsx` + `src/pages/<域>/` |
| 前端 API 调用 | `frontend-enterprise/src/api/client.ts`、`src/api/data-query.ts` |
| 前端文案 | `frontend-enterprise/src/i18n/en.json`（+ `npm run i18n:check`） |
| 服务启停/生命周期 | `scripts/dev.py`（统一入口）、`scripts/dev_supervisor.py` |

## 关键不变量与边界（勿踩）

- **表模型单一来源**：所有 SQLModel 表在 `backend/app/db/models.py`；不要在别处新建 `table=True` 模型。
- **敏感配置加密落库**：渠道凭证、数据库口令等必须 `enc:` 前缀（AES-256-GCM / Fernet）；任何接口不回传明文。
- **能力授权不可由投影放宽**：`core/capability_discovery.py` 只裁剪上下文，绝不给模型新增授权（授权源是 `capability_manifest.py` 的冻结快照）。
- **能力注册表启动期 seal**：`capabilities/registry.py` `seal()` 后禁止注册；运行期只读快照。
- **SQL 白名单**：`data_query/connectors/mysql_connector.py` `_is_sql_allowed`；已知 `WITH ... DELETE` 绕过风险（P0）。
- **skill 运行时代码在数据库不在仓库**：通用技能包（`GeneralSkill` 表）经 Harness 物化到沙箱 `.harness/skill-packages/`，仓库里看不到运行副本。
- **定时任务成功判定只信持久化记录**：`scheduled_tasks/service.py` 的 `_scheduled_harness_outcome` / `_scheduled_business_failures`，绝不只信模型答复文本。
- **pipeline 通道仍是半产品化（本分支热点）**：`scheduled_tasks/renderers/__init__.py` `get_card_renderer(None)` 与 `pipeline.py` 缺省 `renderer_name` 均落到 `sales_card`；引擎对每个 query 步骤无条件注入 `params["is_first_push"]`；`pipeline_steps` 在 `schema.py` 中零校验（无 `process` 步骤类型、无 renderer 枚举），前端 `ScheduledTaskEditorPage.tsx` 无 pipeline 入口。非销售 pipeline 任务须在步骤 JSON 显式指定 `renderer`，且目前只能经 API 手搓 JSON 或写库创建。
- **产物归属单一来源**：`harness/artifacts.py` 的 `artifact_owner_pair(raw)` 是 `(owner_kind, owner_id)` 的唯一定义，下载/预览/分享三条链路共用；旧 manifest 只带 `task_frame_id` 时读作 `("harness_frame", id)`。已注册 kind 仅 `harness_frame`（TaskFrame）与 `scheduled_run`（确定性 pipeline run）。
- **签名层不得依赖业务层**：`security/artifact_share.py` **不得** import `app.core`；它只校验 `owner_kind` 是 `^[a-z][a-z0-9_]{0,31}$` 的 slug，owner 是否存在/属于本租户本会话由 `core/artifact_owners.py` 的 resolver 判定，**任何失败一律收敛为 `None` → 404**（防存在性/租户预言机）。半对形状（有 kind 无 id / 有 id 无 kind）的 token 直接 404，不回退解释。
- **报告写路径与读路径必须同源**：报告目录只能经 `core/harness_session_cleanup.py`（`harness_owner_workspace_root` / `harness_reports_root` / `_validated_workspace_subdir`，均拒绝 symlink 组件）取得；不同源会导致「写得进、`open_harness_artifact` 读 404」。报告是单文件自包含 HTML（内联 CSS/JS/SVG，零外部资源），体积上限 20MiB（publish 侧 25MiB）。**报告分享链接绝不写入 run 的 `trace_json`**（该字段权限门槛低于读会话）。
- **`reporting/` 已接入会话链路与前端（2026-09-30）**：内建能力 `report_generate`（`harness_frame` owner）把扁平 spec 渲染成 `reports/*.html` 并登记为交付物；落库有两条路互为兜底——能力返回的 `artifacts`（`harness_agent.py` 并入）与 Harness 工作区自动发现（`reports/*.html` 属用户可见路径）。**前端**聊天产物卡片对 `.html` 显示「预览/分享」：预览调 `POST /api/chat/artifacts/share-link` 铸造签名 token 后用**相对路径**新标签打开（不受 `TOOL_BASE_URL` 写死 host 影响），分享复制后端绝对 URL。**仍未接线**：`scheduled_tasks/pipeline.py` 的 `scheduled_run` 报告步骤。
- **定时任务自愈阈值与租约同值（均 900s）**：`reap_stale_scheduled_task_runs` 只看 run 年龄，**不看 `lease_owner`/持有者是否存活**，因此 >900s 的合法长任务可能在飞行中被判 `failed` 并清租约 → 原执行收尾又写 `succeeded`（状态翻转 + 双结论消息）且租约可被第二个 worker 重新领取。`worker.py` 启动时还会**无条件**清空所有 `lease_until IS NOT NULL` 的租约（不判是否过期）。
- **pipeline 未知步骤类型会静默“成功”**：`scheduled_tasks/pipeline.py` 步骤循环只识别 `query` 与 `skill_notify`/`feishu_notify`/`notify`，**无 `else` 兜底**；`{"type":"process"}` 之类被忽略，run 仍写「✅ 流水线执行成功」——假成功的新入口。
- **飞书出站记录目前只有 mock 写入**：`feishu_outbound_messages` 的 `db.add` 全部在 `api/mock.py`（`feishu_app_notify`），真实发送链路尚未落库 → 消息管控页与撤回在生产上**无数据源**。
- **不改源码**：文档体系只读代码、只写 `CLAUDE.md` 与 `.claude/index.json`。

## 运行与开发

- 环境：Python 3.11+、Node 20+；OpenAI 兼容模型接口。
- 初始化：`backend/.venv` + `pip install -e "backend[dev]"`；`npm --prefix frontend-enterprise ci`；复制 `backend/.env.example` → `backend/.env`（须配置 `DEMO_MODEL_*` 与强随机 `APP_SECRET`）。
- 启动：`scripts/dev_up.sh --detach`（Windows：`.\scripts\dev_up.ps1 --detach`），统一入口 `python scripts/dev.py up --detach`；停止/状态 `dev_down` / `dev_status`。
- 验证：`curl http://127.0.0.1:5173/api/health` → `{"status":"ok"}`；UI 从 `/workspace/gallery` 进入。
- 默认管理员 `admin` / `admin`。

## 测试策略

- backend：`pytest`，测试目录 `backend/tests/`（169 个 `test_*.py`）。
- **Windows 已知本机失败基线**：仓库根 `AGENTS.md`「Known environment-specific test failures (Windows)」（2026-09-16 快照 48 failed / 2198 passed）——先读它再判断失败归属。
- frontend：`vitest run`，同址 `*.test.ts(x)`（Testing Library + jsdom）。
- lint：backend `ruff`（line-length 100）；frontend `tsc -b`/`vite build`、`i18n:check`、`config:check`。

## 编码规范

- 后端：FastAPI + SQLModel；路由收口在 `app/api/`，逻辑在对应 `app/<domain>/service.py`；SQL 表模型集中在 `app/db/models.py`；禁止硬编码本机路径，环境变量经 `app/config.py`。
- 前端：React 函数组件 + shadcn/Radix UI；路由常量 `src/enums/routes.ts`；同源 API 走 `src/api/client.ts`；文案进 i18n。
- 安全：敏感配置 AES-256-GCM 加密落库（`enc:` 前缀）；任何接口不回传明文。

## AI 使用指引

- 先读本文件，再按「任务 → 文件对照」定位，最后读对应模块 `CLAUDE.md`（顶部面包屑可回根）。
- `.claude/index.json` 记录覆盖缺口与下一步；`next_steps` 即未深读清单，优先按它续扫。
- 涉及 `scheduled_tasks` / `data_query` / `reporting` / 产物归属（`core/artifact_owners.py`）的改动是当前分支热点，先读对应模块 docs 与记忆沉淀（销售播报、定时任务假成功、SQL 白名单绕过、skill 子进程环境隔离等经验）。
- 本轮在途改动**尚未回灌到已提交历史**（工作区 35 项未提交 + 若干新文件/新包），文档已按磁盘现状而非 HEAD 描述；改动被提交或回滚后需再校准一次。
- 大目录（`node_modules/`、`backend/.venv/`、`packaging/sandbox_runtime/`、`.kilo/worktrees/`、`.harness/`（运行期会话工作区，**未进 `.gitignore`**，会持续出现在 `git status`）、日志与 `*.db*`）默认忽略。

## 变更记录 (Changelog)

- 2026-09-30T18:55 — **前端入口打通**：聊天产物卡片 `HarnessArtifactDownloads` 对 `.html` / `text/html` 产物新增「预览」「分享」两个动作（预览 = `share-link` 铸造 + **相对路径**新标签打开，绕开 `TOOL_BASE_URL` 写死的 host；分享 = 复制后端绝对 URL）；新增 4 个单测（含失败路径），`vitest` 8/8、`i18n:check`（3798 条）、`config:check`、`tsc -b` + `vite build` 通过；重启应用后新包上线并复验 share-link + 匿名打开仍 200。
- 2026-09-30T18:45 — **HTML 独立页面真机验证通过**：`python scripts/dev.py up` 启动单端口应用（5173）后，用 admin/admin + demo 模型走完整链路——会话内模型真实调用 `report_generate` → 产物 `reports/report_*.html` 落进 assistant 消息清单 → 下载端点 200/`text/html` → `POST /api/chat/artifacts/share-link` 铸造链接 → **匿名** `GET /api/chat/artifacts/view/{token}` 打开成功，10/10 检查通过；修正 `reporting/CLAUDE.md` 中写错的分享端点路径。
- 2026-09-30T18:30 — **接线 HTML 独立页面（最小闭环）**：新增 `backend/app/reporting/spec.py`（扁平 spec → `ReportDocument`）与内建能力 **`report_generate`**（`core/capability_manifest.py` 声明 + 保留名、`core/capability_discovery.py` 始终展开、`core/harness_capability_invoker.py` `_generate_report`）；产出走既有产物链路（自动发现/能力 artifacts → assistant 消息清单 → `core/artifact_owners.py` resolver → 下载/预览/签名分享），**无需新路由**；新增 `backend/tests/test_reporting_capability.py` 覆盖端到端闭环（相关 92 个测试通过）；据实修正 `reporting/` 三处文档的「尚无生产调用方」表述（模块索引 / 任务对照 / 不变量）。
- 2026-09-30T16:05 — 初始化架构师（增量，聚焦**未提交的在途改动**）：**新建** `backend/app/reporting/CLAUDE.md`（新包，并登记进 Mermaid + 模块索引）；登记产物归属泛化（`core/artifact_owners.py` + `harness/artifacts.py` `artifact_owner_pair` + `artifact_share.py` token 泛化）；「任务 → 文件对照」新增 5 行（日期宏 / 产物归属 / HTML 报告 / 定时任务自愈 / 飞书出站消息撤回）；「关键不变量与边界」新增 6 条（归属单一来源、签名层不依赖业务层、读写同源与 trace_json 禁令、`reporting` 无生产调用方、自愈阈值=租约 900s 的误判风险、pipeline 未知步骤静默成功、飞书出站记录只有 mock 写入）；`frontend-enterprise` 模块行补 channels；忽略目录补 `.harness/`；深度更新 `core/`、`scheduled_tasks/`、`data_query/`、`frontend-enterprise/`、`backend/` 文档；**修复** `backend/app/scheduled_tasks/architecture_analysis.md` 被损坏的 5 行目录（恢复为 HEAD 内容）；刷新 `.claude/index.json`。
- 2026-09-24T16:45 — 初始化架构师（增量，聚焦定时任务 pipeline 通道）：登记新 `backend/app/scheduled_tasks/renderers/` 包（Mermaid 节点 + 模块索引行）；「任务 → 文件对照」拆分定时任务行并新增「pipeline 步骤与飞书卡片渲染」；「关键不变量与边界」新增 pipeline 半产品化条目（默认渲染器为销售卡、`is_first_push` 无条件注入、`pipeline_steps` 零校验、前端无入口）；据提交 `042333ee` 修正事实（销售卡已抽包、`run.py` 已共用同一实现、聊天总结改用 `task.title`）；深度更新 `scheduled_tasks/` 模块文档；刷新 `.claude/index.json`（generated_at = 2026-09-24T16:45:22+08:00）。
- 2026-09-24T09:46 — 初始化架构师（增量）：**新建** `backend/app/core/CLAUDE.md`、`backend/app/capabilities/CLAUDE.md`；扩展 Mermaid 结构图与模块索引（登记 core/capabilities）；新增「任务 → 文件对照」与「关键不变量与边界」两节；深度更新 `backend/`、`data_query/`、`scheduled_tasks/`、`frontend-enterprise/` 文档；修正 Windows 测试基线路径为仓库根 `AGENTS.md`；刷新 `.claude/index.json`（generated_at = 2026-09-24T09:46:04+08:00）。
- 2026-09-23T18:06 — 初始化架构师（增量）：重建根级 CLAUDE.md；新增 Mermaid 结构图；新增 `data_query` 模块文档与导航面包屑；更新 `.claude/index.json`。
