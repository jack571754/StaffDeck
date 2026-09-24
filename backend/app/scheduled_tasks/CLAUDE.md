[根目录](../../../CLAUDE.md) > [backend](../../) > [app](../) > **scheduled_tasks**

# scheduled_tasks — 定时/周期任务引擎

## 模块职责

把会话中的“定时/定期/自动执行”意图固化为可到点运行的一次性 / 每日 / 每周 / 每月 / 间隔任务，并基于 Harness v2 持久化状态判定结果（绝不只信答复文本）。当前分支热点。

## 入口与文件

| 文件 | 职责 |
|---|---|
| `schema.py` | Pydantic 请求/响应模型（Create/Update/Draft/Read/RunRead）|
| `service.py` | 核心逻辑：草稿检测、调度归一化（`normalize_schedule`）、下次执行时间（`compute_next_run_at`）、RRULE（`build_rrule`）、到期领取租约（`due_scheduled_tasks`）、执行分流（`_execute_prepared_scheduled_task`）、Harness v2 成功判定、SOP 版本快照、飞书通知注入 |
| `worker.py` | 后台 worker（`start_background_worker` / `run_worker`，轮询 `due_scheduled_tasks`）与独立 `--once` CLI |
| `pipeline.py` | 确定性流水线执行骨架（约 342 行）：步骤循环、`is_task_first_push_today`、成功/失败收尾、关联 `ChatSession` 消息与流式事件；对卡片构建器仅做向后兼容再导出 |
| `renderers/` | **飞书卡片渲染器注册表**（本分支热点）：`__init__.py`（`CARD_RENDERERS` / `get_card_renderer`）、`sales_card.py`（销售卡 2.0）、`generic_table.py`（通用表）、`base.py`（数值/增量格式化）|
| `fixed_process_workflow.md` | 固定流程任务（取数-比对-推送）的三步执行规范 |
| `architecture_analysis.md` | 定时任务板块架构全景剖析、深层缺陷审计（8 项）与产品化演进方案（本文件是深读入口；**本地未跟踪文档**，其 3.1/3.2 节对 `pipeline.py` 内联硬编码的描述已因渲染器抽包而过时）|

外部接入：`app/api/scheduled_tasks.py`（`enterprise_router` / `chat_router` / `chat_draft_router`）；`app/main.py` 生命周期拉起 `start_background_worker`。

## 数据模型

- `ScheduledTask`（表 `scheduled_tasks`）：`schedule_type ∈ {once,daily,weekly,monthly,interval}`；`status`（active/paused/completed）；`concurrency_policy ∈ {allow,forbid}`、`misfire_policy ∈ {coalesce,skip}`；`lease_owner` / `lease_until` 租约；`execution_mode ∈ {agent,pipeline}`；`pipeline_steps_json`；`metadata_json` 存 SOP 绑定/版本策略与飞书通知配置（`feishu_notify`）；`max_runs` / `run_count` / `last_run_at` / `last_status`。
- `ScheduledTaskRun`（表 `scheduled_task_runs`）：单次执行记录，`scheduled_for` + `status`（running/skipped/succeeded/needs_input/failed/incomplete/retrying）；`result_summary`、`error`、`trace_json`。

## 关键机制

