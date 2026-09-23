---
name: fixed-etl-scheduled-task
description: 固定流程定时任务标准工作流（取数-处理-去重-飞书推送）。当需要为数字员工配置或执行高频定时巡检、周期报表播报、异常监控并推送到飞书群时，强制遵循本流程规范，禁止自由生成发散代码或调用 exec_command。
---

# 固定流程定时任务标准工作流 (Fixed ETL & Feishu Notify Workflow)

本文件是 StaffDeck 平台针对**“周期性数据巡检、业务播报、异常监控与飞书群推送”**定义的标准化、确定性工作流规范。  
无论是**代码开发助手（AI Assistant）**生成配置，还是**数字员工（Digital Employee / Agent）**在运行时执行任务，均必须严格遵循本规范。

---

## 一、 适用场景与意图识别 (Intent Recognition)

当用户或任务指令包含以下意图特征时，应直接识别并命中本工作流：
- **触发场景**：周期性自动运行（如“每10分钟”、“每小时”、“每天早上9点”、“持续轮询”）；
- **业务操作**：从数据源/查询模板“取数/查数据/拉报表”；
- **业务逻辑**：对数据进行“指标计算/比对基准/检测破价/发现异常/过滤去重”；
- **交付目标**：向“飞书群/群机器人/企业微信/运维卡片”推送格式化排版卡片或表格；
- **常见任务**：全渠道价格破价巡检、实时销售数据播报、库存缺货预警、服务超时告警。

---

## 二、 核心工作边界与铁律 (Hard Constraints)

1. **⛔ 严禁自由发散与即兴写代码**：
   - 严禁让大模型在会话中临场编写 Python/Bash 脚本去连接数据库；
   - 严禁调用 `exec_command` 执行未受控的脚本（Windows 环境下使用 bash 语法如 `&&`, `||`, `/dev/null` 会被沙箱强制拒绝）；
2. **⛔ 严禁大结果集穿透上下文**：
   - 数据查询返回的原始明细（数十到上千行）一律在 Python 进程内存内处理，严禁塞入大模型聊天上下文，杜绝触发 `sandbox_json_file` 截断死锁；
3. **✅ 必须采用两步受控执行模式**：
   - 步骤 1：调用能力获取指定技能包内固化的 `run.py` 文件路径；
   - 步骤 2：调用安全沙箱工具 `run_skill_script` 执行，由脚本内部完成取数、计算、去重与卡片直推；
4. **✅ 必须保持业务成败与系统状态对齐**：
   - 脚本必须在 stdout 输出标准 JSON（含 `push_status`），飞书返回失败时必须以非 0 退出码退出，调度层会自动标记任务 `failed`，绝不吞掉错误。

---

## 三、 运行时标准执行协议 (Runtime Execution Protocol)

数字员工（Agent）在被定时任务唤醒执行时，执行链必须严格限制为 **3 步闭环**：

```
                    ┌────────────────────────────────────────────────────────┐
                    │            数字员工定时唤醒标准 3 步执行链               │
                    └──────────────────────────┬─────────────────────────────┘
                                               │
               ┌───────────────────────────────┼───────────────────────────────┐
               ▼                               ▼                               ▼
      【第 1 步：读取技能路径】       【第 2 步：沙箱安全运行】       【第 3 步：如实汇报并结束】
      · 调用 general_skill           · 调用 run_skill_script         · 解析 stdout JSON 结果
      · operation="read"             · script_path="<run.py>"        · 汇报：巡检数、异常数、推送状态
      · 获取物化后的 run.py          · 内存取数 + 比对 + 去重 + 卡片  · 汇报后立即结束，严禁追加动作
```

### 动作 1：读取技能包路径
```json
{
  "name": "general_skill.{skill_slug}",
  "arguments": {
    "operation": "read"
  }
}
```
*返回数据中将包含物化后的执行脚本相对路径（如 `.harness/skill-packages/{slug}/run.py`）。*

