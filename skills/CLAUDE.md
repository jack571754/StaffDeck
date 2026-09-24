[根目录](../CLAUDE.md) > **skills**

# skills — Agent 技能包

## 模块职责

面向外部 Agent（/复用）与数字员工的技能定义与模板：以 `SKILL.md` 为核心，附 `agents/openai.yaml` 与 `references/*.md`。

## 内容结构

| 技能包 | 说明 |
|---|---|
| `staffdeck-api-auth/` | StaffDeck 开放 API 鉴权引导 |
| `staffdeck-api-manage-resources/` | 资源管理 API 技能（references/resources-api.md）|
| `staffdeck-api-manage-sops/` | SOP 管理 API 技能（references/sop-api.md）|
| `staffdeck-api-run-agent/` | 运行数字员工 API 技能（references/run-api.md）|
| `fixed-etl-scheduled-task/` | 固定 ETL 定时任务技能（`SKILL.md`）|
| `templates/fixed-etl-notify/` | 固定流程模板：`run.py`（约 712 行，取数-比对-推送，含去重 `dedup_history.sqlite`）+ `config.json` + `SKILL.md`；当前分支有改动。**广播卡已改为共用后端渲染器**：`run.py` 动态 import `app.scheduled_tasks.renderers.sales_card.build_sales_feishu_card`（`run.py:52-64` / `:447-454`，消除与 `pipeline.py` 的双份实现漂移）；仍保留自有 audit 模式通用卡（`run.py:456-556`）与**本机绝对路径兜底**（`d:\project\...\backend\.env`）|

## 运行与开发

后台脚本在数据库（非本仓库）运行时，仓库仅承载技能定义与模板源码。依赖：`skills/templates/*` 由数字员工按需读取并经 `run_skill_script` 工具执行。`run.py` 通过 `sys.path` 注入 `backend/` 后复用后端卡片渲染器（失败时降级为自带卡片逻辑）。

## 相关文件清单

各子目录 `SKILL.md`、`agents/openai.yaml`、`references/*.md`；`templates/fixed-etl-notify/{run.py,config.json,SKILL.md}`；跨模块引用：`backend/app/scheduled_tasks/renderers/sales_card.py`。

## 变更记录 (Changelog)

- 2026-09-24T16:45 — 增量更新（聚焦定时任务 pipeline 通道）：标注 `templates/fixed-etl-notify/run.py` 已改为动态复用后端 `renderers/sales_card.build_sales_feishu_card`（提交 `042333ee`，双份实现漂移消除）；补 audit 模式自有卡片与本机绝对路径兜底说明。
- 2026-09-23T18:06 — 初始化架构师首次生成；登记技能包清单与 fixed-etl-notify 模板。
- （先前）`fixed-etl-scheduled-task` 模板已由提交 29d5f97 引入。
