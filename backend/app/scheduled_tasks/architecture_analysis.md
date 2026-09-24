# 定时任务板块架构全景剖析、深层缺陷审计与产品化演进方案

> **文件路径**：`backend/app/scheduled_tasks/architecture_analysis.md`  
> **编写日期**：2026-09-24  
> **所属模块**：`backend/app/scheduled_tasks`  
> **核心用途**：深入复盘当前定时任务板块的架构设计、执行链路、代码硬编码痛点、系统级技术隐患，并输出产品化与高可用演进路线图。

---

## 目录

- [一、 当前定时任务板块架构全景](#一-当前定时任务板块架构全景)
  - [1.1 模块定位与职责划分](#11-模块定位与职责划分)
  - [1.2 核心数据模型与控制状态机](#12-核心数据模型与控制状态机)
- [二、 脚本执行链路深度梳理（双轨运行模式）](#二-脚本执行链路深度梳理双轨运行模式)
  - [2.1 链路 A：Agent + Harness v2 + `run_skill_script` 沙箱脚本链路](#21-链路-aagent--harness-v2--run_skill_script-沙箱脚本链路)
  - [2.2 链路 B：Pipeline 确定性流水线引擎链路](#22-链路-bpipeline-确定性流水线引擎链路)
  - [2.3 状态强对齐机制（Anti-False-Positive）](#23-状态强对齐机制anti-false-positive)
- [三、 为什么看起来总是需要“修改源码插入脚本”？（5 大根因分析）](#三-为什么看起来总是需要修改源码插入脚本5-大根因分析)
  - [3.1 根因 1：`pipeline.py` 内部侵入了特定品牌的硬编码业务债](#31-根因-1pipelinepy-内部侵入了特定品牌的硬编码业务债)
  - [3.2 根因 2：固定流程脚本 `run.py` 缺乏 SDK 抽象，属于 750 行的沉重胶水代码](#32-根因-2固定流程脚本-runpy-缺乏-sdk-抽象属于-750-行的沉重胶水代码)
  - [3.3 根因 3：前端配置界面严重滞后（黑盒 Prompt，无流水线/脚本入口）](#33-根因-3前端配置界面严重滞后黑盒-prompt无流水线脚本入口)
  - [3.4 根因 4：将批处理强行包装为 Agent 会话的“傀儡中继”成本](#34-根因-4将批处理强行包装为-agent-会话的傀儡中继成本)
  - [3.5 根因 5：资产与配置分散割裂，无法形成合力](#35-根因-5资产与配置分散割裂无法形成合力)
- [四、 当前架构存在的关键缺陷与技术债务审计](#四-当前架构存在的关键缺陷与技术债务审计)
  - [4.1 缺陷 1：Worker 单线程同步阻塞，引发严重的“队头阻塞 (Head-of-Line Blocking)”](#41-缺陷-1worker-单线程同步阻塞引发严重的队头阻塞-head-of-line-blocking)
  - [4.2 缺陷 2：Worker 缺少未捕获异常保护，Daemon 线程存在“静默暴毙”风险](#42-缺陷-2worker-缺少未捕获异常保护daemon-线程存在静默暴毙风险)
  - [4.3 缺陷 3：长生命周期 Session 与事务锁风险 (SQLite Database Locked)](#43-缺陷-3长生命周期-session-与事务锁风险-sqlite-database-locked)
  - [4.4 缺陷 4：进程崩溃后的 900s 租约僵尸与孤儿任务自愈缺失](#44-缺陷-4进程崩溃后的-900s-租约僵尸与孤儿任务自愈缺失)
  - [4.5 缺陷 5：手动触发运行 (Run Now) 污染计划计数与状态](#45-缺陷-5手动触发运行-run-now-污染计划计数与状态)
  - [4.6 缺陷 6：并发策略 `concurrency_policy == "forbid"` 的无锁竞态](#46-缺陷-6并发策略-concurrency_policy--forbid-的无锁竞态)
  - [4.7 缺陷 7：缺乏连续失败熔断与死信告警机制 (Circuit Breaker)](#47-缺陷-7缺乏连续失败熔断与死信告警机制-circuit-breaker)
  - [4.8 缺陷 8：架构文档脱节（CLAUDE.md 遗漏核心组件）](#48-缺陷-8架构文档脱节claudemd-遗漏核心组件)
- [五、 如何让产品能力更强、体验更佳？（系统化演进路线）](#五-如何让产品能力更强体验更佳系统化演进路线)
  - [5.1 维度 1：执行模式清晰分层（三阶执行引擎架构）](#51-维度-1执行模式清晰分层三阶执行引擎架构)
  - [5.2 维度 2：沉淀平台级 `StaffDeck Task SDK`（干掉 700 行样板代码）](#52-维度-2沉淀平台级-staffdeck-task-sdk干掉-700-行样板代码)
  - [5.3 维度 3：飞书卡片 2.0 组件库与可视化渲染器 (WYSIWYG)](#53-维度-3飞书卡片-20-组件库与可视化渲染器-wysiwyg)
  - [5.4 维度 4：前端交互与开发体验跃升（UI & DevX）](#54-维度-4前端交互与开发体验跃升ui--devx)
  - [5.5 维度 5：建立“定时任务应用模板市场 (Task Recipes)”](#55-维度-5建立定时任务应用模板市场-task-recipes)
- [六、 实施阶段与优先级落地清查 (Roadmap)](#六-实施阶段与优先级落地清查-roadmap)

---

## 一、 当前定时任务板块架构全景

### 1.1 模块定位与职责划分

定时任务是 StaffDeck 中将企业周期性巡检、自动播报、数据同步等诉求固化为自动化执行的核心引擎。整体架构分为 **存储层、调度控制层、执行分流层、沙箱执行层与出站通知层**：

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        StaffDeck 定时任务整体分层架构                                   │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │
               ┌────────────────────────────┼────────────────────────────┐
               ▼                            ▼                            ▼
      【1. 存储与调度层】              【2. 执行与路由层】           【3. 执行引擎双轨】
      ScheduledTask / Run            service.py / worker.py        AgentLoop vs Pipeline
┌──────────────────────────────┐ ┌──────────────────────────────┐ ┌──────────────────────────────┐
│ · schedule_type (once/daily/ │ │ · Worker 900s 租约原子抢占   │ │ 轨道 A (默认 Agent 沙箱):    │
│   weekly/monthly/interval)   │ │ · compute_next_run_at 计算   │ │ · 独立 ChatSession          │
│ · concurrency: forbid/allow  │ │ · 路由分发 (pipeline vs agent│ │ · Harness v2 状态持久化      │
│ · misfire: coalesce/skip     │ │ · 业务成败强对齐判定         │ │ · run_skill_script 安全子进程│
│ · metadata_json: 通知与 SOP  │ │ · 飞书多应用/群路由解析      │ ├──────────────────────────────┤
│ · pipeline_steps_json: 步骤  │ └──────────────────────────────┘ │ 轨道 B (Pipeline 引擎):      │
└──────────────────────────────┘                                  │ · 零 LLM、纯代码闭环       │
                                                                  │ · Query -> Notify 节点流     │
                                                                  └──────────────────────────────┘
```

#### 核心代码文件矩阵

| 文件名 | 物理路径 | 模块核心职责 |
|---|---|---|
| `schema.py` | `backend/app/scheduled_tasks/schema.py` | Pydantic 请求与响应模型定义（Task/Run 的 Create、Update、Draft、Read） |
| `service.py` | `backend/app/scheduled_tasks/service.py` | 调度核心逻辑：意图识别、RRULE 计算、原子抢占租约（`due_scheduled_tasks`）、Harness v2 成败对齐判定、SOP 版本快照 |
| `worker.py` | `backend/app/scheduled_tasks/worker.py` | 后台轮询 Worker（`start_background_worker`、`run_worker`），常驻轮询扫描待执行任务 |
| `pipeline.py` | `backend/app/scheduled_tasks/pipeline.py` | 确定性流水线执行器：免除 LLM 思考，直接执行查询、清洗并构造卡片推向飞书 |
| `fixed_process_workflow.md` | `backend/app/scheduled_tasks/fixed_process_workflow.md` | 固定流程任务标准工作流代码规范（取数-处理-去重-推送） |
| `api/scheduled_tasks.py` | `backend/app/api/scheduled_tasks.py` | 企业控制台与对话侧 RESTful 接入路由 |

### 1.2 核心数据模型与控制状态机

1. **`ScheduledTask` 控制字段**：
   - `schedule_type`：支持 `once`（单次）、`daily`（每日）、`weekly`（每周）、`monthly`（每月）、`interval`（固定间隔分钟/秒）；
   - `concurrency_policy`：`forbid`（上一轮未执行完毕则跳过本次）/ `allow`（允许并发叠加）；
   - `misfire_policy`：`coalesce`（失火补偿只补跑一次）/ `skip`（超期直接跳过）；
   - `lease_owner` / `lease_until`：分布式租约标识与有效期（默认 `LEASE_SECONDS=900`），避免多 Worker 抢占冲突；
   - `execution_mode`：`agent`（智能体沙箱模式，默认）/ `pipeline`（流水线模式）；
   - `pipeline_steps_json`：流水线步骤配置列表；
   - `metadata_json`：持久化 SOP 绑定配置与飞书多通道出站配置（`feishu_notify`）。
2. **`ScheduledTaskRun` 单次执行追踪**：
   - 包含 `scheduled_for`、`status`（`running`, `succeeded`, `failed`, `skipped`, `needs_input`, `retrying`）；
   - 记录 `result_summary`、`error` 以及结构化的 `trace_json`。

---

## 二、 脚本执行链路深度梳理（双轨运行模式）

当前系统根据任务的配置，分流为两条不同的执行链路：

### 2.1 链路 A：Agent + Harness v2 + `run_skill_script` 沙箱脚本链路

这是 [fixed_process_workflow.md](fixed_process_workflow.md) 推荐的高频巡检执行路径：

```
① 到点唤醒
  Worker 轮询到期任务 ➔ 条件 UPDATE 原子抢占租约 (lease_until) ➔ 创建独立 ChatSession 与 ScheduledTaskRun
       │
       ▼
② Agent 唤醒与 Prompt 注入
  向 AgentLoop 注入确定性 3 步 Prompt:
  "1. 调用 general_skill 读取 run.py 路径; 2. 调用 run_skill_script 执行; 3. 读取 JSON 并汇报"
       │
       ▼
③ 脚本物化 (Materialize)
  Agent 调用 general_skill.{slug} (operation="read")
  ➔ Harness 从 GeneralSkill 数据库读取文件
  ➔ 安全写入沙箱隔离目录: .harness/skill-packages/{slug}-{digest}/run.py
       │
       ▼
④ 沙箱子进程安全执行
  Agent 调用 run_skill_script(script_path=".harness/skill-packages/.../run.py")
  ➔ 启动独立的 Python 解释器子进程（注入白名单非密环境变量: STAFFDECK_TASK_ID, TENANT_ID）
       │
       ▼
⑤ 脚本内部 4 阶段 ETL 自闭环 (Zero-Token In-Memory Execution)
  ┌─────────────────────────────────────────────────────────────────────────────────┐
  │ 1. 取数 (Fetch)      ➔ HTTP POST /api/mock/data-query/{template_id} (带 HMAC 头)│
  │ 2. 处理 (Process)    ➔ 内存中计算增量/环比/破价（不把万行明细塞入 LLM 上下文）  │
  │ 3. 去重 (Deduplicate)➔ 读写本目录 dedup_history.sqlite 指纹库，过滤已报警项    │
  │ 4. 推送 (Notify)     ➔ 组装飞书卡片 2.0 JSON，调用飞书 Webhook 或内部通道出站   │
  └─────────────────────────────────────────────────────────────────────────────────┘
       │
       ▼
⑥ 结果捕获与状态强对齐 (Anti-False-Positive)
  脚本向 stdout 输出标准 JSON: {"status": "success", "push_status": "success", ...}
  ➔ Harness 捕获 stdout 并解析 structured_result
  ➔ service.py 的 _scheduled_business_failures 强行校验 push_status 与 exit_code
  ➔ 只要飞书失败，即便 LLM 说“已成功”，ScheduledTaskRun.status 仍强制记为 failed
```

### 2.2 链路 B：Pipeline 确定性流水线引擎链路

在 [service.py:661](service.py#L661) 中，当 `task.execution_mode == "pipeline"` 时走直接执行通道：
- 绕过大模型推理，直接由 [pipeline.py](pipeline.py) 解析 `task.pipeline_steps_json`；
- 依次执行 `query` 步骤（调用数据查询中心 `execute_query_by_id`）与 `notify` 步骤（组装卡片调用 `feishu_app_notify`）；
- 自动向关联的 ChatSession 写入系统生成的总结消息与流式事件。

### 2.3 状态强对齐机制（Anti-False-Positive）

为了杜绝大模型在会话中“幻觉汇报成功，但底层任务报错”的假阳性现象，系统在 [service.py:751-923](service.py#L751-L923) 中引入了强校验协议：
1. **只认底层持久化记录**：校验 `HarnessTurnRecord`、`HarnessTaskFrameRecord`、`HarnessInvocationRecord`；
2. **提取业务失败标志**：`_scheduled_business_failures` 会主动解析 `run_skill_script` 输出的标准结构体：
   - 若 `push_status` 为 `"failed"` 或 `"error"`；
   - 或 `exit_code != 0`；
   - 系统将强行覆写 `ScheduledTaskRun.status = "failed"`，并提取标准错误输出存入 `error` 字段。

---

## 三、 为什么看起来总是需要“修改源码插入脚本”？（5 大根因分析）

尽管系统设计了沙箱机制与流水线雏形，但开发者在新增或调整定时任务时，普遍有**“必须去源码里改代码、加脚本”**的强烈体感，根因在于以下 5 个结构性矛盾：

### 3.1 根因 1：`pipeline.py` 内部侵入了特定品牌的硬编码业务债
查看 [pipeline.py:66-88](pipeline.py#L66-L88) 可以看到：
```python
elif cat == "可复美" and item == "整体":
    brand_rows.append(("可复美整体", r))
elif cat == "可丽金" and item == "整体":
    brand_rows.append(("可丽金整体", r))
...
tot_net = float(summary_row.get("净销_万") or 0)
```
- **架构违背**：流水线引擎本是平台公共组件，代码中却**硬编码了特定品牌名称与具体销售指标字段**；
- **直接后果**：一旦面对非美妆品牌、非销售播报的场景（例如“仓库发货超时预警”、“售后退款巡检”），`pipeline.py` 立即失效。开发者没有任何通用配置入口，**必须在 `pipeline.py` 中写 `if-else` 或新增硬编码函数**。

### 3.2 根因 2：固定流程脚本 `run.py` 缺乏 SDK 抽象，属于 750 行的沉重胶水代码
查看 [skills/templates/fixed-etl-notify/run.py](../../skills/templates/fixed-etl-notify/run.py)：
- 一个参考业务脚本足足有 **750 多行代码**，充斥着：
  - 寻找 `.env` 文件路径与读取环境变量；
  - 手写 HMAC-SHA256 签名计算 `X-UltraRAG-Internal-Token`；
  - 本地 SQLite 的建表、DDL、MD5 指纹过滤逻辑；
  - 手工拼接上百行极度易错的飞书卡片 2.0 嵌套 JSON 字典（`column_set`、`chart`、`table`）；
  - 从本地 `config.json` 解析多层级路由。
- **直接后果**：平台没有提供封装好的运行时库。**新增一个报表巡检，开发者必须拷贝一份这 750 行代码并修改里面的字段**，然后打包或放到仓库中，代码冗余且极难维护。

### 3.3 根因 3：前端配置界面严重滞后（黑盒 Prompt，无流水线/脚本入口）
查看 [ScheduledTaskEditorPage.tsx](../../frontend-enterprise/src/pages/scheduled-tasks/ScheduledTaskEditorPage.tsx)：
- 前端只提供了：任务名称、任务 Prompt（文本框）、SOP 下拉、时间周期、飞书通知配置；
- **完全缺失**：
  - 无法在界面上选择“执行模式（Agent / Pipeline / 自定义脚本）”；
  - 无法在界面上绑定“数据查询模板 ID”与动态入参；
  - 无法在线查看或编辑关联脚本；
  - 无法在线设计飞书卡片。
- **直接后果**：用户在界面上只能录入一段黑盒 Prompt。底层的脚本如何执行、从哪个模板取数、如何去重，前端完全不可配，迫使所有能力退化为后台改代码。

### 3.4 根因 4：将批处理强行包装为 Agent 会话的“傀儡中继”成本
- 系统强制让确定性的 Python 批处理伪装成大模型对话：让大模型充当“命令行执行中继器”；
- 增加了 10~30 秒模型冷启动推理开销与 Token 成本，高频轮询时偶发跑偏；
- 脚本必须作为 `GeneralSkill` 的包文件存放在数据库或文件系统中，无法作为独立的“任务脚本资产”单独管理。

### 3.5 根因 5：资产与配置分散割裂，无法形成合力
- 数据模板在 `data_query` 模块；
- 脚本文件存放在 `skills/` 或数据库的 `GeneralSkill`；
- 路由表存在各个目录的 `config.json` 或 `.env`；
- 飞书通道在 `feishu_binding`；
- 各要素缺乏统一实体抽象，打通一个新任务需要在四五个模块间穿梭。

---

## 四、 当前架构存在的关键缺陷与技术债务审计

除了“需要改源码”的体感问题外，当前架构在底层运行机制上存在以下 **8 项技术隐患与缺陷**：

### 4.1 缺陷 1：Worker 单线程同步阻塞，引发严重的“队头阻塞 (Head-of-Line Blocking)”
查看 [worker.py:28-35](worker.py#L28-L35)：
```python
while not _stopped:
    with Session(engine) as db:
        due = due_scheduled_tasks(db)
        for task in due:
            execute_scheduled_task(db, task)   # 🔴 同步阻塞调用！
    sleep(max(1.0, poll_seconds))
```
- **隐患级别**：`CRITICAL (P0)`
- **分析**：Worker 循环在单线程中同步顺序执行所有到期任务。若某个任务走 Agent 模式耗时 2 分钟，**后续所有其他到期任务全部被阻塞在队列中**，造成任务严重积压延误；
- **矛盾**：`service.py:519` 已实现异步线程启动 `start_scheduled_task_async`，但主 Worker 却未采用。

### 4.2 缺陷 2：Worker 缺少未捕获异常保护，Daemon 线程存在“静默暴毙”风险
- **隐患级别**：`CRITICAL (P0)`
- **分析**：`worker.py` 的轮询循环内部**没有任何全局 `try...except` 保护**；
- 一旦数据库连接闪断、遇到死锁异常或意外抛错，`run_worker` 直接跳出退出。由于主程序以 `daemon=True` 线程拉起 Worker，**没有 Supervisor 监控机制，线程崩溃后整个系统的定时任务调度将永久静默停摆**。

### 4.3 缺陷 3：长生命周期 Session 与事务锁风险 (SQLite Database Locked)
- **隐患级别**：`HIGH (P1)`
- **分析**：Worker 外层的 `with Session(engine) as db:` 跨越了多个长耗时任务的执行生命周期；
- 长时间占用数据库连接不仅浪费资源，在 SQLite 环境下极易与前端并发请求冲突，导致 `sqlite3.OperationalError: database is locked`。

### 4.4 缺陷 4：进程崩溃后的 900s 租约僵尸与孤儿任务自愈缺失
- **隐患级别**：`HIGH (P1)`
- **分析**：任务执行前被标记 `lease_until = now + 900s`。若 Worker 进程被重启或意外 Kill，已在 `running` 的任务既未完成也未失败；
- 系统在接下来 15 分钟内无法重新领取该任务，且重启后**缺乏针对孤儿任务的自愈扫描机制**。

### 4.5 缺陷 5：手动触发运行 (Run Now) 污染计划计数与状态
查看 [service.py:1350-1372](service.py#L1350-L1372)：
```python
def _finish_task_schedule(db: Session, task: ScheduledTask, scheduled_for: datetime, status: str, manual: bool) -> None:
    now = utc_now()
    task.last_run_at = now
    task.last_status = status
    task.run_count += 1    # 🔴 manual=True 时也会无条件累加！
    if not manual:
        ...
        if task.max_runs is not None and task.run_count >= task.max_runs:
            task.status = "completed"
```
- **隐患级别**：`HIGH (P1)`
- **分析**：手动测试运行（`manual=True`）也会无条件累加 `task.run_count`；
- 若设置了 `max_runs`（如限次运行任务），用户在页面点击几次“立即运行”测试，就会导致计划运行次数提前耗尽，下次真正的定时触发将被直接误标为 `completed`；手动测试失败还会覆盖计划任务的 `last_status`。

### 4.6 缺陷 6：并发策略 `concurrency_policy == "forbid"` 的无锁竞态
- **隐患级别**：`MEDIUM (P2)`
- **分析**：[service.py:563-570](service.py#L563-L570) 检查上一轮是否有 `running` 状态时，执行的是普通 `SELECT` 查询；
- 当“定时 Worker 到期唤醒”与“用户在页面点击 Run Now”在同一毫秒级发生时，两者会同时查出“没有 running”，进而并发启动两个相同的实例，击穿 `forbid` 约束。

### 4.7 缺陷 7：缺乏连续失败熔断与死信告警机制 (Circuit Breaker)
- **隐患级别**：`MEDIUM (P2)`
- **分析**：若某个外部接口鉴权失效或数据源不可达，高频任务会每 10 分钟执行一次并报错一次，持续刷屏刷日志；
- 缺乏“连续失败 N 次后自动转为 `paused` 并推送死信警报通知运维/管理员”的自我保护机制。

### 4.8 缺陷 8：架构文档脱节（CLAUDE.md 遗漏核心组件）
- **隐患级别**：`LOW (P3)`
- **分析**：模块规范 [CLAUDE.md](CLAUDE.md) 的核心文件清单中仅有 4 个文件，**完全遗漏了 `pipeline.py`**，说明流水线引擎属于边缘补丁，未完成架构资产的统一收敛。

---

## 五、 如何让产品能力更强、体验更佳？（系统化演进路线）

针对上述痛点与缺陷，建议采用“三阶演进方案”：

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              StaffDeck 定时任务演进愿景架构                            │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │
               ┌────────────────────────────┼────────────────────────────┐
               ▼                            ▼                            ▼
     【1. 引擎三层解耦】              【2. 沉淀 Task SDK】          【3. 产品体验重塑】
┌──────────────────────────────┐ ┌──────────────────────────────┐ ┌──────────────────────────────┐
│ Mode 1: 可视化流水线 (No-Code│ │ from staffdeck import:       │ │ · 可视化节点流编排器         │
│  [Query]➔[Filter]➔[Notify]   │ │ · query.fetch(template_id)   │ │ · Monaco 在线脚本编辑器     │
│ Mode 2: 托管轻量脚本 (FaaS)  │ │ · dedup.is_duplicate(key)    │ │ · 飞书卡片 WYSIWYG 实时预览 │
│  仅写 20 行业务计算逻辑      │ │ · card.build_dashboard(...)  │ │ · 单步 Dry-Run 试运行与调试  │
│ Mode 3: 智能体决策 (Agent)   │ │ · notify.push(card)          │ │ · 节点级执行耗时 Trace 图谱  │
│  复杂多步骤、需大模型推理    │ │ (封装内部通信/去重/卡片拼装) │ │ · 开箱即用任务模板市场       │
└──────────────────────────────┘ └──────────────────────────────┘ └──────────────────────────────┘
```

### 5.1 维度 1：执行模式清晰分层（三阶执行引擎架构）

在数据模型与后端调度层明确支持 3 种执行模式（First-Class Citizens）：

1. **可视化流水线模式 (Pipeline Mode - 0 代码 / 0 Token)**：
   - 彻底重构 [pipeline.py](pipeline.py)，将“可复美/可丽金”硬编码完全剔除；
   - 抽象为通用节点引擎：
     - `QueryStep`：选择数据查询中心已有模板（`template_id`）并动态传参；
     - `TransformStep`：基础字段映射、阈值告警规则（如 `current_price < baseline_price`）；
     - `DedupStep`：选择指纹字段与时间窗口（如 24 小时）；
     - `NotifyStep`：选择标准化卡片样式与飞书推送通道。
   - **效果**：80% 的常规数据报表与巡检，业务人员拖拉拽配置即可上线，**0 行代码、0 源码修改**。
2. **托管脚本模式 (Hosted Script Mode - 极简低代码)**：
   - 面向需要复杂数学计算、交叉比对、Pandas 清洗的复杂场景；
   - 不再强求写 750 行胶水代码和打包 `GeneralSkill`；
   - 仅需提供纯函数 `def process(data, ctx): ...`，平台负责前后端调度。
3. **自主智能体模式 (Autonomous Agent Mode - 认知推理)**：
   - 面向真正需要大模型进行语义分析和非确定性决策的场景（如“分析近一周客诉明细提炼共性问题并撰写改进建议”）；
   - 保留 Harness v2 状态机与 SOP 约束。

### 5.2 维度 2：沉淀平台级 `StaffDeck Task SDK`（干掉 700 行样板代码）

将 `run.py` 中重复的基础设施逻辑沉淀为通用 Python SDK：

```python
# 改造后：业务脚本仅需 20 行，业务开发者只需关注业务清洗逻辑
from staffdeck.task import TaskContext, CardBuilder

def main(ctx: TaskContext):
    # 1. 自动注入身份与内部 Token，一行取数
    rows = ctx.fetch_query(template_id=ctx.params.get("template_id"), params={"date": "today"})
    
    # 2. 纯粹的业务计算
    anomalies = [r for r in rows if r["current_price"] < r["baseline_price"]]
    
    # 3. 平台内置去重服务（自动基于 Task ID 记录指纹，支持 SQLite/Redis 统一后端）
    new_alerts = [r for r in anomalies if not ctx.dedup.check_and_mark(f"{r['sku_id']}_{r['current_price']}")]
    
    # 4. 卡片模板化组装（无需手动拼装深层 JSON）
    card = CardBuilder.anomaly_alert(
        title=f"商品破价巡检 - 发现 {len(new_alerts)} 款异常",
        headers=["商品名称", "当前价", "基准价", "异常渠道"],
        rows=[[x["title"], x["current_price"], x["baseline_price"], x["channel"]] for x in new_alerts],
        mention_users=ctx.notify_targets.users
    )
    
    # 5. 自动根据后台配置的飞书应用或 Webhook 出站并回报状态
    ctx.notify.send_card(card)
```

### 5.3 维度 3：飞书卡片 2.0 组件库与可视化渲染器 (WYSIWYG)

1. **标准化 4 类核心卡片模板**：
   - **业务仪表盘卡片**：顶部 3 分栏指标 + 增量变化颜色标签 + 24 小时走势图；
   - **正负排行榜卡片**：Top 5 正增量领跑 / 负增量预警双栏展示；
   - **表格明细卡片**：自适应列宽与富文本状态标签；
   - **高危报警卡片**：高亮红色 Header + 责任人原生 `<at>` 强提醒。
2. **可视化所见即所得设计器**：
   - 前端选择卡片布局，将数据源字段与卡片槽位（Slot）直接拖拽绑定；
   - 右侧实时模拟飞书卡片实际外观，无需写代码反复推送到飞书群调试。

### 5.4 维度 4：前端交互与开发体验跃升（UI & DevX）

升级 [ScheduledTaskEditorPage.tsx](../../frontend-enterprise/src/pages/scheduled-tasks/ScheduledTaskEditorPage.tsx)：
1. **三段式配置向导**：
   - **步骤 1：触发器定义**（名称、员工归属、周期规则、并发控制）；
   - **步骤 2：执行方式切换**：
     - Tab 1: `可视化流水线`（选择查询模板 ➔ 勾选去重 ➔ 选卡片模板）；
     - Tab 2: `自定义轻量脚本`（内嵌 Monaco Editor 在线编辑器，支持 Python 语法提示）；
     - Tab 3: `智能体对话执行`（保留原生 Prompt 模式）；
   - **步骤 3：通知通道**（飞书应用绑定、群聊、人员强提醒联动）。
2. **单步调试与试运行 (Dry-Run)**：
   - 在保存前提供**“立即试运行 (Test Run)”**；
   - 实时弹窗展示：① 取数行数与样例预览；② 过滤与去重命中数；③ 渲染生成的飞书卡片外观；④ 模拟推送状态。
3. **节点级 Trace 可观测性**：
   - 在执行历史列表中点击单次 Run，展开分步甘特图：
     `[取数: 120ms (1280行)] ➔ [内存清洗: 15ms] ➔ [去重过滤: 8ms (过滤9条,新增3条)] ➔ [飞书推送: 320ms (HTTP 200)]`。

### 5.5 维度 5：建立“定时任务应用模板市场 (Task Recipes)”

为高频业务场景沉淀开箱即用的官方配方：
- 《全渠道商品价格异常巡检与飞书推送》
- 《电商大盘与各店铺实时销售动态播报》
- 《仓库缺货/库存周转预警》
- 《超时未回复工单巡检》

**业务人员体验**：从模板市场点击“使用模版” ➔ 仅需配置数据库连接与飞书群 ➔ 保存即用，全流程研发 0 介入。

---

## 六、 实施阶段与优先级落地清查 (Roadmap)

| 阶段 | 周期 | 核心改动点 | 预期成果 |
|---|---|---|---|
| **Phase 1: 底座加固与高可用 (P0)** | 1~2 天 | 1. `worker.py` 改用线程池/异步分发，消除队头阻塞<br>2. Worker 主循环增加全局 `try...except` 保护与保活日志<br>3. `_finish_task_schedule` 将 `manual=True` 运行脱离 `run_count` 累加<br>4. 启动时自愈扫描超过 900s 的孤儿 running 任务 | 彻底解决 Worker 阻塞、假死停摆与手动测试污染计数的严重稳定性隐患 |
| **Phase 2: 引擎抽象与业务解耦 (P1)** | 3~5 天 | 1. 剥离 `pipeline.py` 中的美妆硬编码字段，重构为通用节点执行器<br>2. 提炼公共 `StaffDeck Task SDK`，封装 HMAC 鉴权、SQLite 去重与卡片构建<br>3. 梳理并更新 [CLAUDE.md](CLAUDE.md) 同步架构规范 | 消除“新增业务必须改源码”的技术债务，将业务脚本从 750 行精简至 20 行 |
| **Phase 3: 前端低代码化与可观测 (P2)** | 5~7 天 | 1. 前端编辑页支持切换“可视化流水线”并绑定数据查询模板<br>2. 实现卡片在线预览与保存前单步 Dry-Run 试运行<br>3. 支持连续失败自动熔断（Circuit Breaker）与管理员通知<br>4. Run 记录展示节点级耗时甘特图 | 形成完整易用的产品化控制面，业务人员无需写代码即可自助上线定时任务 |
