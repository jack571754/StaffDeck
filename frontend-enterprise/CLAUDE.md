[根目录](../CLAUDE.md) > **frontend-enterprise**

# frontend-enterprise — StaffDeck 企业工作台

## 模块职责

面向企业用户的 React/TypeScript 单页工作台：数字员工广场、会话聊天、执行/观测、技能/知识/SOP/工具管理、团队协作、定时任务、数据查询中心、渠道接入、模型/账号配置、开放平台。Vite 8 + React 18 + Tailwind 4 + Radix/shadcn。

## 入口与启动

- 入口：`index.html` → `src/main.tsx` → `src/App.tsx`（路由）。
- 脚本（`package.json`）：`dev`（vite 5173，`--strictPort`）、`build`（`tsc -b && vite build`）、`test`（`vitest run`）、`preview`、`i18n:check`、`config:check`。
- 配置：`vite.config.ts`、`tsconfig.json`；同源 API 默认走 `src/api/client.ts`（`VITE_API_BASE_URL`）。

## 路由常量（`src/enums/routes.ts` → `EnterpriseRoute`）

| 常量 | 路径 |
|---|---|
| `Workspace` / `Chat` / `Gallery` | `/workspace`、`/workspace/chat`、`/workspace/gallery` |
| `Platform` | `/enterprise/platform` |
| `Agents` / `Teams` / `Dashboard` | `/enterprise/agents`、`/enterprise/teams`、`/enterprise/dashboard` |
| `ScheduledTasks` / `Memories` / `Feedback` | `/enterprise/scheduled-tasks`、`/enterprise/memories`、`/enterprise/feedback` |
| `Channels` / `Knowledge` | `/enterprise/channels`、`/enterprise/knowledge` |
| `GeneralSkills` / `Skills` / `Tools` | `/enterprise/general-skills`、`/enterprise/skills`、`/enterprise/tools` |
| `DataQuery` / `DataQueryTemplateNew` / `DataQueryTemplateEdit` | `/enterprise/data-query`、`/enterprise/data-query/templates/new`、`/enterprise/data-query/templates/:templateId` |
| `Accounts` / `Models` / `RuntimeSettings` | `/enterprise/accounts`、`/enterprise/models`、`/enterprise/runtime-settings` |

> 新增页面必须在此枚举登记，否则路由与侧边栏都不认。

## 对外接口

`src/api/client.ts`（`api` 客户端、`ApiError`、`isAuthError`、`StreamEvent`；`TENANT_ID` 默认 `tenant_demo`）、`src/api/data-query.ts`（`dataSourcesApi` / `queryTemplatesApi` / `executeApi` + `DataSource`/`QueryTemplate`/`QueryExecuteResult` 等类型）→ 后端 `/api/...`。

## 页面分层

- `src/pages/` 顶层页：Accounts、Agents、Channels、Debug、Distill、EmployeeGallery、GeneralSkills、Knowledge、Login、Models、OpenPlatform、Persona、RuntimeSettings、Skills、Teams、TeamChat、TeamDetail、Tools、Traces、Tutorial。
- `src/pages/chat/`：`ChatPage.tsx` + `useChatSession.ts` / `chatHelpers.tsx` / `chatTypes.ts` / `chatQueueStorage.ts` / `slashCommands.ts` / `ChatGalleryPage.tsx` + 组件（MessageList、MessageBubble、Composer、ExecutionRecord、KnowledgeCitationList、HarnessArtifactDownloads、TeamCollaborationPanel、SlashCommandChip、MCPAppView、ChatDialogs、ChatHeader、ChatEmptyState、ModelSetupDialog）。
- `src/pages/dashboard/`：DashboardPage + ConversationLogsTab / MemoriesTab / WorkRecordTab / EvolutionPanel。
- `src/pages/scheduled-tasks/`：ScheduledTaskEditorPage（含 interval 调度与飞书通知配置）、TaskSection、TaskActionsMenu、StatusBadge。**注意：该页无 pipeline / execution_mode / renderer 任何入口（grep 零命中），pipeline 任务只能经 API 手搓 JSON 或写库创建（见 `backend/app/scheduled_tasks/CLAUDE.md`）。**
- `src/pages/data-query/`：DataQueryPage、DataSourceList/Dialog、QueryTemplateList/EditorPage/QueryTemplateEditor、SqlEditor、SchemaExplorer、ResultTable、TestRunPanel、ParamsConfigPanel、SkillEditDrawer。
- `src/pages/channels/`：Wechat/Wecom/Feishu/DingTalk/WechatKf Setup + BindingManagers。

