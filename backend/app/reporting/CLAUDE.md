[根目录](../../../CLAUDE.md) > [backend](../../) > [app](../) > **reporting**

# reporting — 可分享 HTML 报告生成（generate 半边）

## 模块职责

把查询结果（或任意行数据）渲染成**单文件自包含 HTML 报告**，原子落盘到 Harness 会话工作区、登记为已发布交付物（published artifact），并可签发短期 HMAC 签名分享链接。

本包只做**生成**半边；**分享/下载**半边早已存在：`app/security/artifact_share.py`（签名 token）+ `app/api/chat.py`（`download_harness_artifact` / `view_published_artifact` / `mint_artifact_share`）+ `app/core/artifact_owners.py`（owner 语义解析）。

> **生产接入（2026-09-30 完成最小闭环）**：内建能力 **`report_generate`**（`core/capability_manifest.py` 声明 + `core/capability_discovery.py` 始终展开 + `core/harness_capability_invoker.py` `_generate_report`）已把本包接上生产：模型在会话里调用它 → 本包渲染并写入帧工作区 `reports/*.html` → Harness 自动发现/能力返回的 `artifacts` 挂到 assistant 消息 → 复用既有下载/预览/签名分享端点。
> **仍未接入**：`scheduled_tasks/pipeline.py`（确定性 pipeline 的 `scheduled_run` 归属）尚无报告步骤，`scheduled_run` owner 目前只有测试驱动。

## 边界与依赖方向

- 依赖：`app.harness`（`publish_harness_artifacts`、`ARTIFACT_OWNER_DEFAULT_KIND`）、`app.core.harness_session_cleanup`（工作区布局）、`app.db.models`、`app.security.artifact_share`、`app.config`。
- **调用方**：`app.core.harness_capability_invoker._generate_report`（内建能力 `report_generate`，惰性 import 本包）。除此之外无人 import 本包；本包也**不** import `api/chat`，**不** import `core.artifact_owners`。
- 分层：`document`/`format`/`assets`/`html`/`renderers`/`spec` 是纯函数；**唯一有副作用（DB + 文件系统）的是 `writer.py`**；`link.py` 只签名。

## 文件

