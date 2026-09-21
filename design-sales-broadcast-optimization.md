# 实时销售播报定时任务优化改造方案

> 范围红线：**不改动 StaffDeck 核心源码**。全部改造落在「数据查询中心配置」「定时任务 Prompt」「通用技能层（已完成）」「SOP 编排」四个免改码层面。
> 关联背景：`backend/_skill_fix/feishu-webhook-notify/` 已完成技能层优化（SKILL.md 铁律④ + run.py 非零退出码），并已通过 `sync_skill.py` 重新入库（2026-09-21）。

---

## 一、问题根因复盘（为什么一次播报要 10 个动作、多轮才完成）

| 动作序号 | 能力调用 | 结果 | 性质 |
|---|---|---|---|
| 1 | `capability_describe` | ✓ | 探索开销，零产出 |
| 2 | `query_realtime_sales` | ✓ | 干活 |
| 3-5 | `read_file` ×3 | ✓ | 大结果落盘后的补救读取 |
| 6 | `general_skill.feishu-webhook-notify` (read) | ✓ | 干活（取 entrypoint） |
| 7 | `exec_command` | ❌ `COMMAND_EXIT_NONZERO` | **走错通道的试错** |
| 8 | `write_file` | ✓ | 干活 |
| 9 | `exec_command` | ❌ `COMMAND_EXIT_NONZERO` | **走错通道的试错** |
| 10 | `exec_command` | ✓ | 干活（推送） |

四个叠加机制：

1. **动作粒度细**：harness v2 中每个能力调用（含 `capability_describe`、`read_file`）各计 1 个动作；本轮额度仅 10（`UIConfig.agent_loop_max_actions`，默认 32，本租户配低了）。
2. **大结果溢出连锁**：外部工具结果序列化后超过 `_INLINE_JSON_TOOL_RESULT_MAX_CHARS = 2000` 字符（`harness_capability_invoker.py:74`）即落盘到 `.harness/tool-results/`，模型只拿到引用 → 被迫 `read_file`（+3 动作）→ 82 条明细逼模型自己写脚本聚合 → 萌生 `exec_command` 念头。
3. **走错执行通道**：`exec_command` 不注入技能密钥 `FEISHU_SALES_REPORT_WEBHOOK`（仅 `run_skill_script` 经 `skill_env.py` 白名单注入），`run.py` 缺凭证即非零退出 → 两次失败白吃 2 个动作。
4. **预算打空即排队**：`action_budget` 状态把 TaskFrame 写回 `queued`，等下一条消息续跑 → 表现为"多轮"。

**结论：任务本身不复杂（查数 → 组装 → 推送），复杂度全部来自「明细数据过大」与「模型自由度过大」。**

---

## 二、深度分析报告的事实核对

对所附分析报告的关键断言逐条核验（2026-09-21，基于当前工作区）：

| 报告断言 | 核验结果 | 说明 |
|---|---|---|
| 问题3：`pymysql` 未在 `pyproject.toml` 声明 | ✅ **属实** | `dependencies` 列表中无 `pymysql`，新机 `pip install -e "backend[dev]"` 后创建 MySQL 数据源必抛 `ModuleNotFoundError`。**属源码层，本次不动，列入后续清单。** |
| 问题4：`_PARAM_PATTERN` 会把 `'%status:pending%'` 误判为入参 | ⚠️ **例子错误，机制缺陷存在** | 正则含 `(?<!\w)` 前瞻保护：`:pending` 前一个字符是 `s`（word char），**会被正确跳过**（源码注释 `mysql_connector.py:28-34` 也写明保护 `'%10:30%'` 这类场景）。真正会误伤的是**冒号前是空格/行首的字符串字面量**，如 `note = 'see :below'`。属源码层，本次不动。 |
| 问题1：分支"移除"了上游 `EXTERNAL_TASK_ALREADY_PENDING` | ⚠️ **定性不准，风险真实** | 该逻辑由上游 `7adc7c8` 引入；当前分支基于旧基点 `c6b7cfc`，是**未包含**而非"移除"。但当前分支确实改了 `tool_executor.py`，合并上游时同文件冲突需手工保留该防重逻辑。 |
| 问题2：i18n 检查 11 处缺失 | ⚠️ 未复跑验证 | 报告给出了具体文件与 key（interval 相关文案），可信度较高；属前端源码层，本次不动，列入后续清单。 |
| 问题5：`data_query_grant_all` 全量授权面 | ✅ 机制属实 | `authorization.py` 中 grant-all = 全部数据源 − 显式 inactive 绑定。属产品设计取舍，敏感数据源建议保持白名单模式。 |
| 问题6：大结果触发截断与执行挂起 | ✅ **属实且比报告更严重** | 阈值不是 32KB 而是 **2000 字符**（`harness_capability_invoker.py:74`），82 条明细（约 33KB）必然落盘 + 触发补救读取链。 |
| 方案B：用 `{"operation":"execute","arguments":["--text",...]}` 调技能 | ❌ **错误，不可采纳** | harness v2 的 `general_skill` 能力**只允许 `operation=read`**（`harness_capability_invoker.py:827-837`，传 `execute` 会被安全降级为 read）。执行必须走 `run_skill_script`。方案 B 需按下文「方案 B'」修正。 |
| 方案B："上一轮已注入 --text 直传能力" | ✅ 属实 | `run.py:118` 自始支持 `--text`；本轮又补了非零退出码与 SKILL.md 铁律④。 |