- **调度计算**：`compute_next_run_at` 按时区在本地时间计算（默认 `Asia/Shanghai`，默认 09:00）；interval 用 `interval_seconds`/`interval_minutes`；`build_rrule` 生成 RRULE 字符串。
- **租约领取**：`due_scheduled_tasks` 用条件 UPDATE 原子领取到点任务，`LEASE_SECONDS=900` 防多 worker 重复跑。
- **成功判定（防“假成功”）**：`_scheduled_harness_outcome`（`service.py` 约 L761-923）只从 Harness v2 持久化读取——需存在 `HarnessTurnRecord`（receipt）、`HarnessTaskFrameRecord`、`HarnessRunRecord`/`HarnessInvocationRecord`；`effective_status` 全 `completed` 才 `succeeded`。
- **业务失败传播**：`_scheduled_business_failures` 解析 `run_skill_script` 结果中的 `ok/status/structured_result.status/push_status/exit_code`；`push_status ∈ {failed,error}` 或 `exit_code != 0` 时强制 `ScheduledTaskRun.status = "failed"`。
- **SOP 快照**：`metadata_json` 支持 `sop_id` + `sop_version_policy`（latest/pinned）；pinned 用 `expand_sop_for_execution` 生成版本快照，服务端生成、不接受客户端注入。
- **固定流程三步模板**（`SCHEDULE_DRAFT_PROMPT`）：读取技能包 run.py 路径 → 调 `run_skill_script`（timeout 60-120s）→ 读返回 JSON 并简报。
- **Pipeline 通道**：`service.py`（`_execute_prepared_scheduled_task`，约 L676）判断 `execution_mode == "pipeline"` 且存在 `pipeline_steps_json` 时，交 `pipeline.py` 解析步骤：`query`（调 `data_query.service.execute_query_by_id`）与 `skill_notify`/`feishu_notify`/`notify`（渲染飞书卡片后调 `feishu_app_notify`），并向关联 `ChatSession` 写入系统总结与流式事件。详见下节。

## Pipeline 通道与飞书卡片渲染器（当前分支热点）

**渲染器已抽包（提交 `042333ee`）**，`pipeline.py` 不再内联业务卡片构建器：

| 文件 | 职责 |
|---|---|
| `renderers/__init__.py` | `CARD_RENDERERS` 注册表 + `get_card_renderer(name)`。别名：`sales_card`/`sales_card_v2`/`sales` → 销售卡；`generic_table`/`table` → 通用表。**`name` 为空/None 时返回 `build_sales_feishu_card`**；未知名回退 `build_generic_feishu_card` |
| `renderers/sales_card.py` | `build_sales_feishu_card`（约 403 行）：销售卡 2.0，含「可复美/可丽金/大盘/电商整体/店铺正负增量 Top5/24 小时走势」业务口径与 `净销_万`/`运营净销_万`/`达播净销_万`/`环比增量_万`/`运营增量_万`/`达播增量_万` 列名 |
| `renderers/generic_table.py` | `build_generic_feishu_card`：任意行 → 通用表格卡（最多 `max_rows=15`）|
| `renderers/base.py` | `fmt_val_styled` / `fmt_diff_styled`（万单位、正绿负红、`0.00万` 灰）|

**分发字段**：notify 步骤按 `step["renderer"]` → `step["params"]["renderer"]` → `metadata["renderer"]` → 默认 `"sales_card"` 解析后调 `get_card_renderer()`（`pipeline.py:175-181`）。**该字段只存在于步骤 JSON 中，schema 无枚举校验。**

**仍存在的债 / 待办（本次逐行核实）**：

1. **默认渲染器仍业务化**：`get_card_renderer(None)` 与 `renderer_name` 缺省都落到 `sales_card`，非销售任务不显式配置 renderer 时仍出销售卡。
2. **引擎无条件注入业务语义**：每个 query 步骤被无条件写入 `params["is_first_push"]`（`pipeline.py:142`）；`is_task_first_push_today()`（`pipeline.py:49-105`，销售「当日首次播报」概念）位于引擎文件。
3. **契约不实**：模块 docstring 声称 `Query -> Process -> Notify` 三阶段（`pipeline.py:3`），但循环只识别 `query` 与 `skill_notify`/`feishu_notify`/`notify`，**无 `process` 步骤类型**，process 被融进卡片装配。
4. **多 query 语义失效**：`query_rows` 是单个变量、被每个 query 步骤覆盖（`pipeline.py:123` / `:144`），notify 只渲染**最后一个** query 的结果。
5. **零校验**：`schema.py:31,55,106` 对 `pipeline_steps` 仅声明 `list[dict[str, Any]]`（无步骤类型枚举、无 `renderer` 字段、无 `template_id` 必填），错误只能运行时暴露（`pipeline.py:139-140`）；`app/api/scheduled_tasks.py` 无 pipeline 专属端点。**pipeline 任务目前只能经 API 手搓 JSON 或直接写库创建。**
6. **前端未产品化**：`frontend-enterprise/src/pages/scheduled-tasks/ScheduledTaskEditorPage.tsx` 对 pipeline/steps/execution_mode/renderer **grep 零命中**，仅暴露飞书通知配置。