| 文件 | 职责 |
|---|---|
| `__init__.py` | 包门面：`__all__` 再导出全部公开符号（`write_report_html`、`mint_report_share_link`、`render_report_html`、`validate_document`、`json_safe`、`get_report_renderer`、`build_*_report`、`format_*`、`ReportDocument` 等） |
| `document.py` | 纯数据契约（无 IO / DB / HTML）：`BlockKind ∈ {heading, markdown, kpi_row, table, bar_chart, line_chart, divider, note}`、`DeltaTone`、`ReportMeta` / `ReportDataset` / `KpiItem` / `ReportBlock` / `ReportDocument`、`json_safe`、`validate_document`、`iter_referenced_datasets`；上限 `MAX_DATASETS=8`、`MAX_BLOCKS=40`、`MAX_DATASET_ROWS=500`、`MAX_COLUMNS=40`；`GENERATOR_ID="staffdeck.reporting/1"` |
| `format.py` | 中性数值格式化（刻意**不复用** `scheduled_tasks` 的飞书颜色宏）：`to_float` / `format_metric_value` / `format_metric_delta` / `format_number` / `format_cell` / `format_bytes`；`DEFAULT_UNIT="万"` |
| `assets.py` | 内联 CSS/JS 字符串常量（做成 Python 常量以规避 PyInstaller `datas` 白名单）：`REPORT_CSS` / `REPORT_JS`；常量内不得出现 `</` |
| `html.py` | **唯一序列化器 + 唯一转义出口**：`render_report_html`（先 `validate_document`）、`build_island`（仅元数据，不含行数据）、`ISLAND_ID="staffdeck-report-data"`、`_esc` / `_json_island`（`<` `>` 全部 `\u` 转义）；图表是服务端内联 SVG（bar / line），**零外部资源** |
| `renderers/` | rows → `ReportDocument` 的**注册表**：`REPORT_RENDERERS`（`generic_table`/`table` → 通用表；`sales_report`/`sales_card`/`sales` → 销售报告）、`get_report_renderer`、`report_renderer_names`、`DEFAULT_RENDERER_NAME="generic_table"`；`base.py`（`ReportRenderer`、`coerce_rows`、`rows_to_dataset`）、`generic_table.py`、`sales_report.py` |
| `spec.py` | **扁平 spec → `ReportDocument`**（`build_report_from_spec`、`SPEC_KEYS`、`MAX_SPEC_ROWS=5000`）：把 `{title, subtitle, renderer, rows, columns, column_labels, summary, kpis, source, max_rows}` 交给注册表渲染器，再在顶部补 `markdown`（summary）与 `kpi_row`（kpis）块；未知字段/类型错误/空标题一律抛 `ReportDocumentError`。**这是模型、pipeline 步骤与 HTTP 调用方唯一需要知道的形状**——没有人再手搓 dataset 与 block |
| `writer.py` | **唯一触 DB / 文件系统**：`write_report_html` → `PublishedReport`、`report_file_name`；`MAX_REPORT_BYTES=20MiB`、`_PUBLISH_MAX_FILE_BYTES=25MiB`、`_TEMP_PREFIX=".tmp-"`、`_REPORT_OPERATION="report_generation"`、`REPORT_CONTENT_TYPE="text/html; charset=utf-8"` |
| `link.py` | 分享链接签发：`mint_report_share_link` → `ReportShareLink`（`url` / `token` / `expires_at` / `remaining_seconds`）；**只签名，不校验存在性与权限** |
| `errors.py` | `ReportError` / `ReportDocumentError(ReportError, ValueError)` / `ReportRenderError` |

## 关键机制与不变量

- **布局单一来源**：报告目录由 `core/harness_session_cleanup.py` 决定（`harness_owner_workspace_root` / `harness_reports_root` / `_validated_workspace_subdir`），**写路径与读路径必须同源**——否则会出现「写得进、`open_harness_artifact` 读 404」。这些 helper 同时拒绝 symlink 路径组件。
- **落盘布局**（artifact 里的 `path` 相对 **workspace root**）：
  - `harness_frame` owner：`<session>/<frame-segment>/reports/<id>.html`（相对路径 `reports/<id>.html`，报告对帧内后续 `exec_command` 可见）；
  - 其他 owner（当前即 `scheduled_run`）：`<session>/reports/<owner-segment>/<id>.html`（相对路径 `reports/<owner-segment>/<id>.html`）。
  - `reports` 段与帧目录名（`-<12 hex>` 结尾）不可能冲突。
- **原子写**：先写 `.tmp-<id>.html` → `fsync` → `os.replace`（同文件系统内原子）；`.tmp-` 前缀会被 Harness 自动发现跳过（见 `core/harness_capability_invoker.py` 的 `.tmp-` 过滤）。
- **体积双上限**：渲染结果 > `MAX_REPORT_BYTES`(20MiB) 抛 `ReportRenderError`；`publish_harness_artifacts` 侧另设 25MiB，避免静默继承其 50MiB 默认值。
- **默认渲染器是中性表，不是销售卡**：`get_report_renderer(None)` 回退 `generic_table`（未知名同样回退 `generic_table`）——这与 `scheduled_tasks.renderers.get_card_renderer(None)` 落到 `sales_card` 的已知 P1 债**刻意相反**，改这里时不要"对齐"回去。
- **报告即交付物**：`write_report_html` 把 `owner_kind` / `owner_id` / `display_name` / `content_type` / `description` 写进 artifact dict，再由调用方挂进 `Message.metadata_json["harness_artifacts"]`，从而**复用既有的下载/预览/分享端点，无需新路由**。
- **分享链接不得写入 `ScheduledTaskRun.trace_json`**：该字段可被任何能列出 runs 的人读到，权限门槛低于读会话（`link.py` 顶部注释）。
- **XSS/离线约束**：唯一转义出口 + 数据岛 `<` `>` 全 `\u` 转义 + 内联资源不含 `</` + 零外部资源（CSS/JS/图表全部内联，禁用 JS 仍可读）。

