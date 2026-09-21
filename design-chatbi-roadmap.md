# StaffDeck ChatBI 双场景演进路线图

> 目标场景：**① 例行/触发式播报**（数据查询 → 逻辑处理 → 飞书卡片推送）与 **② 交互式/自助式查数**（群聊/私聊自然语言提问 → 意图识别 → 动态查询 → 归因分析 → 图表输出）。
> 选型结论：**两阶段推进**——阶段一零改码跑通（本周），阶段二按 2b → 2a → 2c 顺序做三个独立源码增量（第 2-4 周）。播报与查数并重，意图路由（2b）加大投入。
> 关联文档：`design-sales-broadcast-optimization.md`（播报单任务优化，其方案 A+B' 即本路线阶段一的输入）。

---

## 一、现状瓶颈诊断

| 瓶颈 | 根因（已核实） | 影响场景 |
|---|---|---|
| 响应耗时 | Harness v2 每能力调用计 1 动作、完整 Agent 循环；本租户单轮仅 10 动作 | 播报 ~10 动作/30s+；查数多轮交互更慢 |
| 大结果连锁 | 外部工具结果 >2000 字符即落盘 `.harness/tool-results/`（`harness_capability_invoker.py:74`），模型被迫 `read_file` 补救，进而萌生写脚本动机 | 播报（82 条明细 33KB 触发） |
| 运算准确率 | 聚合下放给模型（写脚本/心算），有幻觉风险 | 播报合计行、查数统计 |
| NL 灵活性 | data_query 为「模板+参数」模式，自然语言必须映射到预定义模板参数 | 查数 |
| 规模化成本 | 每次播报/查数都是完整 LLM 多轮循环，token 成本与并发压力线性增长 | 两者 |

**核心思路：把「确定的部分」从 LLM 手里拿回来（SQL 聚合、模板渲染、日期解析、固定推送），LLM 只负责真正需要语言能力的部分（意图理解、文案润色）。**

---

## 二、阶段一：零改码跑通（第 1 周）

| # | 步骤 | 产出 / 验收 |
|---|---|---|
| P1-1 | 数据查询中心新建预聚合模板 `query_realtime_sales_summary`（SQL 见 design-sales-broadcast-optimization.md 步骤1：GROUP BY + UNION ALL 总计行，无分号） | 返回 <1KB，不触发落盘 |
| P1-2 | 定时任务 Prompt 替换为 B' 版（锁死 3 次能力调用：query → general_skill read → run_skill_script --text） | 播报单轮 3-4 动作完成 |
| P1-3 | 整理「标准问法 → 模板」Top 20 清单（业务域），写入员工 persona / 知识库 | 交互查数有引导与兜底 |
| P1-4 | HTML 图表报告模板：查询结果 → publish_artifact → artifact share 沙箱预览短链发群 | 群内可点开交互图表（链接形态） |
| P1-5 | 采集基线：用 traces / `HarnessInvocationRecord` 统计播报动作数与端到端耗时 | 基线指标入库，作为阶段二验收对照 |

---

## 三、阶段二：源码增量（第 2-4 周，按 2b → 2a → 2c 顺序）

### 2b. NL → 模板意图路由（优先，查数主攻）

**目标：命中模板的查数 P50 < 5s（1 次 LLM 路由 + 1 次 SQL），LLM 不写 SQL，准确率由模板白名单保证。**