---

## 三、三个方案评估结论

| 方案 | 结论 | 理由 |
|---|---|---|
| **A. 预聚合查询模板** | ⭐⭐⭐⭐⭐ **立即执行（核心解）** | 把聚合下推到数据库，返回行数从 82 → 个位数、体积从 ~33KB → <1KB，**不触发 2000 字符落盘**，砍掉 `read_file`×3 与写脚本动机。数字全部来自 SQL，零幻觉。 |
| **B. 硬化 Prompt** | ⭐⭐⭐⭐ **采纳但必须修正**（→ 方案 B'） | 原方案 B 的 `operation=execute` 在 harness v2 行不通；修正为 `general_skill read` → `run_skill_script --text`，可省掉 `write_file` 动作且不落盘。 |
| **C. 固化 SOP** | ⭐⭐⭐ **可选，纠正预期** | SOP 能收敛执行路径，但仍运行在 Harness 之上、仍计动作预算，"零 Token / 100% 成功率"是夸大。定位为 A+B 落地稳定后的固化手段，而非提速手段。 |

**推荐组合：A + B' 立即落地；C 待 A+B' 稳定运行 1-2 周后再评估是否固化。**

---

## 四、改造步骤

### 步骤 1：数据查询中心新建预聚合模板（方案 A）

在 企业工作台 → 数据查询中心 新建查询模板：

- 模板名：`query_realtime_sales_summary`
- 数据源：与现 `query_realtime_sales` 相同
- 参数：`end_date`（同现有定义）
- SQL（**表名/字段名按实际数据源 schema 调整，以下为模板示意；结尾不要带分号**，只读白名单按单条 SELECT 匹配）：

```sql
SELECT department, platform,
       ROUND(SUM(live_net_sales), 2)   AS live_net_sales,
       ROUND(SUM(ops_net_sales), 2)    AS ops_net_sales,
       ROUND(SUM(total_net_sales), 2)  AS total_net_sales,
       ROUND(SUM(gmv), 2)              AS gmv,
       ROUND(SUM(refund_amount), 2)    AS refund_amount
FROM sales_realtime_data
WHERE date_key = :end_date
GROUP BY department, platform
UNION ALL
SELECT 'TOTAL', 'TOTAL',
       ROUND(SUM(live_net_sales), 2), ROUND(SUM(ops_net_sales), 2),
       ROUND(SUM(total_net_sales), 2), ROUND(SUM(gmv), 2), ROUND(SUM(refund_amount), 2)
FROM sales_realtime_data
WHERE date_key = :end_date
```

设计要点：
- `UNION ALL` 总计行（`TOTAL/TOTAL`）由数据库算好，模型只做"搬运"，满足「全部数字必须来自工具返回」。
- 避免使用 `WITH ROLLUP`：ROLLUP 汇总行以 NULL 区分、且 `GROUPING()` 需 MySQL 8+，兼容性差；`UNION ALL` 各版本通用。
- 返回体积 <1KB，低于 2000 字符落盘阈值，模型单次拿到全量结果。

### 步骤 2：修正后的定时任务 Prompt（方案 B'）

替换定时任务的执行指令为：