## 生产接入：`report_generate` 内建能力

| 环节 | 位置 |
|---|---|
| 能力声明 | `core/capability_manifest.py`（`builtin.reporting.generate`、`side_effect="write"`、`required=["title"]`）并列入 `RESERVED_HARNESS_CAPABILITY_NAMES`；`core/capability_discovery.py` 列入 `ALWAYS_EXPANDED_CAPABILITIES`（模型始终可见 schema，无需 `capability_search`） |
| 分发 | `core/harness_capability_invoker.py` `_invoke_internal` → `if name == "report_generate": return self._generate_report(arguments)` |
| 生成 | `_generate_report`：`build_report_from_spec(arguments, tenant_id=…)` → `write_report_html(owner_kind="harness_frame", owner_id=self.task_frame_id, session_id=self.session.id)`；返回 `{success, data:{path, display_name, size, sha256, notice}, artifacts:[…]}`；`ReportError`/`OSError` 转成 `REPORT_GENERATION_ERROR` 失败而不是抛异常 |
| 落库 | 两条路，互为兜底：① 能力结果里的 `artifacts` 由 `core/harness_agent.py` 并入 AgentLoop 产物 → 引擎写进 assistant 消息的 `metadata_json["harness_artifacts"]`；② `invoker.discover_artifacts()` 在 `reports/*.html` 属用户可见路径，自动发现同一文件（`_aggregate_artifacts` 按 `type|task_frame_id|path` 去重） |
| 打开 | 无需新路由（**真机已验证 2026-09-30，前端入口已接入**）：下载 `GET /api/chat/sessions/{session_id}/artifacts/{task_frame_id}?tenant_id=…&path=reports/…`；铸造签名链接 `POST /api/chat/artifacts/share-link`（body：`tenant_id` / `session_id` / `task_frame_id` / `path`，可选 `owner_kind` / `ttl_seconds`）→ `GET /api/chat/artifacts/view/{token}` 可**匿名**打开；会话产物卡片对 `.html` 直接提供「预览/分享」（`frontend-enterprise/src/pages/chat/components/HarnessArtifactDownloads.tsx`，预览用相对路径，避开 `TOOL_BASE_URL` 写死的 host）；owner 语义走 `core/artifact_owners.py` |

闭环由 `backend/tests/test_reporting_capability.py` 覆盖：能力调用 → 文件落盘 → 挂消息清单 → `resolve_artifact_owner` → 摘要/大小一致 → 铸造并解码分享链接 → 匿名解析同一文件 → 自动发现包含该路径 → `reports/` 无 `.tmp-` 残留。

**真机验证（2026-09-30，`python scripts/dev.py up` 后的单端口应用 5173）**：登录 admin/admin（`tenant_demo`）→ 建会话（**必须带 `agent_id`**）→ `POST /api/chat/turn` 让 demo 模型生成报告 → 消息清单里出现 `reports/report_*.html`（`owner_kind=harness_frame`、`display_name`「…​.html」、`content_type=text/html`、`operation=report_generation`）→ 下载端点 200/`text/html`/字节数一致 → `share-link` 200 → **不带任何认证**打开 `/api/chat/artifacts/view/{token}` 得 200 且正文含报告标题与数据。10/10 检查通过。

## 任务 → 文件对照（AI 定位用）

