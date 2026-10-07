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

`src/api/client.ts`（`api` 客户端、`ApiError`、`isAuthError`、`StreamEvent`；`TENANT_ID` 默认 `tenant_demo`）、`src/api/data-query.ts`（`dataSourcesApi` / `queryTemplatesApi` / `executeApi` + `DataSource`/`QueryTemplate`/`QueryExecuteResult` 等类型）→ 后端 `/api/...`。共享类型集中在 `src/types/index.ts`（本分支新增 `FeishuOutboundMessageRead` / `FeishuOutboundMessagePage` / `FeishuOutboundStatsRead`）。

## 页面分层

- `src/pages/` 顶层页：Accounts、Agents、Channels、Debug、Distill、EmployeeGallery、GeneralSkills、Knowledge、Login、Models、OpenPlatform、Persona、RuntimeSettings、Skills、Teams、TeamChat、TeamDetail、Tools、Traces、Tutorial。
- `src/pages/chat/`：`ChatPage.tsx` + `useChatSession.ts` / `chatHelpers.tsx` / `chatTypes.ts` / `chatQueueStorage.ts` / `slashCommands.ts` / `ChatGalleryPage.tsx` + 组件（MessageList、MessageBubble、Composer、ExecutionRecord、KnowledgeCitationList、HarnessArtifactDownloads、TeamCollaborationPanel、SlashCommandChip、MCPAppView、ChatDialogs、ChatHeader、ChatEmptyState、ModelSetupDialog）。
  - `HarnessArtifactDownloads.tsx`（产物卡片）：所有产物都可**下载**（走鉴权 blob 接口）；图片额外内联预览；**`.html` / `content_type=text/html` 的产物**再显示「预览」「分享」两个动作——预览调 `POST /api/chat/artifacts/share-link` 拿签名 token，再用**相对路径** `/api/chat/artifacts/view/{token}` 新标签打开（因此控制台用 127.0.0.1、局域网地址或改过的端口都能打开，不受 `TOOL_BASE_URL` 写死影响）；分享复制后端返回的绝对 URL。
- `src/pages/dashboard/`：DashboardPage + ConversationLogsTab / MemoriesTab / WorkRecordTab / EvolutionPanel。
- `src/pages/scheduled-tasks/`：ScheduledTaskEditorPage（含 interval 调度与飞书通知配置）、TaskSection、TaskActionsMenu、StatusBadge。**注意：该页无 pipeline / execution_mode / renderer 任何入口（grep 零命中），pipeline 任务只能经 API 手搓 JSON 或写库创建（见 `backend/app/scheduled_tasks/CLAUDE.md`）。**
- `src/pages/data-query/`：DataQueryPage、DataSourceList/Dialog、QueryTemplateList/EditorPage/QueryTemplateEditor、SqlEditor、SchemaExplorer、ResultTable、TestRunPanel、ParamsConfigPanel、SkillEditDrawer。
- `src/pages/channels/`：Wechat/Wecom/Feishu/DingTalk/WechatKf Setup + BindingManagers，以及 **`FeishuMessagesTab.tsx`（飞书出站消息观测与撤回，本分支在途）**。
  - `ChannelsPage.tsx` 顶部用 `UnderlineTabs`（`@/components/ui`）分两个主 Tab：`channels`（渠道接入与绑定，原有全部内容）与 `feishu-messages`（`FeishuMessagesTab`，无 props）；切到消息 Tab 时 `setSelectedId('')`。
  - `FeishuMessagesTab` 数据流：`GET /api/enterprise/channels/feishu/messages/stats`、`GET .../feishu/messages`（channel_type/status/search/offset/limit）、`POST .../feishu/messages/{id}/recall?tenant_id=…`；含 4 张统计卡、筛选+搜索、DataTable、客户端分页、`card_json` 详情弹窗与二次确认撤回。
  - **该 Tab 无角色门控**（组件不接收 `currentUser`，路由与 `ChannelsPage` 都无条件渲染），只按 `can_recall`/`channel_type` 禁用按钮；后端同名接口也只要求登录+租户相等（同文件的渠道投递审计接口却要求管理员）。

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
| 飞书出站消息管控 / 撤回 UI | `src/pages/channels/FeishuMessagesTab.tsx`（+ `.test.tsx`）、`src/pages/ChannelsPage.tsx`（`MAIN_TABS`）、`src/types/index.ts` |
| 聊天产物卡片的下载 / 预览 / 分享 | `src/pages/chat/components/HarnessArtifactDownloads.tsx`（+ `.test.tsx`）；HTML 报告经 `api.post('/api/chat/artifacts/share-link')` 铸造签名链接（预览用相对路径新标签打开，分享复制后端绝对 URL） |
| 开放平台卡片 | `src/components/openPlatform/` |

## 测试与质量

- Vitest + Testing Library（jsdom），同址 `*.test.ts(x)`，52+ 测试文件（本分支新增 `pages/channels/FeishuMessagesTab.test.tsx`）。
- lint/检查：`tsc -b`、`i18n:check`（`scripts/check-i18n.cjs`）、`config:check`（`scripts/check-vite-env.cjs`）。
- 编码风格（见根 `AGENTS.md`）：严格 TS、两空格、单引号、分号；组件 `PascalCase`，hook `use...`；优先 `@/` 别名。

## 相关文件清单

`src/App.tsx`、`src/main.tsx`、`src/auth.ts`、`src/employee.ts`、`src/api/client.ts`、`src/api/data-query.ts`、`src/enums/routes.ts`、`src/lib/*`、`src/hooks/*`、`src/i18n/*`、`src/components/openPlatform/*`、`src/components/knowledge/*`、`package.json`、`vite.config.ts`、`scripts/check-*.cjs`。

## 变更记录 (Changelog)

- 2026-09-30T18:55 — 增量更新：`HarnessArtifactDownloads` 新增「预览/分享」动作（仅 `.html` / `text/html` 产物）：预览经 `POST /api/chat/artifacts/share-link` 铸造签名 token 后用**相对路径**新标签打开，分享复制后端绝对 URL；新增 4 个单测（含失败路径），`vitest` 8/8、`i18n:check`（3798 条）、`config:check`、`tsc -b` + `vite build` 全部通过；同步「任务 → 文件对照」与页面分层说明。
- 2026-09-30T16:05 — 增量更新（在途改动核实）：`pages/channels/` 登记 **`FeishuMessagesTab.tsx`**（飞书出站消息观测与撤回）与 `ChannelsPage.tsx` 的 `UnderlineTabs` 双主 Tab 结构；补该 Tab 的 API 数据流、**无角色门控**事实与测试文件；共享类型补 `FeishuOutboundMessage*`；「任务 → 文件对照」新增一行。
- 2026-09-24T16:45 — 增量更新（聚焦定时任务 pipeline 通道）：标注 `src/pages/scheduled-tasks/ScheduledTaskEditorPage.tsx` **无 pipeline / execution_mode / renderer 入口**（grep 零命中，仅暴露飞书通知配置），pipeline 任务只能经 API 手搓 JSON 或写库创建；同步「任务 → 文件对照」表。
- 2026-09-24T09:46 — 增量更新：新增路由常量表、`src/hooks` / `src/lib` / `src/i18n` / `openPlatform` / `knowledge` 明细，以及前端「任务 → 文件对照」表；登记 `SqlEditor.test.tsx`。
- 2026-09-23T18:06 — 初始化架构师重建模块文档；补 data-query 与 scheduled-tasks 页面分层。
