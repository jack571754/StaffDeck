---
name: fixed-etl-notify
description: 固定流程巡检与播报通用模板：自动调用查询模板取数、内存清洗比对、SQLite状态指纹去重，并向飞书群推送格式化卡片。
version: 1.0.0
entrypoint: run.py
---

# 固定流程巡检与播报技能 (Fixed ETL & Feishu Notify)

本技能为 StaffDeck 推荐的确定性任务闭环脚手架：
1. **取数 (Fetch)**：通过 HTTP 调用 StaffDeck 内部数据查询模板 (`/api/mock/data-query/{template_id}`)，支持时间宏（如 `@today`）；
2. **处理 (Process)**：在 Python 内存中高效计算，完成异常判定或汇总统计，零 Token 消耗，不发生大结果集截断；
3. **去重 (Deduplicate)**：内置轻量级 SQLite 指纹库，对已推送记录进行防重标记，防止高频定时任务重复打扰；
4. **推送 (Notify)**：装配飞书交互式卡片 2.0，推送到指定的飞书群机器人 Webhook；
5. **状态对齐 (Alignment)**：输出标准 JSON 结果并对齐进程退出码，平台调度器根据 `push_status` 判定真实业务成败。

## 运行参数

- `--mode`: 运行模式，可选 `audit`（巡检模式）或 `digest`（汇总播报模式），默认 `audit`。
- `--dry-run`: 演练模式，仅计算并输出结果，不真正调用飞书 Webhook 推送。

## 环境变量支持

- `STAFFDECK_BASE_URL`: StaffDeck 服务基础地址，默认 `http://127.0.0.1:5173`。
- `DATA_QUERY_TEMPLATE_ID`: 数据查询模板 ID（如 `qt_50f463a815af4801`）。
- `FEISHU_ALERT_WEBHOOK`: 飞书机器人 Webhook URL。
