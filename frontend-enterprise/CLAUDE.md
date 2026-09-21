[根目录](../CLAUDE.md) > **frontend-enterprise**

# frontend-enterprise — 企业工作台 SPA

## 模块职责

StaffDeck 的企业工作台前端：数字员工管理、对话、SOP/通用技能（含技能市场）、知识库、工具、渠道绑定、团队协作、定时任务、模型配置与开放平台等 20+ 页面。构建产物 `dist/` 由后端 `single_port_app.py` 在 `/enterprise` 下托管（单端口架构）；Vite dev server 仅用于旧式分离模式调试。

## 入口与启动

- HTML 入口：`index.html` → `src/App.tsx`（react-router-dom v7，`BrowserRouter`）。
- 路由常量：`src/enums/routes.ts`（`EnterpriseRoute`）。
- 鉴权：`src/auth.ts`（企业会话、角色判断 `isEnterpriseAdmin` / `isGalleryEmployee`）。
- API 层：`src/api/client.ts`（环境变量 `VITE_API_BASE_URL` 默认同源、`VITE_TENANT_ID` 默认 `tenant_demo`）。

```bash
npm install          # 安装
npm run dev          # Vite dev：127.0.0.1:5173（严格端口），/api 代理到 VITE_PROXY_TARGET(默认 8000)
npm run build        # tsc -b && vite build（打包前必跑，产物供后端托管）
npm run test         # vitest run
npm run i18n:check   # 改 UI 文案时必跑（scripts/check-i18n.cjs）
npm run config:check # 改 Vite 环境变量用法时必跑（scripts/check-vite-env.cjs）
```

推荐从仓库根使用 `scripts/dev_up.sh`（自动构建并单端口启动）。

## 对外接口（页面/路由）

主要页面（`src/pages/`，73 个 tsx 文件）：

| 区域 | 页面 |
|---|---|
| 登录/引导 | `LoginPage`、`TutorialPage` |
| 工作台 | `dashboard/DashboardPage`（含 `ScheduledTasksTab`、`WorkRecordTab`、`MemoriesTab`、`EvolutionPanel`、`ConversationLogsTab`） |
| 数字员工 | `AgentsPage`、`EmployeeGalleryPage`、`DistillPage`（经验蒸馏） |
| 对话 | `chat/ChatPage`、`chat/ChatGalleryPage`、`chat/components/MessageList`、`chat/components/MessageBubble`、`chat/components/Composer`、`chat/components/ChatHeader`、`chat/components/HarnessArtifactDownloads`、`chat/components/ScheduledDraftCard`、`chat/components/TeamCollaborationPanel`、`chat/components/SlashCommandChip`、`chat/components/MCPAppView`、`chat/components/KnowledgeCitationList`、`chat/components/ExecutionRecord`、`chat/components/ModelSetupDialog`、`chat/useChatSession.ts` |
| SOP 技能 | `SkillsPage` |
| 通用技能 | `GeneralSkillsPage`、`general-skills/SkillMarketDialog`（开源技能市场一键安装） |
| 知识库 | `KnowledgePage`（KnowledgeManagePage / KnowledgeAddPage） |
| 工具 | `ToolsPage` |
| 渠道 | `ChannelsPage`、`channels/WecomSetup`、`channels/WechatSetup`、`channels/WechatKfSetup`、`channels/FeishuSetup`、`channels/DingTalkSetup`、`channels/BindingManagers` |
| 定时任务 | `scheduled-tasks/ScheduledTaskEditorPage`（new/edit，支持 once/daily/weekly/monthly/interval 5 种调度）、`scheduled-tasks/TaskSection`、`scheduled-tasks/TaskActionsMenu`、`scheduled-tasks/StatusBadge`、`scheduled-tasks/shared.ts` |
| 团队协作 | `TeamsPage`、`TeamDetailPage`、`TeamChatPage` |
| 系统 | `ModelsPage`、`RuntimeSettingsPage`、`PersonaPage`、`AccountsPage`、`OpenPlatformPage` |
| 可观测 | `TracesPage`、`DebugPage` |

## 关键依赖与配置