### 动作 2：调用 run_skill_script 执行
```json
{
  "name": "run_skill_script",
  "arguments": {
    "script_path": "<第1步返回的 run.py 路径>",
    "argv": ["--mode", "audit"],
    "timeout_seconds": 60,
    "max_output_bytes": 65536
  }
}
```

### 动作 3：读取结果汇报并退出
脚本 `stdout` 必须返回标准 JSON：
```json
{
  "status": "success",
  "push_status": "success",
  "total_checked": 1280,
  "new_items": 3,
  "skipped_duplicates": 5,
  "message": "已成功推送 3 条新异常至飞书监控群"
}
```
数字员工根据返回直接向会话回复简报（如：“巡检完成，共核对 1280 款商品，发现 3 款新增异常，已自动推送至飞书群”），回复后**立即终结本次对话**。

---

## 四、 执行脚本内部四模块规范 (`run.py` 结构)

每个业务专用的固定流程脚本必须完整内聚以下 4 个标准模块（参考代码位于 `skills/templates/fixed-etl-notify/run.py`）：

### 1. 取数模块 (Fetch)
- **端点**：`POST http://127.0.0.1:5173/api/mock/data-query/{template_id}`
- **认证**：通过头信息携带 `X-UltraRAG-Internal-Token`（支持 HMAC 自动计算或读取环境变量 `STAFFDECK_INTERNAL_TOKEN`）；
- **参数**：支持日期参数动态化（`{"params": {"end_date": "YYYY-MM-DD"}}`）。

### 2. 处理模块 (Process)
- **零 Token 计算**：在 Python 内存中对 `rows` 执行 Pandas/字典清洗、同比环比或阈值破价判定；
- **提炼收敛**：仅筛选出符合预警条件的记录，组装为高密度数据字典。

### 3. 去重模块 (Deduplicate)
- **状态存储**：在技能包同级目录下维护轻量 SQLite 状态库 `dedup_history.sqlite`；
- **唯一指纹**：计算唯一 MD5 指纹 `md5(f"{YYYY-MM-DD}_{channel}_{id}_{value}")`；
- **防骚扰逻辑**：近 24 小时已推送过的相同指纹自动归入 `skipped_duplicates`，只有当出现新异常或指标变动时才放行。

### 4. 推送模块 (Notify)
- **卡片装配**：原生组装飞书交互式卡片 2.0（Header 色带 + 顶部指标概览栏 + 自适应表格 + 底部防重注释）；
- **Webhook 出站**：向环境变量 `FEISHU_ALERT_WEBHOOK` 或 `FEISHU_SALES_REPORT_WEBHOOK` 发送；
- **成功落库**：仅在 Webhook 返回 HTTP 200 后才将指纹持久化入库。

---

## 五、 AI 自动识别与草案生成模板 (AI Draft Template)

当 AI 助手或服务端大模型在会话中识别到用户需要配置此类定时任务时，生成的 `ScheduledTask` 必须遵循以下参数标准：

| 配置字段 | 推荐值 / 模板规范 | 说明 |
|---|---|---|
| **title** | 包含业务名与周期（如 `全渠道价格巡检与飞书推送(每10分钟)`） | 12-32 个中文字符 |
| **schedule_type** | `interval`（分钟循环）或 `daily`（每天定点） | 满足高频监控诉求 |
| **concurrency_policy** | `forbid` | **强制**：上一轮未结束则跳过，防止并发重叠 |
| **misfire_policy** | `coalesce` | 超过窗口合并执行一次 |
| **prompt** | 见下方标准确定性 Prompt 模板 | 严禁写自由发散话术 |

### 标准确定性任务描述 (Prompt)
```text
你是{业务角色}员工。请严格按以下步骤确定性执行，禁止自由发挥或调用 exec_command：

1. 调用对应业务能力的技能包 general_skill.{skill_slug}（operation="read"），获取执行脚本 run.py 的文件路径；
2. 调用工具 run_skill_script 执行脚本（设置 timeout_seconds=60）；
3. 读取 run_skill_script 返回的 JSON 结构并如实简报（包含巡检总数、发现数、推送状态），汇报后立即结束任务。
```