| 文件 | 内容 |
|---|---|
| `backend/app/data_query/intent_router.py`（新） | `route_question(question, tenant_id) -> RouteResult(template_slug, params, confidence) \| None`。**一次** LLM 调用完成模板选择 + 参数抽取；prompt 注入该租户可见模板清单（slug/描述/参数 schema/示例问法）；confidence 低于阈值返回 None |
| `backend/app/data_query/date_resolver.py`（新） | 纯函数相对日期解析："昨天/本周/上月/近7天" → 具体日期参数。不依赖 LLM，可充分单测 |
| `backend/app/data_query/models.py`（已核实：无 schema.py，参数结构在此） | 模板参数增强落点为 `QueryTemplate.params_json`（现为 `list[dict[str, Any]]`，`models.py:100`）：扩展参数项 schema（枚举约束、`compare_to` 环比/同比字段）；对比模板 SQL 支持 `:end_date_prev`；`QueryExecuteRequest.params`（`models.py:165`）保持兼容 |
| 挂点：`backend/app/channels/service_intake.py` 领取路径上、**进入员工 Harness 执行之前**（已核实：`service_intake.py` 负责入站领取/租约，`service_routing.py` 只负责员工选择与命令解析，二者均非执行点；拦截点取 intake 领取后、员工会话/turn 构建前） | 渠道消息先过意图路由：命中 → 直执行模板（见下）→ **一次** LLM 组装回复 → outbox；未命中 → 回退现有 Harness Agent 流程（不阻断） |
| 模板执行复用 | 已核实 `tool_executor.py:126-127,397` 有 `_execute_data_query_tool` 分发且已接入 `authorized_data_source_ids` 权限；也可直调 `data_query/service.execute_query_by_id` 更短路径 |
| 模板清单缓存 | 按租户缓存路由用模板清单，模板 CRUD 时失效 |
| 归因分析（一期简化） | 对比模板返回本期/上期两列 + 模型生成差异解读；维度下钻留待方案 3 |
| 测试 | `tests/test_data_query_intent_router.py`：命中 / 日期解析 / 低置信回退 / 租户权限隔离 |

### 2a. 确定性播报管道（播报主攻）

**目标：播报 <2s、零 LLM 依赖（可选润色除外）、动作预算不再相关。**

| 文件 | 内容 |
|---|---|
| `backend/app/db/models.py` + `backend/app/scheduled_tasks/schema.py`（已核实：ScheduledTask 表模型在 db/models.py，schema.py 仅为 Pydantic 层；现无 execution_mode/pipeline 类字段，无设计冲突） | `ScheduledTask` 增加 `execution_mode: "agent" \| "pipeline"`（默认 agent，完全向后兼容） |
| `backend/app/scheduled_tasks/pipeline_runner.py`（新）；执行分发挂 `scheduled_tasks/service.py`（已核实：`worker.py` 仅为薄轮询壳 `run_worker()`，真正任务执行逻辑在 service.py） | 顺序执行 step 列表，**不进 Harness Agent 循环**：`query`（直调 `data_query/service.execute_query_by_id`，权限沿用 `authorized_data_source_ids`）→ `render`（字符串模板渲染 Markdown 正文，支持从上游 step 结果取值）→ `notify`（webhook 直推或渠道 outbox）。幂等重跑；失败写 `ScheduledTaskRun` 失败态 + 渠道告警消息 |
| 前端 `ScheduledTaskEditorPage.tsx` | pipeline 模式编辑器（step 配置：工具/模板/推送三型） |
| 并发设计 | ≤100 任务/日量级：worker 按租户串行即可，无需引入队列；>500/日 再评估 |
| 测试 | `tests/test_scheduled_pipeline.py`：全链路 / 失败告警 / 幂等重跑 |

### 2c. 图表输出通道

| 项 | 内容 | 改码量 |
|---|---|---|
| `_skill_fix/chart-render/`（新通用技能包） | run.py：输入数据 JSON → matplotlib 渲染 PNG → 产物目录（凭证零依赖，纯本地） | 免改码 |
| `backend/app/channels/adapters/feishu.py` | 出站补 image 消息类型发送。已核实：`send()` 现仅支持 `interactive`/`post`/`text` 三类（`feishu.py:422,563-569`），无 image 出站分支，需新增；`channels/media.py` 媒体桥与附件下载已就绪 | 小改 |
| artifact share HTML 交互图 | 复用现有沙箱预览短链（阶段一 P1-4 已验证），作为 PNG 之外的富选项 | 零新增 |

