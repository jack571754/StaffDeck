---
name: feishu-webhook-notify
description: "飞书群机器人卡片与表格通知：向指定群自定义机器人 webhook 发送交互式卡片消息（支持表格与 Markdown 排版）或纯文本消息，用于定时汇报推送。"
---

# 飞书群机器人通知

向飞书群自定义机器人 webhook 发送通知消息：
- **原生飞书卡片 2.0（`msg_type=interactive`）**：
  1. **实时销售播报模式**：传入包含 `category` 和 `item` 的实时销售数据 JSON（或 `--text` 传 JSON 字符串），自动提取**数据更新时间**作为卡片标题时间（如 `📊 实时销售播报 · 09-22 12:02（当日累计）`），自动组装 **汇总分栏（今日净销/达播净销/运营端净销） + 7 列优化配色自适应明细表（核心行加粗、绿涨红跌灰平） + 今日/昨日 24 小时时段环比柱状图** 的高价值交互式大盘卡片！
  2. **原生卡片 JSON 模式**：直接传入符合飞书 Card Schema 2.0 的 JSON（支持 `column_set`、`table`、`chart` 等高级组件），自动规范打包发送，并统一将表格列宽规范为自适应。
  3. **Markdown 表格模式**：在 Markdown 正文中使用 `| 列1 | 列2 |` 语法，脚本自动将其转换为飞书原生 `tag: "table"` 表格组件，所有列宽全部自适应（width: auto）、数值智能靠右、高紧凑行高与内置分页！
- 自动提取正文首行作为卡片色彩标题栏（`header`）。

## ⚠️ 核心铁律
1. **凭证不落地**：webhook URL 只来自环境变量 `FEISHU_SALES_REPORT_WEBHOOK`；严禁写进输出、日志、提示词。
2. **只发不收**：仅发送消息，不做读取/群管理/交互回调。
3. **禁止自行编写替代脚本**：一律运行本技能包内 `run.py`。
4. **禁止用 `exec_command` 运行本技能脚本**：本技能凭证 `FEISHU_SALES_REPORT_WEBHOOK` 只经 `run_skill_script` 注入子进程；用 `exec_command` 执行会导致凭证缺失、进程以非零码退出而失败。**执行本技能只准用 `run_skill_script`**。若你发现自己在尝试用 `exec_command` 直跑本脚本，立即停下改回 `run_skill_script`。

## 执行方式

**第 1 步：读取本技能包。**
调用 `general_skill` 能力（operation=`read`），获取返回的 `entrypoint_path`。**必须用返回路径，不要虚构。**

**第 2 步：落盘正文并执行（或直传 --text）。**
将 Markdown 播报正文通过 `write_file` 写入工作区文件（例如 `report.md`）或直接通过 `--text` 传参，调用工具 `run_skill_script`：

```json
{
  "script_path": "<上一步返回的 entrypoint_path>",
  "argv": ["--text-file", "report.md"],
  "timeout_seconds": 45,
  "max_output_bytes": 65536
}
```

- 参数说明：
  - `--text`：正文字符串，正文中的 Markdown 表格将自动转换为飞书卡片 2.0 原生表格；首行自动提拔为卡片标题。
  - `--text-file`：正文文件路径（推荐用于长正文），正文中的 Markdown 表格将自动转换为飞书卡片 2.0 原生表格。
  - `--title`：（可选）自定义卡片顶部标题。
  - `--template`：（可选）卡片顶部色块颜色，可选 `blue`（默认）、`wathet`、`turquoise`、`green`、`orange`、`red`、`carmine`、`violet` 等。
  - `--page-size`：（可选）表格每页展示行数（1-10，默认 10）。
  - `--dry-run`：（可选）演练模式，仅打印卡片 JSON，不发起 HTTP 请求。
  - `--msg-type`：（可选）`interactive`（默认飞书卡片）或 `text`（降级纯文本）。
- webhook：由环境变量 `FEISHU_SALES_REPORT_WEBHOOK` 自动注入，缺失时报错。

## 结果判读（铁律：如实，勿误报成功）

- **不只信退出码**：本技能进程以非零退出码表示失败（配置缺失=2，其余失败=1）。若工具返回 `COMMAND_EXIT_NONZERO` 或 `COMMAND_TIMEOUT`，一律视为**发送失败**，必须如实上报，不得当作成功。
- **stdout 结果 JSON 规范**：
  - 成功：`{"status":"success","message":"发送成功","msg_type":"interactive","StatusCode":0}`（退出码 0）。
  - 失败：`{"status":"error","message":"..."}`（退出码非 0），如实转述给用户，**不要编造发送结果**。
- 判断准则：**退出码非 0 与 stdout 中 `status:error` 任一出现，即为失败**；两者都对才算成功。

## 严禁
- 严禁把 webhook URL 打印到 stdout/stderr/日志。
- 发送完成后如实汇报推送结果，立即结束本轮任务。
