[根目录](../../../CLAUDE.md) > [backend](../../) > [app](../) > **data_query**

# data_query — 数据查询中心

## 模块职责

企业数据查询中心：以“数据源 + 查询模板”为核心，支持 MySQL / HTTP 两种连接器，提供 SQL 模板语义化、连接测试、模板试运行与执行，并对敏感配置做 AES-256-GCM 加密。当前分支热点（`feat/clean-data-query-and-interval`）。

## 入口与文件

| 文件 | 职责 |
|---|---|
| `api.py` | REST 路由，prefix `/api/enterprise/data-query`，全部需登录并按租户隔离；管理端点要求租户管理员 |
| `service.py` | 领域逻辑：数据源 / 模板 CRUD、连接测试、试运行、执行 |
| `models.py` | Pydantic 模型（DataSource/QueryTemplate CRUD+Read、QueryExecuteRequest/Result、TableSummary、TablePreviewResult、ColumnMeta、AdhocTestRequest 等）|
| `executor.py` | 模板执行器 |
| `intent_router.py` | 意图路由（语义查询入口）|
| `date_resolver.py` | 日期/时间参数解析 |
| `authorization.py` | 授权与权限判定 |
| `security.py` | AES-256-GCM 加密工具（`enc:` 前缀，APP_SECRET 派生 key，12 字节 nonce，base64(nonce+ct+tag)）|
| `connectors/base.py` | 连接器抽象基类 |
| `connectors/mysql_connector.py` | MySQL 连接器 |
| `connectors/http_connector.py` | HTTP 连接器（对齐官方标准 http 工具执行语义）|

## 数据模型

- `DataSourceRead/DataSourceCreate/DataSourceUpdate`：数据来源元数据；敏感字段（口令等）经 `security.encrypt_value` 加密后落库。
- `QueryTemplateRead/Create/Update` + `QueryTemplateVersionRead`：SQL 查询模板及版本。
- `QueryExecuteResult` / `TableSummary` / `TablePreviewResult` / `ColumnMeta`：执行结果与元数据（Decimal/日期已归一化，见提交 7a32585）。
- 内部使用 `app/db/models.py` 的表模型与 `app/db/get_session`。

## 鉴权与安全

- 用户仅能操作自己的租户：`_resolve_tenant` 先 `ensure_current_user_tenant` + `ensure_tenant`（跨租户 403）。
- 管理端点：`ensure_tenant_admin`。
- SQL 输入：执行器与白名单机制需防护 `WITH ... DELETE` 等绕过（见记忆沉淀 feat-data-query-center-upstream-review；P0：SQL 白名单绕过）。
- 敏感配置：AES-256-GCM 加密（`security.py`），接口不回传明文。

## 对外接口

`/api/enterprise/data-query/*`：数据源与模板 CRUD、连接测试、模板试运行、主动执行。前端对应 `frontend-enterprise/src/pages/data-query/` 与 `src/api/data-query.ts`。

## 测试与质量

前端有同址测试（DataQueryPage/DataSourceList/ParamsConfigPanel/QueryTemplateList）；后端逻辑建议补 connector 与安全用例。本模块此前经 OpenBMB 官方审查：P0×2（SQL 白名单 `WITH...DELETE` 绕过 + i18n 114 条缺失）/ P1×11，修复方向见记忆索引。

## 相关文件清单

`api.py`、`service.py`、`models.py`、`executor.py`、`security.py`、`connectors/*.py`；外部：`app/api/` 挂载路由、`frontend-enterprise/src/pages/data-query/`。

## 变更记录 (Changelog)

- 2026-09-23T18:06 — 初始化架构师首次生成；记录连接器、AES-GCM 加密与官方审查风险点。