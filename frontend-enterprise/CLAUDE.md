[根目录](../CLAUDE.md) > **frontend-enterprise**

# frontend-enterprise — StaffDeck 企业工作台

## 模块职责

面向企业用户的 React/TypeScript 单页工作台：数字员工广场、会话聊天、执行/观测、技能/知识/SOP/工具管理、团队协作、定时任务、数据查询中心、渠道接入、模型/账号配置、开放平台。Vite 8 + React 18 + Tailwind 4 + Radix/shadcn。

## 入口与启动

- 入口：`index.html` → `src/main.tsx` → `src/App.tsx`（路由）。
- 脚本（`package.json`）：`dev`（vite 5173，`--strictPort`）、`build`（`tsc -b && vite build`）、`test`（`vitest run`）、`preview`、`i18n:check`、`config:check`。
- 配置：`vite.config.ts`、`tsconfig.json`；同源 API 默认走 `src/api/client.ts`（`VITE_API_BASE_URL`）。

## 对外接口

`src/api/client.ts`（通用客户端）、`src/api/data-query.ts`（数据查询中心 API）→ 后端 `/api/...`。路由常量 `src/enums/routes.ts`（EnterpriseRoute）。

## 页面分层

- `src/pages/` 顶层页：Accounts、Agents、Channels、Debug、Distill、EmployeeGallery、GeneralSkills、Knowledge、Login、Models、OpenPlatform、Persona、RuntimeSettings、Skills、Teams、TeamChat、TeamDetail、Tools、Traces、Tutorial。
- `src/pages/chat/`：ChatPage + `useChatSession.ts` / `chatHelpers.tsx` / `chatTypes.ts` / `ChatGalleryPage` / 组件（MessageList、Composer、ExecutionRecord、ScheduledDraftCard、TeamCollaborationPanel、HarnessArtifactDownloads 等）。
- `src/pages/dashboard/`：DashboardPage + ConversationLogsTab / MemoriesTab / ScheduledTasksTab / WorkRecordTab / EvolutionPanel。
- `src/pages/scheduled-tasks/`：ScheduledTaskEditorPage（含 interval 调度）、TaskSection、TaskActionsMenu、StatusBadge。
- `src/pages/data-query/`：DataQueryPage、DataSourceList/Dialog、QueryTemplateList/EditorPage/QueryTemplateEditor、SqlEditor、SchemaExplorer、ResultTable、TestRunPanel、ParamsConfigPanel、SkillEditDrawer。
- `src/pages/channels/`：Wechat/Wecom/Feishu/DingTalk/WechatKf 接入页 + BindingManagers。
- `src/components/`：通用组件（shadcn `ui/`、openPlatform/、knowledge/），`src/i18n/` 国际化。

## 测试与质量

- Vitest + Testing Library（jsdom），同址 `*.test.ts(x)`，约 52+ 测试文件。
- lint/检查：`tsc -b`、`i18n:check`（`scripts/check-i18n.cjs`）、`config:check`（`scripts/check-vite-env.cjs`）。

## 相关文件清单

`src/App.tsx`、`src/main.tsx`、`src/auth.ts`、`src/api/client.ts`、`src/api/data-query.ts`、`src/enums/routes.ts`、`package.json`、`vite.config.ts`、`scripts/check-*.cjs`。

## 变更记录 (Changelog)

- 2026-09-23T18:06 — 初始化架构师重建模块文档；补 data-query 与 scheduled-tasks 页面分层。