- 运行时：React 18、react-router-dom 7、radix-ui + `components/ui`（shadcn 风格）、Tailwind CSS 4（`@tailwindcss/vite`）、cytoscape（知识图谱）、sonner（toast）、qrcode、pinyin-pro、lucide-react。
- 构建：Vite 8 + `@vitejs/plugin-react` + `vite-plugin-svgr`；`vite.config.ts` 定义 `@ → ./src` 别名、vitest jsdom 环境（setup：`src/test/setup.ts`）。
- 测试：Vitest + Testing Library（`@testing-library/react`、`user-event`），共 52 个测试文件。
- 配置文件：`package.json`、`tsconfig.json`、`components.json`（shadcn）、`.env.example`、`scripts/check-*.cjs`。

## 目录结构

```
src/
├── App.tsx / App.test.tsx          # 路由与布局
├── api/client.ts / client.test.ts  # API 客户端
├── auth.ts                         # 企业会话与角色
├── employee.ts / employee.test.ts  # 数字员工档案工具函数
├── enums/routes.ts                 # 路由常量
├── pages/                          # 页面（73 个 tsx，含 chat/ dashboard/ scheduled-tasks/ general-skills/ channels/）
├── components/                     # 通用组件（ui/ shadcn、openPlatform/、knowledge/ 知识图谱、BiddingArena 等）
├── hooks/  lib/  types/  i18n/     # 钩子、工具库、类型、国际化(en.json)
└── test/setup.ts                   # Vitest setup
```

---

## 聊天页面（深度）

### useChatSession Hook

`src/pages/chat/useChatSession.ts` 是聊天页面的核心状态管理器，一个巨型自定义 hook 封装了所有聊天相关的状态、副作用和操作。

**核心状态**

| 状态组 | 关键字段 |
|---|---|
| 会话与员工 | `sessions`、`sessionId`、`agents`、`teams`、`selectedAgentId`、`sessionAgentFilter` |
| 模型配置 | `modelConfigs`、`selectedModelConfigId`、`modelSetupOpen` |
| 消息与流 | `input`、`lastTurn`、`runningTurn`、`streamSlot`（loading/phase/accumulated/turnId） |
| 跟踪与溯源 | `expandedTraceIds`、`collapsedTraceIds`、`traceTick` |
| 附件与命令 | `composerAttachments`、`composerDragActive`、`slashCommands` |
| 定时任务 | `scheduledDrafts`、`createdScheduledTasks`、`dismissedDraftMessageIds` |
| 人工接管 | `handoffs`、`showHandoffInbox`、`handoffReplies` |
| UI 状态 | `sidebarCollapsed`、`isComposing`、`activeCitation` |
| UI 配置 | `uiConfig`（show_thinking_trace、show_skill_trace、show_tool_trace、context 预算等） |

**关键配置常量**

```
CHAT_STREAM_IDLE_TIMEOUT_MS = 600s       # 流空闲超时
CHAT_STREAM_IDLE_CHECK_INTERVAL_MS = 5s  # 空闲检查间隔
CHAT_STREAM_HEARTBEAT_GRACE_MS = 20s     # 心跳宽限
CHAT_TRACE_RECOVERY_WINDOW_MS = 10min    # 跟踪恢复窗口
RUNNING_EVENT_RECOVERY_WINDOW_MS = 600s  # 运行中事件恢复窗口
STREAM_RELAY_RECOVERY_POLL_INTERVAL_MS = 5s  # 中继恢复轮询
GENERAL_SKILL_MAX_ATTEMPTS = 10          # 通用技能最大尝试
```

**终态流事件集合**（`STREAM_TERMINAL_EVENTS`）：`complete`、`done`、`stream_end`、`stream_cancelled`、`stream_interrupted`、`error`、`error_occurred`

### SSE 流式消费

`streamChatTurn()` → `streamPost()`（`src/api/client.ts`）：

1. POST `/api/chat/stream`，请求体为 JSON
2. 使用 `response.body.getReader()` 读取 ReadableStream
3. `TextDecoder` 逐块解码，按 `\n\n` 分块
4. 每块解析为 `StreamEvent { event, data }`（event: 行前缀 + data: JSON）
5. 调用 `onEvent(item)` 回调逐事件分发

### 消息渲染管线

**消息数据模型**（`chatTypes.ts`）：

| 类型 | 说明 |
|---|---|
| `SessionSlot` | 服务端消息 + 实时消息双缓冲 |
| `StreamSlot` | 流加载状态：loading/phase/accumulated/turnId/abortController |
| `TurnTrace` | 一轮对话的跟踪线列表 + 起止时间 |
| `TraceLine` | 单条跟踪线：kind（thinking/decision/skill/tool/code/knowledge）+ state + icon + 详情/代码/输出 |
| `TraceTool` / `TraceSkill` | 工具/技能跟踪的结构化数据 |
| `MCPAppViewDescriptor` | MCP App 嵌入视图描述 |

