[根目录](../../../CLAUDE.md) > [backend](../../) > [app](../) > **data_query**

# data_query — 数据查询中心

## 模块职责

企业数据查询中心：以“数据源 + 查询模板”为核心，支持 MySQL / HTTP 两种连接器，提供 SQL 模板语义化、连接测试、模板试运行与执行、自然语言意图路由、日期表达式解析，并对敏感配置做 AES-256-GCM 加密。当前分支热点（`feat/clean-data-query-and-interval`）。

## 入口与文件

| 文件 | 职责 |
|---|---|
| `api.py` | REST 路由，`APIRouter(prefix="/api/enterprise/data-query")`，全部需登录并按租户隔离；管理端点要求租户管理员 |
| `service.py` | 领域逻辑：数据源 / 模板 CRUD（`create_data_source`/`update_data_source`/`list_data_sources`）、连接测试（`test_data_source_connection`）、试运行（`test_query_template`）、执行（`execute_query_by_id`）、临时查询（`execute_adhoc_query`）、schema 刷新（`refresh_schema_cache`）、模板版本（`list_template_versions`/`rollback_template_version`） |
| `models.py` | SQLModel 模型：表 `DataSource` / `QueryTemplate` / `QueryTemplateVersion`；请求/响应 `DataSourceCreate/Update/Read`、`QueryTemplateCreate/Update/Read`、`QueryTemplateVersionRead`、`QueryExecuteRequest`/`QueryExecuteResult`、`TableSummary`、`ColumnMeta`、`TablePreviewResult`、`AdhocTestRequest` |
| `executor.py` | 模板执行器：`QueryExecutor`、`_validate_params`（参数校验）、`_QueryCache`（缓存 + `invalidate_template_cache`、`_make_cache_key`）、`_clean_row`（Decimal/日期归一化） |
| `intent_router.py` | 自然语言 → 模板意图路由：`route_question`、`_call_router_llm`、`RouteResult`、`INTENT_ROUTER_PROMPT`、模板元数据缓存（`get_cached_templates` / `clear_intent_template_cache`，`_CACHE_TTL_SECONDS=300`） |
| `date_resolver.py` | 日期/时间参数解析：`resolve_date_expression`、`resolve_date_range`、`compute_prev_period_date`、`_parse_explicit_date` |
| `authorization.py` | `authorized_data_source_ids`：当前用户可访问的数据源集合 |
| `security.py` | AES-256-GCM 加密（`encrypt_value`/`decrypt_value`/`encrypt_config`/`decrypt_config`；`enc:` 前缀，APP_SECRET 派生 key，12 字节 nonce，base64(nonce+ct+tag)） |
| `connectors/base.py` | `BaseConnector`、`QueryResult` 抽象 |
| `connectors/mysql_connector.py` | MySQL 连接器：`MySQLConnector`、`_is_sql_allowed`（`_SQL_ALLOW_PATTERN` 白名单，`SQLNotAllowedError`）、注释剥离 `_strip_comments`、`:param` 转换 `_convert_params` |
| `connectors/http_connector.py` | HTTP 连接器：`HttpApiConnector`、路径/查询参数替换 `_substitute_path_and_query`、响应归一化 `_normalize_json_response`（`max_rows=1000`） |
| `connectors/__init__.py` | `get_connector(data_source)` 工厂 |

## 数据模型

- `DataSource`（表 `data_sources`）：`type ∈ {mysql, http}`，`config_json` 敏感字段（口令等）经 `security.encrypt_value` 加密后落库；`service._sensitive_keys_for` 决定哪些 key 加密（默认 `["password","api_key","token","secret"]`）。
- `QueryTemplate`（表 `query_templates`）：`status ∈ {active, ...}`（意图路由只取 `active`）；`params_json`、`dimensions_json`、`metrics_json`、`example_questions_json`、`query_type`、`business_notes`；`QueryTemplateVersion` 记录版本，支持回滚。
- `QueryExecuteResult`：`columns` / `rows` / `execution_time_ms`；Decimal 与日期已归一化（提交 7a32585）。

## 鉴权与安全

- 用户仅能操作自己的租户：`api._resolve_tenant` 先 `ensure_current_user_tenant` + `ensure_tenant`（跨租户 403）。
- 管理端点：`api._ensure_tenant_admin` → `security.permissions.ensure_tenant_admin`。
- SQL 输入：MySQL 连接器 `_is_sql_allowed` 白名单 + 注释剥离。**已知风险**：`WITH ... DELETE` 等可绕过白名单（见记忆沉淀 `feat-data-query-center-upstream-review-2026-09-23`，P0）。
- 敏感配置：AES-256-GCM 加密（`security.py`），接口不回传明文。
- 内部调用：mock 数据查询接口用 `security.internal_service` 的 `X-UltraRAG-Internal-Token` HMAC 头。

