# 固定流程定时任务（取数-处理-去重-飞书推送）代码路径工作流规范

> **代码路径**：`backend/app/scheduled_tasks/fixed_process_workflow.md`  
> **核心用途**：供**大模型 AI（对话草案提取器 / 数字员工 Agent / 编程辅助 AI）**在代码层面直接识别、检索并严格遵循的标准固定流程规范。  
> **设计目标**：将高频周期性任务（巡检、播报、监控）从不可控的“自由 Agent 思考循环”收敛为**“确定性代码闭环（0 Token、0 沙箱截断、0 幻觉超时、本地指纹去重）”**。

---

## 一、 为什么必须采用代码级固定流程？

在 StaffDeck 平台实测中，让大模型自主通过 ReAct 自由循环处理高频定时任务（如每 10 分钟销售播报或价格巡检）存在结构性缺陷：
1. **耗时与超时**：国内主流模型单次响应需 20s~19min，7~8 轮交互必然触发 480s 任务硬预算超时（`TURN_BUDGET_TIMEOUT`）；
2. **大结果集截断自死锁**：SQL 查询返回超过 10KB 明细，被 Harness 沙箱截断落盘为 `sandbox_json_file` 句柄，模型尝试写脚本去读取，引发语法与死循环；
3. **Windows 平台兼容**：弱模型常生成 Linux bash 语法（`&&`, `||`, `/dev/null`），在 Windows 平台直接被安全层拒绝；
4. **缺乏去重与状态对齐**：模型在会话中没有持久化状态，每 10 分钟会对同一破价异常重复刷屏；飞书推送报错时模型仍可能盲目汇报“成功”。

**解决方案**：采用本规范定义的**“数据查询中心取数 ➔ Python 内存处理 ➔ SQLite 指纹去重 ➔ 飞书卡片 2.0 原生装配 ➔ 状态强对齐”**模式。

---

## 二、 代码模块流转与调用链

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        StaffDeck 固定流程定时任务代码模块流转                          │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │
               ┌────────────────────────────┼────────────────────────────┐
               ▼                            ▼                            ▼
   【1. 调度与草案层】              【2. 执行与沙箱层】           【3. 数据中心与外设】
   app.scheduled_tasks              app.harness                   app.data_query & Feishu
   ┌──────────────────────┐         ┌───────────────────┐         ┌────────────────────────┐
   │ SCHEDULE_DRAFT_PROMPT│ ──生成──►│ run_skill_script │ ──启动──►│ POST /api/mock/data-  │
   │ 识别固定流程意图并组装 │ Prompt  │ (skill_script.py) │ 子进程  │ query/{template_id}    │
   │ 确定性 3 步 Prompt   │         │                   │         │ (带 Internal-Token)    │
   └──────────┬───────────┘         └─────────┬─────────┘         └───────────┬────────────┘
              │ 唤醒                          │                             │ 返回结构化
              ▼                               ▼ 读取 stdout JSON            ▼ rows/columns
   ┌──────────────────────┐         ┌───────────────────┐         ┌────────────────────────┐
   │ execute_scheduled_   │ ◄───────│ 结构化结果校验    │ ◄───────│ run.py 内部处理:       │
   │ task (service.py)    │ 捕获业务 │ push_status 判定  │ 完成推送    │ 1. 内存清洗与指标比对  │
   │ push_status 失败记为 │ 状态     │ exit_code 退出码  │ 并落库指纹  │ 2. SQLite 指纹去重     │
   │ task failed          │         └───────────────────┘         │ 3. 飞书卡片 2.0 原生装配│
   └──────────────────────┘                                       └────────────────────────┘
