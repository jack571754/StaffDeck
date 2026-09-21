---
name: product-price-lookup
description: 秒级查询具体某一个商品在飞书多维表格中维护的普惠价/维护价，支持展示 S促普惠价、618价格、D11价格三档价盘以及当前生效的大促阶段。当用户询问特定单品的价格或维护价时调用。
---

# 单品价格与维护价速查

## 执行方式（唯一，禁止其它）

用户问**单个具体商品**的价格时，按下面两步入：

**第 1 步：读取本技能包，拿准确路径。** 调用 `general_skill` 能力（operation=`read`，query=<从用户消息提取的商品关键词>）。它会返回本技能包的 **`entrypoint_path`** 与 `file_paths`（位于 workspace 的 `.harness/skill-packages/` 下，目录名含内容摘要后缀）。**必须用返回的 `entrypoint_path`，不要手动拼路径、不要虚构**。

**第 2 步：执行。** 用工具 `run_skill_script`，参数如下（`script_path` 填第 1 步返回的 `entrypoint_path`）：

```json
{
  "script_path": "<上一步返回的 entrypoint_path>",
  "argv": ["--mode", "query", "--keyword", "<商品核心关键词，如：面膜 / 次抛精华 / 商品名或ID>"],
  "timeout_seconds": 20,
  "max_output_bytes": 65536
}
```

- 关键词只需提取**最能唯一标识商品的名词**（品牌+品名或俗名），不要整句。
- 若确无 `entrypoint_path`（非 Harness 会话），再用本包内 `run.py` 本地运行；**不要**自行编写替代脚本。

## 结果呈现规范

脚本 stdout 是合法 JSON，取 `results` 数组构造结构化卡片/表格回复用户：

1. 明确标出当前大促阶段（`current_promotion_stage`，如 S促 / 618 / D11）；
2. 列出该商品的三档价盘（`S促普惠价`、`618价格`、`D11价格`）及当前执行的维护基准价；
3. 若 `results` 为空：如实告知“当前价盘库中未收录该商品”，可建议用户换一个说法（商品昵称/规格）再试，**不要编造价格**。

## 严禁

- 严禁多轮自省探索、调用 `capability_describe`、`write_file` 或自行编写 Python 脚本；
- 严禁把 `--mode` 设为 `audit`（那是全网巡检，价格速查用不到）；
- 回答后立即结束本轮任务，不追加额外工具调用。