```markdown
你是实时销售播报员工。严格按以下 4 步执行，全程只允许 3 次能力调用，不做任何其他动作：

1. 调用数据查询工具 query_realtime_sales_summary，arguments:
   {"params": {"end_date": "<当日日期 YYYY-MM-DD>"}}

2. 基于返回结果组装播报正文（Markdown，内存中完成，不落盘）：
   - 首行（将自动成为卡片标题，≤64字符）：📊 实时销售播报 · MM-DD HH:mm（当日累计）
   - 一句话总结：总净销、达播/运营拆分、成交、退款（数字一律取自 TOTAL 行，不得自行计算）
   - 表格：| 部门 | 平台 | 达播净销 | 运营净销 | 合计净销 | 成交金额 | 退款金额 |
     逐行誊抄返回数据；TOTAL 行改写为「全店合计」。严禁编造或心算任何数字。

3. 调用通用技能推送（严格两步，禁止 exec_command）：
   a. 调用能力 general_skill.feishu-webhook-notify，operation=read，取返回的 entrypoint_path；
   b. 调用工具 run_skill_script：
      {"script_path": "<entrypoint_path>", "argv": ["--text", "<第2步组装的完整Markdown正文>"],
       "timeout_seconds": 45, "max_output_bytes": 65536}
   注意：凭证由 run_skill_script 自动注入，严禁用 exec_command 运行技能脚本（会因缺凭证失败）。

4. 按 run_skill_script 返回如实汇报：退出码非 0 或 stdout 中 status=error 即为推送失败，
   原样转述错误信息；成功则回复"推送成功"。汇报后立即结束，严禁写库、追问或创建其他任务。
```

相对原方案 B 的修正点（重要）：
- ❌ `{"operation": "execute", "arguments": [...]}` → ✅ `general_skill read` + `run_skill_script`（harness v2 唯一可行路径，且与 SKILL.md 铁律④一致）。
- ✅ 用 `--text` 直传正文，省掉 `write_file`（少 1 个动作）；argv 由 harness 可信组装、不经 shell，换行/中文安全。
- ✅ 若未来正文增长超过数 KB，再退回 `write_file` + `--text-file`（SKILL.md 中已有该路径）。

### 步骤 3（可选，延后）：SOP 固化（方案 C）

待 A+B' 稳定运行后，在 SOP 管理中创建两节点 SOP：
1. 节点「提取汇总」：绑定 `query_realtime_sales_summary`，槽位注水 `end_date`。
2. 节点「卡片推送」：绑定 `feishu-webhook-notify`，槽位承接上节点汇总正文。

预期收益：执行路径确定化、便于审计与版本回滚。**注意：SOP 仍走 Harness、仍计动作预算，不会"零 Token"，成功率取决于数据源与 webhook 可用性，并非 100%。**

---

## 五、预期效果量化

| 维度 | 改造前 | 改造后（A+B'） |
|---|---|---|
| 能力调用数 | 10（含 2 次失败） | **3-4**（query → read → run_skill_script → finish） |
| 工具结果体积 | ~33KB，落盘 + read_file×3 | <1KB，单次内联返回 |
| 聚合责任 | 模型写脚本（易失败/易幻觉） | 数据库 SQL（精确） |
| 技能执行通道 | exec_command 试错 ×2 | run_skill_script 一次到位 |
| 单轮预算占用 | 10/10 打空 → 排队多轮 | 3-4/10，单轮完成 |
| 失败可见性 | 退出码 0 掩盖失败 | 非零退出码 + status:error 双信号（已上线） |

---

## 六、本次不动、列入后续清单的源码层问题

以下问题真实存在，但按红线本次不改，建议单开分支处理：

1. **`pymysql` 依赖声明**：`backend/pyproject.toml` dependencies 增加 `pymysql`。
2. **参数正则字符串字面量误伤**：`mysql_connector.py:35`，可改为先剥离字符串字面量再匹配，或在 `_convert_params` 校验 param_names ⊆ 声明参数集时对未声明参数保留原文。
3. **合并基点落后**：rebase/merge `origin/main`（`7adc7c8`），合并 `tool_executor.py` 时务必保留上游 `EXTERNAL_TASK_ALREADY_PENDING` 防重与 `IntegrityError` 处理。
4. **i18n 11 处缺失**：`chatHelpers.tsx`、`ScheduledDraftCard.tsx`、`ScheduledTaskEditorPage.tsx` 等 interval 相关文案补 `en.json`（本地化 zh 缺失项同样处理），过 `npm run i18n:check`。
5. **`data_query_grant_all` 默认值**：存敏感数据源的租户建议保持白名单模式；产品层面可考虑 grant-all 开启时增加二次确认提示。
6. **单轮动作额度**：本租户 `agent_loop_max_actions` 为 10，建议调回默认 32（UIConfig 配置，非源码；可作为运营配置项随时调整）。