```

---

## 三、 AI 自动识别与草案抽取规范 (AI Drafting Protocol)

服务端在 [service.py](service.py) 中通过 `detect_scheduled_task_draft` 提取用户意图。**AI 在识别到此类需求时，必须输出符合以下规范的 JSON**：

### 1. 意图判定触发词
- 用户意图包含：“定时 / 周期 / 每N分钟 / 每天定点 / 轮询”；
- 并且包含：“取数 / 查数据 / 巡检 / 监控 / 播报 / 报表”；
- 并且交付目标为：“推送飞书群 / 发给机器人 / 发预警卡片 / 报表推送”。

### 2. 字段填充规范
- `title`：12~32 个字符，必须明确业务与频次，例如 `全渠道商品价格异常巡检与飞书推送`；
- `schedule_type`：优先 `interval`（分钟轮询，默认 10）或 `daily`（每天定点，如 09:00）；
- `concurrency_policy`：强制设为 `forbid`（上一轮未结束则跳过，杜绝并发堆叠）；
- `misfire_policy`：设为 `coalesce`；
- `prompt`：**必须严格采用以下确定性三步模板，严禁发散自由话术**：

```text
你是{业务领域}员工。请严格按以下步骤确定性执行，禁止自由发挥或调用 exec_command：

1. 调用对应业务能力的技能包 general_skill.{skill_slug}（operation="read"），获取执行脚本 run.py 的文件路径；
2. 调用工具 run_skill_script 执行脚本（设置 timeout_seconds=60-120）；
3. 读取 run_skill_script 返回的 JSON 结构并如实简报（包含巡检总数、发现数、推送状态），汇报后立即结束任务。
```

---

## 四、 核心执行脚本四大模块代码契约 (`run.py`)

业务技能脚本（参考脚手架位于 `skills/templates/fixed-etl-notify/run.py`）必须完整内聚以下四部分：

### 模块 1：数据查询模板调用 (Fetch)
- **协议**：HTTP POST；
- **端点**：`http://127.0.0.1:5173/api/mock/data-query/{template_id}`；
- **认证头**：`X-UltraRAG-Internal-Token`（通过 `app_secret` 与 `ultrarag-internal-mock-api-v1` 计算 HMAC-SHA256，或读取环境变量 `STAFFDECK_INTERNAL_TOKEN`）；
- **参数动态化**：自动传入今日日期 `{"params": {"end_date": "YYYY-MM-DD"}}`。

### 模块 2：内存清洗与比对 (Process)
- 数据直接在 Python 进程的堆内存中循环判定，严禁回传给 LLM；
- 破价判定条件：`current_price < baseline_price`；
- 播报判定条件：计算当前汇总值、24小时环比增减幅。

### 模块 3：本地状态指纹去重 (Deduplicate)
- **存储介质**：脚本所在目录的 `dedup_history.sqlite`；
- **指纹计算**：`MD5(f"{YYYY-MM-DD}_{channel}_{identifier}_{price}")`；
- **逻辑**：查询表中若存在该指纹且在当天窗口内，则归入 `skipped_duplicates`，不重复推送到群，避免警报轰炸。

### 模块 4：飞书卡片 2.0 装配与推送 (Notify)
- **多任务与多员工分流路由机制 (Task & Agent Routing)**：
  1. **任务专属传参（最高优先级）**：定时任务 Prompt 中直接携带 `--task-title`、`--webhook-url`、`--notify-users`，同一员工名下的不同任务可完全推向不同群与人员；
  2. **任务级路由表 (`task_routes`)**：在 `config.json` 中按任务 ID 或任务标题关键字配置（如“自营价格巡检”进自营群，“分销破价”进分销群）；
  3. **员工级路由表 (`agent_routes`)**：任务未单独配置时，回退到该数字员工名下的默认群；
  4. **全局环境变量（兜底统一管理）**：`backend/.env` 中的 `FEISHU_SALES_REPORT_WEBHOOK`、`FEISHU_ALERT_WEBHOOK` 或 `STAFFDECK_TASK_{ID}_WEBHOOK`；