**chatHelpers 核心函数族**（60+ 工具函数）

| 分组 | 代表函数 |
|---|---|
| 消息合并 | `computeMergedMessages`、`attachTurnIdsToServerMessages`、`shouldKeepRealtimeMessage` |
| 流处理 | `hasRenderableStreamingText`、`isStreamingMessageId`、`streamingMessageId`、`normalizeSessionEventForStream` |
| 跟踪线 | `harnessEventTraceLine`、`toolTraceDetail`、`knowledgeTraceDetail`、`generalSkillTraceDetail`、`reflectionTraceDetail`、`mergeTraceLine`、`mergeTurnTraceSnapshot` |
| 草稿/定时 | `scheduledDraftForMessage`、`isScheduledSession` |
| 会话过滤 | `sessionFilterStorageKey`、`buildSessionFilterOptions` |
| 草稿对话 | `draftConversationKey`、`isDraftConversationKey` |
| 知识引用 | `isKnowledgeTracePhase`、`knowledgeTraceLineId`、`knowledgeTraceText` |
| 队列 | `readQueuedChatTurns`、`writeQueuedChatTurns` |

### 聊天页面组件树

```
ChatPage
├── SidebarProvider
│   ├── AppSidebar (chat variant) — 会话列表/员工选择/未读计数/接管入口
│   └── main
│       ├── ChatHeader — 员工信息/模型切换/更多操作
│       ├── MessageList — 消息滚动列表
│       │   ├── MessageBubble (user/assistant)
│       │   │   ├── CodeBlock（代码渲染）
│       │   │   ├── KnowledgeCitationList（知识引用）
│       │   │   ├── ScheduledDraftCard（定时任务草稿卡）
│       │   │   ├── HarnessArtifactDownloads（产物下载）
│       │   │   ├── TeamCollaborationPanel（团队协作面板）
│       │   │   ├── MCPAppView（MCP App 嵌入视图）
│       │   │   └── ExecutionRecord（执行记录/跟踪线展开）
│       │   └── SlashCommandChip（斜杠命令）
│       └── Composer — 输入框+附件+斜杠命令+发送
└── ChatDialogs — 重命名/删除/模型设置等对话框
```

### 流恢复机制

- 页面刷新或连接中断后，通过 `RUNNING_EVENT_RECOVERY_WINDOW_MS`（600s）内的运行中事件进行恢复
- 中继恢复模式：`STREAM_RELAY_RECOVERY_POLL_INTERVAL_MS` 轮询
- `isRecoverableRunningTrace` 判断是否为可恢复的运行中跟踪
- `shouldDeferPersistedEventToLiveStream` 判断持久化事件是否应让位于实时流

---

## API 客户端（深度）

### 客户端结构（`src/api/client.ts`）

**基础请求层**

| 函数/方法 | 说明 |
|---|---|
| `api.get<T>(path)` | GET JSON |
| `api.post<T>(path, body)` | POST JSON |
| `api.postWithSignal<T>(path, body, signal)` | POST + AbortSignal |
| `api.postKeepalive<T>(path, body)` | POST keepalive（页面关闭时用） |
| `api.put<T>(path, body)` | PUT JSON |
| `api.patch<T>(path, body)` | PATCH JSON |
| `api.delete<T>(path)` | DELETE |
| `api.blob(path)` | GET Blob（文件下载） |
| `api.postBlob(path, body)` | POST 返回 Blob |
| `api.postForm<T>(path, form)` | POST FormData（文件上传） |

**鉴权**：`authHeader()` 从 `getEnterpriseAuthSession()` 取 token，自动注入 `Authorization: Bearer <token>`

**错误处理**：`ApiError` 类（status/body/code），`parseErrorPayload()` 解析多种错误格式（detail/message/error 字段 + 数组验证详情 + 嵌套对象 code/message），`stableErrorCode()` 提取稳定错误码（大写字母+下划线格式）

### 流式接口

