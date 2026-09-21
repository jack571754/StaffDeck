---
name: check-price-anomalies
description: "多店铺价格合规性监控：巡检京东/抖音/拼多多/唯品会各店铺商品价格，核对是否低于飞书维护基准价、检测同一天价格跳变。"
---

# 电商各店铺改价与异动监控

连接业务数据库对多平台商品做价格异动清洗，并与飞书多维表格（S促普惠价/618/D11）动态对比，输出异动清单。

## ⚠️ 核心工作边界与铁律
1. **零自动处置权**：严禁自动修改商品后台价格，严禁自动向商家下架。
2. **强制人工复核**：所有异动必须附带人工二次复核合规提示。
3. **禁止自行编写替代脚本**：一律使用内置工具 `run_skill_script` 运行本技能包内的 `run.py`，不要用 code runner / write_file 另起炉灶。

## 执行方式

**只有用户明确要求"全网巡检 / 检查异常 / 价格异动监控"时才进入 audit 模式**：

**第 1 步：读取本技能包。** 调用 `general_skill` 能力（operation=`read`，query=<用户巡检诉求>），用返回的 `entrypoint_path`（在 workspace 的 `.harness/skill-packages/` 下，目录名含摘要后缀）。**必须用返回路径，不要虚构。**

**第 2 步：执行。** 用工具 `run_skill_script`，`script_path` 填第 1 步返回的 `entrypoint_path`：

```json
{
  "script_path": "<上一步返回的 entrypoint_path>",
  "argv": ["--mode", "audit"],
  "timeout_seconds": 60,
  "max_output_bytes": 131072
}
```

- 若用户只是问**单个商品的价格**，不要走本技能，改叫 `product-price-lookup` 速查，避免无谓全库扫描。
- 若确无 `entrypoint_path`（非 Harness 会话），再用本包内 `run.py` 本地运行一次，不要自行编写脚本。

## 智能体调用话术（策略卡）

把本包打造成 StaffDeck 数字员工可直接对话召唤的价格巡检智能体：将 `AGENT_PROMPT.md` 贴进数字员工
persona_prompt / 技能 usage 提示词。它把调用路径固化到判断层（analyze/down/learn/memory），要求 LLM
**只做最终表达**、汇报严格引用 `ai_material`/`summary`、且强制合规红线。所有结论来自确定性输出，禁止自行推断价格。

## 判断层（把巡检从「报警器」升级为「分析员」）

`audit` 只产出原始异动清单；当运营问「为什么、要不要处理、是否跨平台联动」时，走**判断层** `price_analyst.py`（确定性、无 LLM 依赖），LLM 只做最终话术表达：

- **`--mode analyze`**：复用 `run.py --mode audit` 取数（子进程，不重复 SQL/基准逻辑），对原清单做：
  - 跨平台归类：按飞书商品昵称把同一商品在京东/抖音/唯品会的联动异动聚为一条，识别「同款全渠道受影响」；
  - 去噪/置信标记：跌破维护普惠价=高/中置信（需处置），纯日内波动按幅度分级，低置信并入摘要不逐条告警；
  - 产出 `summary` + `ai_material`：浓缩文本 + 「禁止自动改价」合规红线，供智能体直接汇报。
- **`--mode down --platform <平台> --sku <id> [--limit N]`**：单商品下钻，查近 N 条价格时序，返回 current/min/max/均值/极差，供智能体「这个价为什么这么改」的深度问答。

用法（本地 / 智能体 subprocess 均可）：
```bash
py _skill_fix/check-price-anomalies/price_analyst.py --mode analyze
py _skill_fix/check-price-anomalies/price_analyst.py --mode down --platform 京东 --sku <id> --limit 30
```

**记忆学习（阶段 2，确定性经验库）**：运营对某 SKU 处置过、且经人工确认后，把结论回写为经验，供后续 analyze/down 自动标注、降噪话术：

```bash
py _skill_fix/check-price-anomalies/price_analyst.py --mode learn --platform 京东 --sku <id> --label 常态调价 --note "每周五档期下调"
py _skill_fix/check-price-anomalies/price_analyst.py --mode memory   # 列出全部经验
```

- `--label` 仅允许 `常态调价` / `属实跌破`（防止噪声污染）；缺 platform/sku 时拒绝写入；
- 命中经验的异动会在输出 `items` 追加 `memory_label` / `memory_note`，并在 `ai_material` 里提示"历史判定为…"，供智能体汇报时引用——**纯标注，绝不自动改价**；
- 经验库落 `PRICE_STATE_DIR/price_memory.json`（默认 `~/.staffdeck_price_audit`），跨 run 持久、幂等累加。
- 清理脏经验：`--mode learn --label 属实跌破` 仅用于确认跌破；误标需运营编辑 `price_memory.json` 或删除对应键后再写入。

判断层新增配置：`PRICE_LOW_CONF_WINDOW`（跌破且幅度小于该绝对额视为小幅/低置信并入摘要，默认 `0.5`）。

## 原生定时任务（推荐长期巡检方式）

不要每次都用 LLM 现读本技能再执行。用 StaffDeck「自动任务」承载，prompt 极窄化、全程确定性：

