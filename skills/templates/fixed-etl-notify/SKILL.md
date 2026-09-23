---
name: fixed-etl-notify
description: 固定流程巡检与播报通用模板：自动调用查询模板取数、内存清洗比对、SQLite状态指纹去重，并向飞书群推送格式化卡片。支持同一员工多任务独立路由、多数字员工路由与精准@关注人。
version: 1.2.0
entrypoint: run.py
---

# 固定流程巡检与播报技能 (Fixed ETL & Feishu Notify)

本技能为 StaffDeck 推荐的确定性任务闭环脚手架：
1. **取数 (Fetch)**：通过 HTTP 调用 StaffDeck 内部数据查询模板 (`/api/mock/data-query/{template_id}`)，支持时间宏（如 `@today`）；
2. **处理 (Process)**：在 Python 内存中高效计算，完成异常判定或汇总统计，零 Token 消耗，不发生大结果集截断；
3. **去重 (Deduplicate)**：内置轻量级 SQLite 指纹库，对已推送记录进行防重标记，防止高频定时任务重复打扰；
4. **推送 (Notify)**：装配飞书交互式卡片 2.0，推送到指定的飞书群机器人 Webhook，并精准 @特定业务负责人；
5. **状态对齐 (Alignment)**：输出标准 JSON 结果并对齐进程退出码，平台调度器根据 `push_status` 判定真实业务成败。

## 多任务与多员工路由机制 (Task & Agent Routing & Mentions)

针对**“同一数字员工下不同定时任务通知群体不同”**以及**“不同数字员工通知不同群”**的真实业务诉求，支持以下 3 层灵活配置：

### 1. 任务级专属路由（最高优先级，推荐用于单员工挂多任务场景）

在技能目录下的 `config.json` 中配置 `task_routes`，按**任务标题（支持模糊匹配）**或**任务ID**绑定目标群与负责人：
```json
{
  "task_routes": {
    "自营价格巡检": {
      "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/群1-自营旗舰店群",
      "notify_users": [{"id": "ou_ziying", "name": "自营运营负责人"}],
      "mention_all": false,
      "_comment": "同一员工任务1：推送到自营群并@自营负责人"
    },
    "分销商破价监控": {
      "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/群2-分销渠道群",
      "notify_users": [{"id": "ou_fenxiao", "name": "渠道经理"}],
      "mention_all": true,
      "_comment": "同一员工任务2：推送到分销群并@所有人"
    }
  }
}
```

### 2. 任务 Prompt / CLI 运行时显式传参（零配置，天然独立）

在定时任务创建时，每个任务的 Prompt 中直接在 `argv` 携带该任务专用的目标群和通知人：
```json
{
  "name": "run_skill_script",
  "arguments": {
    "script_path": "<run.py>",
    "argv": [
      "--mode", "audit",
      "--task-title", "自营价格巡检",
      "--webhook-url", "https://open.feishu.cn/open-apis/bot/v2/hook/专属群Hook",
      "--notify-users", "ou_xxxx:张三,ou_yyyy:李四"
    ]
  }
}
```

### 3. 员工级与全局环境变量兜底
- `agent_routes`: 当任务未单独指定时，自动回退到该数字员工绑定的默认群；
- `backend/.env`: 全局兜底变量 `FEISHU_SALES_REPORT_WEBHOOK` / `FEISHU_ALERT_WEBHOOK`。

## 飞书卡片 @人员 (Mention) 规范
- 支持单个或多个飞书 open_id：`ou_xxxx`（飞书群内弹窗并精准@）；
- 支持公司企业邮箱：`user@company.com`；
- 支持 `@所有人`：传入 `--mention-all` 或在配置中开启 `"mention_all": true`；
- 飞书卡片将原生渲染高亮标签：`🔔 通知负责人：<at id="ou_xxxx">张三</at>`。

## 运行参数

- `--mode`: 运行模式，可选 `audit`（巡检模式）或 `digest`（汇总播报模式），默认 `audit`。
- `--template-id`: 指定数据查询模板 ID（默认为 `qt_50f463a815af4801`）。
- `--task-id`: 当前定时任务 ID（精准匹配专属群与关注人；同时让服务端据此解析该任务所选飞书应用）。
- `--task-title`: 当前定时任务标题（模糊匹配任务专属群）。
- `--binding-id`: 指定推送使用的飞书应用（`ChannelBinding.id`）。**定时任务通常无需传入**：服务端会按 `--task-id` 读取任务配置里所选的应用，本参数仅用于覆盖或做一致性断言。环境变量 `FEISHU_NOTIFY_BINDING_ID` 可作默认值。
- `--agent-id`: 当前执行的数字员工 ID（或读取环境变量 `STAFFDECK_AGENT_ID`）。
- `--webhook-url`: 显式覆盖飞书通知 Webhook 地址。
- `--notify-users`: 需要在群内 @提醒的人员列表（逗号分隔，如 `ou_xxx:张三,ou_yyy:李四` 或 `ou_xxx`）。
- `--mention-all`: 是否在飞书群中 @所有人。
- `--dry-run`: 演练模式，仅计算并输出结果，不真正调用飞书 Webhook 推送。
