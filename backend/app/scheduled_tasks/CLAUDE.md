[根目录](../../../CLAUDE.md) > [backend](../../) > [app](../) > **scheduled_tasks**

# scheduled_tasks — 定时/周期任务引擎

## 模块职责

把会话中的“定时/定期/自动执行”意图固化为可到点运行的一次性 / 每日 / 每周 / 每月 / 间隔任务，并基于 Harness v2 持久化状态判定结果（绝不只信答复文本）。当前分支热点。

## 入口与文件

| 文件 | 职责 |
|---|---|
| `schema.py` | Pydantic 请求/响应模型（Create/Update/Draft/Read/RunRead）|
| `service.py` | 核心逻辑：草稿检测、调度归一化（`normalize_schedule`）、下次执行时间（`compute_next_run_at`）、RRULE（`build_rrule`）、到期领取租约（`due_scheduled_tasks`）、执行与 Harness v2 成功判定、SOP 版本快照、飞书通知注入 |
| `worker.py` | 后台 worker（`start_background_worker` / `run_worker`，轮询 `due_scheduled_tasks`）与独立 `--once` CLI |
| `pipeline.py` | 确定性流水线执行器（支持无 LLM 的 Query -> Process -> Notify 链路）|
| `fixed_process_workflow.md` | 固定流程任务（取数-比对-推送）的三步执行规范 |
| `architecture_analysis.md` | 定时任务板块架构全景剖析、深层缺陷审计（8 项）与产品化演进方案（本文件是深读入口）|

外部接入：`app/api/scheduled_tasks.py`（`enterprise_router` / `chat_router` / `chat_draft_router`）；`app/main.py` 生命周期拉起 `start_background_worker`。

## 数据模型

- `ScheduledTask`（表 `scheduled_tasks`）：`schedule_type ∈ {once,daily,weekly,monthly,interval}`；`status`（active/paused/completed）；`concurrency_policy ∈ {allow,forbid}`、`misfire_policy ∈ {coalesce,skip}`；`lease_owner` / `lease_until` 租约；`execution_mode ∈ {agent,pipeline}`；`pipeline_steps_json`；`metadata_json` 存 SOP 绑定/版本策略与飞书通知配置（`feishu_notify`）；`max_runs` / `run_count` / `last_run_at` / `last_status`。
- `ScheduledTaskRun`（表 `scheduled_task_runs`）：单次执行记录，`scheduled_for` + `status`（running/skipped/succeeded/needs_input/failed/incomplete/retrying）；`result_summary`、`error`、`trace_json`。

## 关键机制

- **调度计算**：`compute_next_run_at` 按时区在本地时间计算（默认 `Asia/Shanghai`，默认 09:00）；interval 用 `interval_seconds`/`interval_minutes`；`build_rrule` 生成 RRULE 字符串。
- **租约领取**：`due_scheduled_tasks` 用条件 UPDATE 原子领取到点任务，`LEASE_SECONDS=900` 防多 worker 重复跑。
- **成功判定（防“假成功”）**：`_scheduled_harness_outcome`（`service.py` 约 L751-923）只从 Harness v2 持久化读取——需存在 `HarnessTurnRecord`（receipt）、`HarnessTaskFrameRecord`、`HarnessRunRecord`/`HarnessInvocationRecord`；`effective_status` 全 `completed` 才 `succeeded`。
- **业务失败传播**：`_scheduled_business_failures` 解析 `run_skill_script` 结果中的 `ok/status/structured_result.status/push_status/exit_code`；`push_status ∈ {failed,error}` 或 `exit_code != 0` 时强制 `ScheduledTaskRun.status = "failed"`。
- **SOP 快照**：`metadata_json` 支持 `sop_id` + `sop_version_policy`（latest/pinned）；pinned 用 `expand_sop_for_execution` 生成版本快照，服务端生成、不接受客户端注入。
- **固定流程三步模板**（`SCHEDULE_DRAFT_PROMPT`）：读取技能包 run.py 路径 → 调 `run_skill_script`（timeout 60-120s）→ 读返回 JSON 并简报。
- **Pipeline 通道**：`service.py` 约 L661 判断 `execution_mode == "pipeline"`，交 `pipeline.py` 解析 `pipeline_steps_json`，执行 `query`（调 `data_query.service.execute_query_by_id`）与 `notify`（调 `feishu_app_notify`）节点，并向关联 `ChatSession` 写入系统总结与流式事件。