| 函数 | 端点 | 说明 |
|---|---|---|
| `streamChatTurn(body, onEvent, signal?)` | POST `/api/chat/stream` | 聊天流式响应（SSE 风格） |
| `streamPost(path, body, onEvent, signal?)` | 通用 POST 流 | SSE 格式：`event:` + `data:` 多行，每块 `\n\n` 分隔 |
| `streamGet(path, onEvent, signal?)` | 通用 GET 流 | 同上 |

**SSE 解析**（`parseSseBlock`）：
- 提取 `event:` 行作为事件名
- 提取所有 `data:` 行，拼接后 JSON 解析
- 解析失败时 data 原样返回 `{ raw }`

### 附件上传

`uploadChatAttachments<T>(tenantId, files, signal?)` → POST `/api/chat/attachments`，FormData 上传文件

### 配置与环境

| 变量 | 默认 | 说明 |
|---|---|---|
| `VITE_API_BASE_URL` | `''`（同源） | API 基址 |
| `VITE_TENANT_ID` | `tenant_demo` | 默认租户 ID |
| `VITE_SHOW_DEBUG` | `false` | 是否显示调试信息 |
| `VITE_PROXY_TARGET` | `8000` | dev server 代理目标 |

---

## 测试与质量

- 同址 `*.test.ts(x)`，共 52 个测试文件，`npm run test` 运行；测试以被测单元命名。
- 覆盖范围：路由守卫、API 客户端、页面组件（聊天/团队/渠道/登录/员工库/蒸馏等）、工具库（时区/身份作用域/剪贴板/能力目录事件等）、知识图谱模型、通用组件（DataTable/SearchableSelect/StatCard/AppSidebar 等）。
- TypeScript strict；组件 `PascalCase`、hooks `use...`；导入优先 `@/` 别名；两空格、单引号、分号。
- UI 变更需在浏览器验证受影响路由与用户角色。

## 常见问题 (FAQ)

- **页面 404？** 单端口模式下由后端 SPA fallback 托管；确认已 `npm run build` 且后端为 `single_port_app:app`。
- **API 跨域/代理？** 单端口同源无需代理；分离模式设 `VITE_PROXY_TARGET` 指向后端（默认 8000）。
- **新增环境变量？** 必须同步 `.env.example` 并跑 `npm run config:check`。
- **技能市场怎么接入？** `GeneralSkillsPage.tsx` 集成 `SkillMarketDialog`，对接后端 `/api/general_skills/market/*` 三个端点，数据源为 skills.sh 注册表。
- **定时任务草稿是什么？** 对话中 LLM 检测到用户意图后，由后端生成 `ScheduledTaskDraftRead`，前端 `ScheduledDraftCard.tsx` 展示并允许用户确认/编辑后创建正式任务。
- **聊天流断了怎么办？** `useChatSession` 内置流恢复机制，600s 窗口内的运行中事件会自动恢复；中继模式 5s 轮询。

## 相关文件清单（高信号）

`src/App.tsx`、`src/api/client.ts`、`src/auth.ts`、`src/enums/routes.ts`、`src/pages/chat/ChatPage.tsx`、`src/pages/chat/useChatSession.ts`、`src/pages/chat/chatHelpers.tsx`、`src/pages/chat/chatTypes.ts`、`src/pages/AgentsPage.tsx`、`src/pages/GeneralSkillsPage.tsx`、`src/pages/general-skills/SkillMarketDialog.tsx`、`src/pages/chat/components/ScheduledDraftCard.tsx`、`src/pages/chat/components/MessageBubble.tsx`、`src/pages/chat/components/MessageList.tsx`、`src/pages/chat/components/Composer.tsx`、`src/pages/scheduled-tasks/ScheduledTaskEditorPage.tsx`、`src/components/knowledge/KnowledgeGraphVisualization.tsx`、`vite.config.ts`、`package.json`

## 变更记录 (Changelog)

- 2026-09-17T10:09:30+08:00 深度补扫：新增聊天页面深度章节（useChatSession 核心状态/SSE 流式消费/消息渲染管线/组件树/流恢复机制）、新增 API 客户端深度章节（基础请求层/流式接口/SSE 解析/附件上传/环境配置）、扩充相关文件清单。
- 2026-09-17T10:09:30+08:00 增量更新：页面数更新为 73 tsx、测试 40+→52 文件、新增技能市场（SkillMarketDialog）、定时任务草稿卡片（ScheduledDraftCard）、interval 调度编辑器、扩充页面清单表格、新增 FAQ。
- 2026-09-11T15:42:50 — 初始化架构师首次生成。
