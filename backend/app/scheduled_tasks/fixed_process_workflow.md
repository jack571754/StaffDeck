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
- **凭证**：通过环境变量 `FEISHU_ALERT_WEBHOOK` 或 `FEISHU_SALES_REPORT_WEBHOOK` 读取，绝不在代码或配置明文硬编码；
- **格式**：采用飞书交互式卡片 2.0（包含标题红/蓝模板、Header 汇总字段、7 列自适应表格、涨跌高质感配色、去重说明 Note）；
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