## 与 Agent 的对接（Harness v2）

数据查询已作为**内建能力**接入 Harness（当前分支改动）：

- `core/capability_manifest.py`：注册 `data_query_search`（`builtin.data_query.search`）与 `data_query_execute`（`builtin.data_query.execute`），并列入 `RESERVED_HARNESS_CAPABILITY_NAMES`。
- `core/capability_discovery.py`：两者列入 `ALWAYS_EXPANDED_CAPABILITIES`（模型始终可见其 schema）。
- `core/harness_capability_invoker.py`：`_search_data_queries`（关键词匹配 `name/description/business_notes/example_questions/dimensions/metrics`，返回 `template_id`）与 `_execute_data_query`（调 `service.execute_query_by_id`，返回 `table_markdown`；提示模型禁止写临时脚本直连底层库）。
- 会话快路径：`app/api/chat.py` → `_maybe_handle_data_query_request`（调 `intent_router.route_question` + `service.execute_query_by_id`，命中则直接回复，不走完整 Agent 循环）。

## 对外接口

`/api/enterprise/data-query/*`：数据源与模板 CRUD、连接测试、模板试运行、主动执行、表结构浏览（`list_data_source_tables`/`describe_data_source_table`/`preview_data_source_table`/`refresh_data_source_schema`）、模板版本（`list_query_template_versions`/`rollback_query_template_version`）、按路径执行（`execute_query_template_by_path`）、员工可用模板（`list_agent_query_templates`）。前端对应 `frontend-enterprise/src/pages/data-query/` 与 `src/api/data-query.ts`（`dataSourcesApi` / `queryTemplatesApi` / `executeApi`）。

## 任务 → 文件对照（AI 定位用）

| 想改什么 | 去看 |
|---|---|
| 新增/修改 REST 端点 | `api.py` |
| 执行/参数校验/缓存/结果清洗 | `executor.py`（`QueryExecutor`、`_validate_params`、`_clean_row`） |
| SQL 白名单与 MySQL 行为 | `connectors/mysql_connector.py`（`_SQL_ALLOW_PATTERN`、`_is_sql_allowed`） |
| HTTP 取数语义 | `connectors/http_connector.py` |
| 自然语言 → 模板匹配、置信度阈值 | `intent_router.py`（`route_question(threshold=0.8)`，名称/示例问句直配 0.98） |
| “今天/近7天/环比”等日期换算 | `date_resolver.py` |
| 敏感字段加密与密钥派生 | `security.py` |
| 模型侧能看到的 data_query 能力 | `../core/capability_manifest.py`、`../core/capability_discovery.py`、`../core/harness_capability_invoker.py` |

## 测试与质量

- 后端：`backend/tests/test_data_query_api.py`（含当前分支新增用例）、`test_data_query_executor`（若存在）、`test_capability_discovery.py`（校验 data_query 能力投影）。
- 前端：`frontend-enterprise/src/pages/data-query/SqlEditor.test.tsx`（新增）、DataQueryPage/DataSourceList/ParamsConfigPanel/QueryTemplateList 同址测试。
- 官方审查结论（记忆沉淀 `feat-data-query-center-upstream-review-2026-09-23`）：P0×2（SQL 白名单 `WITH...DELETE` 绕过 + i18n 114 条缺失）/ P1×11。

## 相关文件清单

`api.py`、`service.py`、`models.py`、`executor.py`、`intent_router.py`、`date_resolver.py`、`authorization.py`、`security.py`、`connectors/*.py`；外部：`app/api/chat.py`（会话快路径）、`app/core/capability_manifest.py`、`app/core/harness_capability_invoker.py`、`frontend-enterprise/src/pages/data-query/`、`frontend-enterprise/src/api/data-query.ts`。

## 变更记录 (Changelog)

- 2026-09-24T09:46 — 增量更新：补 `intent_router.py` / `date_resolver.py` / `authorization.py` 细节；新增「与 Agent 的对接（Harness v2）」与「任务 → 文件对照」；登记前端 `SqlEditor.test.tsx`。
- 2026-09-23T18:06 — 初始化架构师首次生成；记录连接器、AES-GCM 加密与官方审查风险点。