---

## 四、里程碑验收

| 里程碑 | 时间点 | 验收标准 |
|---|---|---|
| M1 | 第 1 周末 | 播报 3-4 动作单轮完成；基线动作数/耗时入库；标准问法清单上线 |
| M2 | 2b 完成后 | 群聊查数命中模板 P50 < 5s；未命中回退 Harness 可用；意图路由租户隔离通过测试 |
| M3 | 2a 完成后 | 播报管道 <2s、零 LLM 调用（可选润色除外）；失败有渠道告警 |
| M4 | 2c 完成后 | 图表 PNG 可直接发群；HTML 交互图短链并存 |

## 五、前置依赖与风险

| 项 | 说明 | 处置 |
|---|---|---|
| `pymysql` 依赖缺失 | `pyproject.toml` 未声明，新机部署 MySQL 数据源必挂（见 design-sales-broadcast-optimization.md 第六节） | **阶段二开工前必须补上** |
| 意图错配 | 路由把问题映射到错误模板/参数 | confidence 阈值 + 未命中回退 Harness；路由结果在回复中注明所用模板，便于纠错 |
| pipeline 无断点恢复 | 不走 Harness 即无 TaskFrame 状态机 | 以幂等重跑替代（query/notify 均设计为可重入） |
| 渠道文案长度 | 飞书卡片正文上限（`MAX_CARD_TEXT_LEN=15000`）与渠道 2000 字限制 | 图表用 PNG 规避长文本；超长正文走 artifact share 链接 |
| 合并基点落后 | 分支落后上游 `7adc7c8`，`tool_executor.py` 同文件改动冲突 | 阶段二动工前先 rebase/merge 上游，保留 `EXTERNAL_TASK_ALREADY_PENDING` 防重逻辑 |

## 六、远期方向（方案 3，暂不启动）

语义层/指标层 ChatBI：为数据源定义指标 DSL（维度/度量/时间粒度），NL → 指标查询编译 SQL，业务口径沉淀知识库，维度下钻归因引擎。工程量数周级，待阶段二稳定运行、问法覆盖不足成为主要矛盾时再评估。

## 七、事实核验记录

2026-09-21 对照当前工作区代码逐条复核：

| 断言 | 结果 |
|---|---|
| 工具结果落盘阈值 2000 字符（`harness_capability_invoker.py:74`） | ✅ 复核一致 |
| `pyproject.toml` 缺 `pymysql` | ✅ 仍缺失（阶段二前置项有效） |
| 分支未含上游 `EXTERNAL_TASK_ALREADY_PENDING`（`7adc7c8`） | ✅ 仍无命中，合并风险有效 |
| `channels/` 存在 `service_intake.py` / `service_routing.py` / `media.py` / `service_outbox.py` | ✅ 全部存在；但 intake=领取、routing=选员工，**执行拦截点需更精确**（已在 2b 修订） |
| `data_query/` 无 `schema.py`，参数结构在 `models.py` `params_json` | ✅ 已修订 2b 落点 |
| `tool_executor.py` 已注册 `data_query` 工具类型并接入权限 | ✅ `_execute_data_query_tool`（L126/397） |
| `scheduled_tasks/worker.py` 为薄壳，执行在 `service.py`；无 execution_mode 字段 | ✅ 已修订 2a 落点与挂点 |
| feishu 适配器出站无 image 分支（仅 interactive/post/text） | ✅ 2c 判断成立 |

## 附：方案对比备忘（构思阶段结论）

| 方案 | NL 灵活性 | 耗时 | 成本 | 结论 |
|---|---|---|---|---|
| 1 纯编排层 | 差（问法枚举） | 15-30s | 低 | 阶段一先行 |
| 2 轻量增量 | 中（路由+回退） | 查数<5s / 播报<2s | 中 | **主线** |
| 3 语义层 | 高（真自助 ChatBI） | 中 | 高（数周级） | 远期 |