**已修复项（`042333ee`）**：

- 销售卡片抽到 `renderers/sales_card.py`，`pipeline.py` 由约 660 行降至 342 行，不再内联业务构建器。
- 平行实现漂移消除：`skills/templates/fixed-etl-notify/run.py` 现 `from app.scheduled_tasks.renderers.sales_card import build_sales_feishu_card`（`run.py:52-64` / `:447-454`），广播卡与 pipeline 共用同一实现；run.py 仅保留自有 audit 模式通用卡（`run.py:456-556`）。
- 聊天总结文案不再写死「实时销售播报」，改用 `task.title`（`pipeline.py:242`）。

**引擎骨架（应保留）**：步骤循环、`_finish_task_schedule` 租约收尾、`trace_json`、关联 `ChatSession` 消息与流式事件、异常 → `run.status="failed"` 传播，符合本仓库「绝不信模型答复文本」既有不变量。

## 任务 → 文件对照（AI 定位用）

| 想改什么 | 去看 |
|---|---|
| 调度周期计算 / RRULE / 时区 | `service.py`（`normalize_schedule` / `compute_next_run_at` / `build_rrule`） |
| 到点领取与并发/失火策略 | `service.py`（`due_scheduled_tasks`、`concurrency_policy`、`misfire_policy`、`LEASE_SECONDS`） |
| “假成功”判定 / 业务失败传播 | `service.py`（`_scheduled_harness_outcome`、`_scheduled_business_failures`） |
| 手动运行（Run Now）与计数 | `service.py`（`_finish_task_schedule`，约 L1393；`manual=True` 仍累加 `run_count` 是已知缺陷 5） |
| 无 LLM 的确定性取数-推送（步骤循环/收尾） | `pipeline.py` |
| 飞书卡片渲染（销售卡 / 通用表 / 注册表） | `renderers/__init__.py`（`CARD_RENDERERS`）、`renderers/sales_card.py`、`renderers/generic_table.py` |
| Worker 轮询/阻塞/异常保护 | `worker.py`（缺陷 1 队头阻塞、缺陷 2 无 try/except） |
| 固定流程任务的提示词与规范 | `SCHEDULE_DRAFT_PROMPT`（`service.py` 内）、`fixed_process_workflow.md` |
| 架构缺陷全景与演进 Roadmap | `architecture_analysis.md`（本地未跟踪；3.1/3.2 节部分已过时） |

## 已知缺陷与风险（摘自 `architecture_analysis.md` 并补充本次核实项）