| 想改什么 | 去看 |
|---|---|
| 报告支持哪些块类型 / 数量上限 / 文档校验规则 | `document.py`（`BlockKind`、`BLOCK_KINDS`、`validate_document`、`MAX_*`） |
| 模型/调用方传来的扁平配置怎么变成文档 | `spec.py`（`build_report_from_spec`、`SPEC_KEYS`） |
| 会话里怎么触发报告生成 | `../core/capability_manifest.py`（`report_generate` 声明）+ `../core/harness_capability_invoker.py` `_generate_report` |
| 新增一种报表渲染器 | `renderers/__init__.py` 的 `REPORT_RENDERERS` + 新模块（照 `generic_table.py` 写） |
| 销售报告口径（品牌/店铺/24h 走势、KPI） | `renderers/sales_report.py`（`_classify_rows`、`_build_kpis`、`_build_rank_blocks`、`_build_brand_dataset`、`_build_hourly_dataset`） |
| HTML 结构 / 样式 / 图表 / 转义 | `html.py`、`assets.py` |
| 报告落在哪、叫什么名字 | `writer.py` + `../core/harness_session_cleanup.py`（`harness_reports_root`） |
| 分享链接 TTL / URL 形态 | `link.py`、`../security/artifact_share.py` |
| 下载 / 预览 / 铸造链接端点、完整性校验 | `../api/chat.py`（`download_harness_artifact`、`view_published_artifact`、`mint_artifact_share`）；owner 语义在 `../core/artifact_owners.py` |
| 产物归属定义（`owner_kind`/`owner_id`） | `../harness/artifacts.py` 的 `artifact_owner_pair`（唯一定义） |

## 渲染器注册表

- 注册方式：`REPORT_RENDERERS: dict[str, ReportRenderer]`（名称别名 → 构造函数），分发点 `get_report_renderer(name)`；**无 `seal()`、无重复注册校验**（与 `capabilities/registry.py` 的 seal 语义不同）。
- 契约：`ReportRenderer = Callable[..., ReportDocument]`，**不做签名强制**。
- 两道降级：未知名字 → `generic_table`；销售数据缺失/非销售行 → `sales_report` 内部降级为通用表。

## 测试与质量

- `backend/tests/test_reporting_renderer.py`：`json_safe` 类型归一化、XSS 转义与数据岛无裸尖括号、零外部资源、内联 SVG 图表、非法文档报 `ReportDocumentError`、默认渲染器为中性表、截断提示与首推语义。
- `backend/tests/test_reporting_writer.py`：`scheduled_run` / `harness_frame` 报告经 `resolve_artifact_owner` + `open_harness_artifact` 往返（sha256/size/正文）、落盘于 `reports/<segment>`、无 `.tmp-` 残留、超限在写前失败、`mint_report_share_link` 的 owner 对与 TTL。
- `backend/tests/test_reporting_capability.py`：**端到端闭环**（`report_generate` → 落盘 → 挂消息清单 → resolver → 摘要/大小一致 → 签名分享链接 → 匿名解析 → 自动发现 → 无 `.tmp-` 残留），外加 `spec.py` 的正反用例与能力注册（reserved / always-expanded / `available` / `side_effect=write`）。
- 相关：`test_artifact_owners.py`、`test_artifact_share_token_owner.py`、`test_artifact_share_preview.py`。
- 规范：`ruff`（line-length 100）。

## 常见问题 (FAQ)

- **报告写成功但预览/下载 404**：先看 `owner_kind` 是否已在 `core/artifact_owners.py` 注册（未注册 resolver 直接返回 `None` → 全 404），再看 token 里的 owner 对是否与 `Message.metadata_json["harness_artifacts"]` 中的条目一致。
- **想验证闭环**：跑 `pytest backend/tests/test_reporting_capability.py`（能力 → 落盘 → 清单 → 分享链接 → 打开）；或在会话里让模型调 `report_generate`，然后在聊天产物区下载/分享。
- **想让定时任务 pipeline 直接出 HTML 报告**：尚未接线。pipeline 的 notify 步骤可复用 `build_report_from_spec` + `write_report_html(owner_kind="scheduled_run", owner_id=run.id)`，但要注意分享链接不得写入 `trace_json`。
- **为什么不用 Markdown**：报告是单文件 HTML 快照（内联 CSS/JS/SVG），可离线打开、可签名分享；Markdown 由聊天侧单独渲染。

