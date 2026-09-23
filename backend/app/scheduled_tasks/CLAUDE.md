[根目录](../../../CLAUDE.md) > [backend](../../) > [app](../) > **scheduled_tasks**

# scheduled_tasks — 定时/周期任务引擎

## 模块职责

把会话中的“定时/定期/自动执行”意图固化为可到点运行的一次性 / 每日 / 每周 / 每月 / 间隔任务，并基于 Harness v2 持久化状态判定结果（绝不只信答复文本）。当前分支热点。

## 入口与文件

| 文件 | 职责 |
|---|---|
| `schema.py` | Pydantic 请求/响应模型（Create/Update/Draft/Read/RunRead）|
| `service.py` | 核心逻辑：草稿检测、调度归一化（`normalize_schedule`）、下次执行时间（`compute_next_run_at`）、RRULE、到期领取租约、执行与 Harness v2 成功判定、SOP 版本快照、飞书通知注入 |
| `worker.py` | 后台 worker（`start_background_worker` / `run_worker`，轮询 `due_scheduled_tasks`）与独立 `--once` CLI |
| `fixed_process_workflow.md` | 固定流程任务（取数-比对-推送）的三步执行规范 |

外部接入：`app/api/scheduled_tasks.py`（路由）；`app/main.py` 生命周期拉起 `start_background_worker`。

## 数据模型

- `ScheduledTask`：`schedule_type ∈ {once,daily,weekly,monthly,interval}`；`status`（active/paused/completed）；`concurrency_policy ∈ {allow,forbid}`、`misfire_policy ∈ {coalesce,skip}`；`lease_owner` / `lease_until` 租约；`metadata_json` 存 SOP 绑定/版本策略与飞书通知配置。
- `ScheduledTaskRun`：单次执行记录，`scheduled_for` + `status`（running/skipped/succeeded/needs_input/failed/incomplete/retrying）。

## 关键机制

- **调度计算**：`compute_next_run_at` 按时区在本地时间计算（默认 `Asia/Shanghai`，默认 09:00）；interval 用 `interval_seconds`/`interval_minutes`；`build_rrule` 生成 RRULE 字符串。
- **租约领取**：`due_scheduled_tasks` 用条件 UPDATE 原子领取到点任务，`LEASE_SECONDS=900` 防多 worker 重复跑。
- **成功判定（防“假成功”）**：`_scheduled_harness_outcome` 只从 Harness v2 持久化读取——需存在 `HarnessTurnRecord`（receipt）、`HarnessTaskFrameRecord`、`HarnessRunRecord`/`HarnessInvocationRecord`；`effective_status` 全 `completed` 才 `succeeded`；技能脚本（`run_skill_script`）返回业务失败时强制 `failed`。
- **业务失败传播**：`_scheduled_business_failures` 解析 `run_skill_script` 结果中的 `ok/status/structured_result.status/push_status` 等字段。
- **SOP 快照**：`metadata_json` 支持 `sop_id` + `sop_version_policy`（latest/pinned）；pinned 用 `expand_sop_for_execution` 生成版本快照，服务端生成、不接受客户端注入。
- **固定流程三步模板**（`SCHEDULE_DRAFT_PROMPT`）：读取技能包 run.py 路径 → 调 `run_skill_script`（timeout 60-120s）→ 读返回 JSON 并简报。

## 相关提示词/规范

`SCHEDULE_DRAFT_PROMPT`（service.py 内）定义草稿提取与 interval/once 判定规则；固定流程抽取规则强制三步确定性执行、禁止模型自行写代码/调 exec_command。

## FAQ

- 任务一直 pending：观察 worker 是否运行、租约是否被占、上游模型调用是否拖慢（turn 超时窗口）。
- 想验证成功路径：造一条 interval 任务并跑 `python -m app.scheduled_tasks.worker --once`。

## 相关文件清单

`schema.py`、`service.py`、`worker.py`、`fixed_process_workflow.md`；外部：`app/api/scheduled_tasks.py`、`app/db/models.py`（ScheduledTask/ScheduledTaskRun）。

## 变更记录 (Changelog)

- 2026-09-23T18:06 — 初始化架构师首次生成；记录 interval 调度、租约、Harness v2 成功判定与业务失败传播机制。