> 运行通用技能 `check-price-anomalies` 包内脚本（`--mode audit`），解析其 stdout JSON。若 `status=success`，用 `total_anomalies_found` / `total_records_checked` / `current_promotion_stage` 做一句话汇总；不得二次改价、不得改动飞书基准表、不得重复推送。运行结果即本轮巡检溯源头。

平台会为每次 run 自动落 `ScheduledTaskRun.trace` + AgentEvent 流、调度/重试/忽略策略全自理；`run.py` 自身的数据库查询、价盘解析、去重均确定性执行。

## 配置项（环境变量）

| 变量 | 含义 | 默认 |
|---|---|---|
| `PRICE_DB_HOST/PORT/USER/PASSWORD` | 业务库连接 | 见 run.py |
| `FEISHU_APP_TOKEN/FEISHU_TABLE_ID/FEISHU_APP_ID/FEISHU_APP_SECRET` | 飞书基准表 | 见 run.py |
| `FEISHU_ALERT_WEBHOOK` | 群机器人 webhook（留空跳过推送） | 见 run.py |
| `PRICE_STATE_DIR` | 去重/缓存状态持久化目录（必须稳定绝对路径，跨 run 保留去重） | `~/.staffdeck_price_audit` |
| `PRICE_FLUCT_TOLERANCE` | 「同一天普惠价波动」触发阈值（元） | `0.5` |

> Harness 工作区每次 run 全新分配；去重状态若写相对路径会在跨 run 丢失导致重复推送，故一律落入 `PRICE_STATE_DIR`。

## 部署为系统定时任务（推荐，脱离 LLM 执行层）

不要用 agent 当调度器。用 `price_audit_launcher.py`（本包内）挂系统定时器，确定性执行并负责：跑 run.py → 判定成败 → 失败时向群推一张运维告警卡 → 日志滚动落盘。环境变量沿用上面表格，另加 `PRICE_LOG_DIR`（默认 `<包>/logs`）、`PRICE_LOG_KEEP`（保留天数，默认 30）。

- **Windows（任务计划程序，PowerShell 管理员）**：
  ```powershell
  $p = "C:\path\to\check-price-anomalies"
  schtasks /Create /TN "StaffDeckPriceAudit" /TR "py $p\price_audit_launcher.py" /SC DAILY /ST 09:00 /RU SYSTEM /F
  ```
- **Linux（cron）**：`0 9 * * * PYTHONPATH=... py .../price_audit_launcher.py >> .../logs/cron.out 2>&1`

失败时 launcher 返回非零，可在任务计划里再配「任务失败后发邮件/通知」做第二层告警。

## 推送行为

- 若巡检**发现异常**（`total_anomalies_found > 0`），脚本会**按平台分类**向飞书群各推送一张独立告警卡片：京东/抖音/拼多多/唯品会各有异动时，每个平台一张卡片，卡片只列本平台异动；**无异常的平台不推送**；**全部无异常时不推送**、不打扰群。
- 群 webhook 地址通过环境变量 `FEISHU_ALERT_WEBHOOK` 注入（目标群 → 设置 → 群机器人 → 添加自定义机器人后复制 webhook 地址），未配置时脚本会跳过推送、巡检结果照常返回。
- **告警过滤**：仅当数据库实际价格**低于（跌破）**飞书维护普惠价时才告警推送；数据库价格**高于**飞书维护价属正常，不告警、不推送。
- **不重复推送**：每条异动（平台+商品+异常类型）只有当它是**新增**或**现价比上次推送时发生了变化**才会再次推送；同一异动状态未变不会重复发卡片。异动解决后自动从告警状态移除，若复发视为新异动再次推送。

## 各平台匹配逻辑

普惠价计算与飞书基准匹配字段（各取其对应字段，格式为「数据库 → 飞书商品表字段」）：

| 平台 | 普惠价计算 | 数据库匹配键 | 飞书商品表匹配字段 |
|---|---|---|---|
| 京东 | `京东价 - 官方直降` | `商品ID` | `京东商品链接`（若存完整URL会自动提取其中商品ID） |
| 抖音 | `origin_price - money_off_campaign` | `product_id` | `抖音SKUID` |
| 拼多多 | `page_price` | `goods_id` | 暂无业务，不参与飞书基准匹配 |
| 唯品会 | `页面价` | `mid` | `唯品mid` |

当数据库匹配键与飞书字段值相等时，才将飞书维护价作为该商品基准，用于「跌破维护普惠价」判定；拼多多仅参与「同一天普惠价波动」判定，不做基准比对。

## 结果呈现规范

取 stdout JSON 中的 `anomalies` 数组，以 Markdown 表格呈现异动清单（平台、商品ID、当前价、基准价/日内极差、风险级别、异常类型）。表格底部必须附合规提示：

```
⚠️【提示】：本数据仅供参考，系统无自动改价权限，请责任运营人工核实后处理。
```

若 `total_anomalies_found` 为 0，明确告知用户"本次巡检未发现异动"，不要凭空编造。

## 严禁
- 严禁自动修改商品后台价格、严禁自动下架、严禁向商家发送任何处置指令；
- 回答后立即结束本轮任务。