- **推送应用由服务端权威解析（勿在 Prompt 中硬编码应用标识）**：
  - 一个租户可接入多个飞书自建应用，推送与群列表都必须先确定"用哪个应用"；解析收口在 `app/channels/feishu_binding.py`；
  - 定时任务的「飞书消息通知配置」可选定 `binding_id`；脚本只需在请求体带 `scheduled_task_id`，服务端即按该任务所选应用发卡。**显式指定的应用失效时报错，绝不回退**到另一个应用（静默回退会把卡片发到错误的企业应用且无人知情）；
  - 脚本子进程可读取 `STAFFDECK_TASK_ID` / `STAFFDECK_TENANT_ID` 两个非密标识（白名单见 `app/harness/command.py` 的 `_SKILL_CONTEXT_ENV_KEYS`）；凭证类变量（`APP_SECRET`、`STAFFDECK_INTERNAL_TOKEN`、模型 Key）**永不**下传，应用标识也不经 argv 传递；
  - `--binding-id` 仅作覆盖/一致性断言用途，不作为权威通道。
- **飞书卡片 @人员 (Mention) 原生渲染**：
  - 飞书交互式卡片原生支持在卡片中装配 `<at id="ou_xxxx">张三</at>` 或 `<at id="all">所有人</at>`，卡片送达时触发强提醒；
- **结构与层次**：
  1. 顶部指标汇总栏 (`column_set` 3 权重分栏，汇总今日净销、达播净销、运营端净销，并附带各自较上一时刻增量)；
  2. 整体销售播报摘要（截止时刻、电商整体净销及增量，以及运营端与达播端各自较上一时刻的增量数据，例：`其中运营端 378.89万，较上一时刻增量 -19.27万；达播端 44.81万，较上一时刻增量 +24.33万`）；
  3. **中间板块（店铺正负增量动态排行 Top 5）**：
     - 在整体播报与明细表格之间，采用飞书卡片 2.0 原生双栏对齐（`column_set` bisect 灰色底色）；
     - **左栏（📈 正增量领跑 Top 5）**：绿色高亮展示增量幅度（如 `+1.29万`）及累计净销；
     - **右栏（📉 负增量预警 Top 5）**：红色高亮展示掉单/负增量幅度（如 `-0.01万`）及累计净销，若无负增量店铺则提示“其余活跃店铺增量均为正或持平”；
  4. 品牌与渠道销售明细表格（7 列规范自适应列宽表格）；
  5. 24 小时时段走势环比柱状图 (`chart` 原生图表，今日 vs 昨日）。
- **出站**：POST 到 Webhook，成功后将新指纹落库记录时间。

---

## 五、 状态强对齐与返回值契约 (Result Schema)

脚本在执行完毕前，必须向标准输出 `stdout` 打印单个 JSON 字典：

```json
{
  "status": "success",
  "push_status": "success",
  "total_checked": 1280,
  "new_items": 3,
  "skipped_duplicates": 9,
  "message": "已成功推送 3 条异常预警至飞书群"
}
```

### 调度器成败对齐规则（位于 [service.py:748](service.py#L748) `_scheduled_business_failures`）：
1. 若 `push_status` 为 `"failed"` 或 `"error"`：
   - 系统自动将该次 `ScheduledTaskRun.status` 标记为 `failed`；
   - 提取 `message` 字段作为系统错误日志 `ScheduledTaskRun.error`；
   - 坚决杜绝“脚本业务失败但调度记录显示成功”的假阳性。
2. 若 `push_status` 为 `"skipped"`（全部去重，无新增）：
   - 系统视为成功运行，记录日志，不打扰群成员。
3. 若 `exit_code != 0`：
   - 平台捕获 `stderr` 并将任务判定为异常退出。

---

## 六、 常见问题与排障备忘

1. **HTTP 401 Unauthorized**：
   - 检查脚本是否正确计算并携带了 `X-UltraRAG-Internal-Token`；
   - 确保 `APP_SECRET` 环境变量与后端 `backend/.env` 一致。
2. **日期参数格式错误 (ValueError: must be YYYY-MM-DD)**：
   - 不要向数据查询接口直接传递未转换的汉字“今天”，应在脚本中提前使用 `datetime.now().strftime("%Y-%m-%d")` 格式化。
3. **Windows 平台提示命令被拦截 (Bash command chaining is not supported)**：
   - 定时任务 Prompt 中严禁使用 `&&`、`||` 或 `exec_command`，必须使用平台专用的 `run_skill_script` 工具。