| 级别 | 缺陷 | 锚点 |
|---|---|---|
| P0 | Worker 单线程同步执行导致队头阻塞 | `worker.py:28-35`（`execute_scheduled_task` 同步调用） |
| P0 | Worker 主循环无全局异常保护，daemon 线程可静默暴毙 | `worker.py` `run_worker` |
| P1 | 外层 `with Session(engine)` 跨越长任务，SQLite `database is locked` | `worker.py` |
| P1 | 进程崩溃后 900s 租约僵尸 + 孤儿任务无自愈扫描 | `service.py` / 启动路径 |
| P1 | Run Now 污染 `run_count`，提前触发 `max_runs` | `service.py:1393` |
| P1 | 默认渲染器与 `is_first_push` 业务语义耦合在引擎/注册表 | `pipeline.py:142`、`pipeline.py:49-105`、`renderers/__init__.py:21` |
| P1 | 多 query 流水线只渲染最后一个 query（`query_rows` 单变量覆盖） | `pipeline.py:123,144` |
| P2 | `concurrency_policy == "forbid"` 无锁竞态 | `service.py:563-570` |
| P2 | `pipeline_steps` 零 schema 校验（无枚举 / renderer / template_id 校验） | `schema.py:31,55,106` |
| P2 | pipeline 通道无前端入口，仅能 API / 写库创建 | `ScheduledTaskEditorPage.tsx`（零命中） |
| P2 | docstring 三阶段与实际步骤类型不符（无 process 步骤） | `pipeline.py:3,135-207` |
| P2 | 无连续失败熔断/死信告警 | — |
| P3 | 文档曾遗漏 `pipeline.py`（已在本次文档中补全）；`architecture_analysis.md` 未跟踪 | — |

## 相关提示词/规范

`SCHEDULE_DRAFT_PROMPT`（`service.py` 内）定义草稿提取与 interval/once 判定规则；固定流程抽取规则强制三步确定性执行、禁止模型自行写代码/调 `exec_command`。仓库根 `AGENTS.md`「Fixed-flow scheduled tasks」指向 `fixed_process_workflow.md` 与 `skills/fixed-etl-scheduled-task/SKILL.md`。

## FAQ

- 任务一直 pending：观察 worker 是否运行、租约是否被占、上游模型调用是否拖慢（turn 超时窗口）。
- 想验证成功路径：造一条 interval 任务并跑 `python -m app.scheduled_tasks.worker --once`。
- 任务“成功”但交付物是工具调用 JSON 原文：见记忆沉淀 `scheduled-task-false-success-toolcall-json`。
- 非销售 pipeline 任务却收到销售播报卡：步骤 JSON 未显式指定 `renderer`，缺省落到 `sales_card`（见上文债 1）。

## 相关文件清单

`schema.py`、`service.py`、`worker.py`、`pipeline.py`、`renderers/{__init__,sales_card,generic_table,base}.py`、`fixed_process_workflow.md`、`architecture_analysis.md`（本地未跟踪）；外部：`app/api/scheduled_tasks.py`、`app/db/models.py`（`ScheduledTask`/`ScheduledTaskRun`）、`app/data_query/service.py`、`app/core/`（Harness v2）、`skills/templates/fixed-etl-notify/run.py`（共用销售卡渲染器）。

## 变更记录 (Changelog)

- 2026-09-24T16:45 — 增量更新（聚焦 pipeline 通道）：**新增**「Pipeline 通道与飞书卡片渲染器」一节，登记新 `renderers/` 包（`CARD_RENDERERS` 注册表 / `sales_card.py` / `generic_table.py` / `base.py`）；据提交 `042333ee` 修正事实——销售卡片已抽包、`pipeline.py` 由约 660 行降至 342 行、`run.py` 已改为共用同一 `build_sales_feishu_card`（双份实现漂移消除）、聊天总结改用 `task.title`；据实保留残留债（默认渲染器业务化、`is_first_push` 无条件注入、无 process 步骤、多 query 只渲染最后一个、`pipeline_steps` 零校验、前端无入口）；补「已知缺陷与风险」5 行；标注 `architecture_analysis.md` 为本地未跟踪且 3.1/3.2 节已过时。
- 2026-09-24T09:46 — 增量更新：补「任务 → 文件对照」与「已知缺陷与风险」表（锚点来自 `architecture_analysis.md`）；补 pipeline 通道、Run Now 计数缺陷与 max_runs 语义。
- 2026-09-24 — 完成定时任务板块架构全景剖析、深层缺陷审计与产品化演进方案（输出 `architecture_analysis.md`），补全 `pipeline.py` 索引。
- 2026-09-23T18:06 — 初始化架构师首次生成；记录 interval 调度、租约、Harness v2 成功判定与业务失败传播机制。