## 已知风险 / 待办（本次核实）

| 级别 | 问题 | 锚点 |
|---|---|---|
| ~~P0~~ | ~~无生产调用方~~ **已解决（2026-09-30）**：`report_generate` 内建能力已接线；`scheduled_run`（pipeline）路径仍未接 | `core/harness_capability_invoker.py` `_generate_report` |
| P2 | `write_report_html(file_name=...)` 未做路径段校验，含 `/` 或 `../` 可越出 owner 目录（逃出 workspace 时 `relative_to` 抛未捕获 `ValueError`） | `writer.py:86,103,113-121` |
| P2 | owner resolver 注册表靠 **import 副作用**建立，`app/core/__init__.py` 不导出 → 新入口若未 import 该模块，registry 为空则全 404 | `core/artifact_owners.py:223-224` |
| P2 | 体积上限在全量内存渲染**之后**判定（无字符级预估） | `writer.py:105-110` |
| P3 | `DEFAULT_UNIT` 硬编码 `"万"`，非万元口径数据会被误标单位 | `format.py:18` |
| P3 | 销售报告与 `scheduled_tasks.renderers.sales_card.build_sales_feishu_card` 的分类/列名**双份硬编码**，仅靠注释要求同步，无一致性测试 | `renderers/sales_report.py:6-9` |
| P3 | `spec.build_report_from_spec` 的 `rows` 上限 5000（`MAX_SPEC_ROWS`）只挡输入；真实渲染行数仍由 `MAX_DATASET_ROWS=500` 截断 | `spec.py` |

## 相关文件清单

`__init__.py`、`document.py`、`format.py`、`assets.py`、`html.py`、`spec.py`、`writer.py`、`link.py`、`errors.py`、`renderers/{__init__,base,generic_table,sales_report}.py`；外部：`app/harness/artifacts.py`（`artifact_owner_pair`）、`app/core/harness_session_cleanup.py`、`app/core/artifact_owners.py`、`app/core/published_deliverables.py`、`app/core/harness_capability_invoker.py`（`report_generate`）、`app/security/artifact_share.py`、`app/api/chat.py`。

## 变更记录 (Changelog)

- 2026-09-30T18:55 — **前端入口接入**：会话产物卡片（`frontend-enterprise/src/pages/chat/components/HarnessArtifactDownloads.tsx`）对 HTML 报告增加「预览/分享」（预览 = `share-link` + 相对路径新标签打开；分享 = 复制后端绝对 URL），无需新后端路由。
- 2026-09-30T18:45 — **真机验证通过**（单端口应用 5173，admin/admin，demo 模型真实调用 `report_generate`）：产物落库、下载、铸造分享链接、**匿名打开**共 10 项检查全绿；据此修正「打开」一行中写错的端点（真实为 `POST /api/chat/artifacts/share-link`，下载需带 `session_id`）。
- 2026-09-30T18:30 — 接线生产最小闭环：新增 `spec.py`（扁平 spec → `ReportDocument`）；新增内建能力 **`report_generate`**（manifest 声明 + always-expanded + invoker `_generate_report`）把本包接上会话链路，复用既有下载/预览/签名分享端点（无需新路由）；新增 `test_reporting_capability.py` 覆盖闭环（92 个相关测试通过）；更新「生产接入」一节与风险表（P0「无生产调用方」标记为已解决，pipeline 路径仍未接）。
- 2026-09-30T15:50 — 初始化架构师**新建**：登记 `reporting/` 包（document/format/assets/html/renderers/writer/link/errors）；记录「generate 半边」定位、与 `artifact_share`/`artifact_owners` 的边界、落盘布局与原子写、体积双上限、中性默认渲染器；标注**尚无生产调用方**及 6 项风险。