## 组件与工具库

| 目录 | 内容 |
|---|---|
| `src/components/ui/` | shadcn/Radix 基础组件 |
| `src/components/openPlatform/` | 开放平台：`PlatformColumn`、`PlatformEmployeeCard`(+test)、`PlatformEmployeeDrawer`、`PlatformKindDetailView`、`PlatformResourceCard`、`PlatformResourceDrawer`、`index.ts` |
| `src/components/knowledge/` | `KnowledgeGraphVisualization.tsx`(+test)、`knowledgeGraphModel.ts`(+test) |
| `src/hooks/` | `useIsMobile`、`useToolTest(toolId, agentQuery, pollSeconds=5)`、`useClientPagination<T>` |
| `src/lib/` | `agent-scope-storage.ts`（`ENTERPRISE_AGENT_STORAGE_KEY`、`TEAM_SCOPE_PREFIX`、`toTeamScope`/`readEmployeeScope`/`emitAgentScopeChange`）、`capability-catalog-events.ts`（能力目录刷新事件总线）、`apiErrorMessages.ts`、`enterprise-ui.ts`（统一样式常量 `MENU_ITEM_CLASS`/`DIALOG_*_CLASS`/`SEARCH_COMBO_*`、`formatDateTime`）、`timezone.ts`（`getClientTimeZone`/`parseBackendDateTime`/`formatClientDateTime`）、`handoff-assignee.ts`、`identity-scope.ts`、`clipboard.ts`、`utils.ts`（`cn`） |
| `src/i18n/` | `index.tsx`（`I18nProvider`、`t(source, values?)`、`AppLocale ∈ {zh-CN, en-US}`、localStorage key `staffdeck_locale`）、`en.json`（英文词条目录） |

## 任务 → 文件对照（AI 定位用）

| 想改什么 | 去看 |
|---|---|
| 新增页面/路由 | `src/enums/routes.ts`、`src/App.tsx` |
| 聊天会话状态与流式 | `src/pages/chat/useChatSession.ts`、`chatHelpers.tsx`、`chatTypes.ts` |
| 斜杠命令 | `src/pages/chat/slashCommands.ts` |
| 定时任务编辑（含 interval） | `src/pages/scheduled-tasks/ScheduledTaskEditorPage.tsx`（**无 pipeline/execution_mode UI**，pipeline 任务需 API 手搓 JSON） |
| 数据查询 UI / SQL 编辑器 | `src/pages/data-query/`（`SqlEditor.tsx` 含 `formatSql`）、`src/api/data-query.ts` |
| 统一 UI 样式常量 | `src/lib/enterprise-ui.ts` |
| 文案（必须同步 i18n） | `src/i18n/en.json` + `npm run i18n:check` |
| 当前员工/团队作用域 | `src/lib/agent-scope-storage.ts` |
| 开放平台卡片 | `src/components/openPlatform/` |

## 测试与质量

- Vitest + Testing Library（jsdom），同址 `*.test.ts(x)`，52+ 测试文件。
- lint/检查：`tsc -b`、`i18n:check`（`scripts/check-i18n.cjs`）、`config:check`（`scripts/check-vite-env.cjs`）。
- 编码风格（见根 `AGENTS.md`）：严格 TS、两空格、单引号、分号；组件 `PascalCase`，hook `use...`；优先 `@/` 别名。

## 相关文件清单

`src/App.tsx`、`src/main.tsx`、`src/auth.ts`、`src/employee.ts`、`src/api/client.ts`、`src/api/data-query.ts`、`src/enums/routes.ts`、`src/lib/*`、`src/hooks/*`、`src/i18n/*`、`src/components/openPlatform/*`、`src/components/knowledge/*`、`package.json`、`vite.config.ts`、`scripts/check-*.cjs`。

## 变更记录 (Changelog)

- 2026-09-24T16:45 — 增量更新（聚焦定时任务 pipeline 通道）：标注 `src/pages/scheduled-tasks/ScheduledTaskEditorPage.tsx` **无 pipeline / execution_mode / renderer 入口**（grep 零命中，仅暴露飞书通知配置），pipeline 任务只能经 API 手搓 JSON 或写库创建；同步「任务 → 文件对照」表。
- 2026-09-24T09:46 — 增量更新：新增路由常量表、`src/hooks` / `src/lib` / `src/i18n` / `openPlatform` / `knowledge` 明细，以及前端「任务 → 文件对照」表；登记 `SqlEditor.test.tsx`。
- 2026-09-23T18:06 — 初始化架构师重建模块文档；补 data-query 与 scheduled-tasks 页面分层。