## 任务 → 文件对照（AI 定位用）

| 想改什么 | 去看 |
|---|---|
| 调度周期计算 / RRULE / 时区 | `service.py`（`normalize_schedule` / `compute_next_run_at` / `build_rrule`） |
| 到点领取与并发/失火策略 | `service.py`（`due_scheduled_tasks`、`concurrency_policy`、`misfire_policy`、`LEASE_SECONDS`） |
| “假成功”判定 / 业务失败传播 | `service.py`（`_scheduled_harness_outcome`、`_scheduled_business_failures`） |
| 手动运行（Run Now）与计数 | `service.py`（`_finish_task_schedule`，约 L1350-1372；`manual=True` 仍累加 `run_count` 是已知缺陷 5） |
| 无 LLM 的确定性取数-推送 | `pipeline.py`（注意其中含品牌硬编码业务债，缺陷根因 1） |
| Worker 轮询/阻塞/异常保护 | `worker.py`（缺陷 1 队头阻塞、缺陷 2 无 try/except） |
| 固定流程任务的提示词与规范 | `SCHEDULE_DRAFT_PROMPT`（`service.py` 内）、`fixed_process_workflow.md` |
| 架构缺陷全景与演进 Roadmap | `architecture_analysis.md` |

## 已知缺陷与风险（摘自 `architecture_analysis.md`）

| 级别 | 缺陷 | 锚点 |
|---|---|---|
| P0 | Worker 单线程同步执行导致队头阻塞 | `worker.py:28-35`（`execute_scheduled_task` 同步调用） |
| P0 | Worker 主循环无全局异常保护，daemon 线程可静默暴毙 | `worker.py` `run_worker` |
| P1 | 外层 `with Session(engine)` 跨越长任务，SQLite `database is locked` | `worker.py` |
| P1 | 进程崩溃后 900s 租约僵尸 + 孤儿任务无自愈扫描 | `service.py` / 启动路径 |
| P1 | Run Now 污染 `run_count`，提前触发 `max_runs` | `service.py:1350-1372` |
| P2 | `concurrency_policy == "forbid"` 无锁竞态 | `service.py:563-570` |
| P2 | 无连续失败熔断/死信告警 | — |
| P3 | 文档曾遗漏 `pipeline.py`（已在本次文档中补全） | — |

## 相关提示词/规范

`SCHEDULE_DRAFT_PROMPT`（`service.py` 内）定义草稿提取与 interval/once 判定规则；固定流程抽取规则强制三步确定性执行、禁止模型自行写代码/调 `exec_command`。仓库根 `AGENTS.md`「Fixed-flow scheduled tasks」指向 `fixed_process_workflow.md` 与 `skills/fixed-etl-scheduled-task/SKILL.md`。

## FAQ

- 任务一直 pending：观察 worker 是否运行、租约是否被占、上游模型调用是否拖慢（turn 超时窗口）。
- 想验证成功路径：造一条 interval 任务并跑 `python -m app.scheduled_tasks.worker --once`。
- 任务“成功”但交付物是工具调用 JSON 原文：见记忆沉淀 `scheduled-task-false-success-toolcall-json`。

## 相关文件清单

`schema.py`、`service.py`、`worker.py`、`pipeline.py`、`fixed_process_workflow.md`、`architecture_analysis.md`；外部：`app/api/scheduled_tasks.py`、`app/db/models.py`（`ScheduledTask`/`ScheduledTaskRun`）、`app/data_query/service.py`、`app/core/`（Harness v2）。

## 变更记录 (Changelog)

- 2026-09-24T09:46 — 增量更新：补「任务 → 文件对照」与「已知缺陷与风险」表（锚点来自 `architecture_analysis.md`）；补 pipeline 通道、Run Now 计数缺陷与 max_runs 语义。
- 2026-09-24 — 完成定时任务板块架构全景剖析、深层缺陷审计与产品化演进方案（输出 `architecture_analysis.md`），补全 `pipeline.py` 索引。
- 2026-09-23T18:06 — 初始化架构师首次生成；记录 interval 调度、租约、Harness v2 成功判定与业务失败传播机制。
