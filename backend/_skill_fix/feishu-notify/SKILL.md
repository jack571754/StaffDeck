---
name: feishu-notify
description: "飞书单聊文本通知：经飞书开放接口向指定用户 open_id 发送一条文本消息（tenant_access_token → im/v1/messages）。"
---

# 飞书单聊通知

向指定飞书用户发送一条文本消息。走飞书开放接口纯 HTTP：`tenant_access_token/internal` 取凭证 → `im/v1/messages?receive_id_type=open_id` 发文本。
凭证与平台内置飞书 ChannelBinding 同源（自建应用 App ID + App Secret），经参数/环境变量注入。

## ⚠️ 前置条件（缺失时本技能会明确报错，不静默降级）

1. 平台需已配置 **active 的飞书自建应用** ChannelBinding（App ID + App Secret + 长连接）；
2. 已知接收人的 **open_id**（或已建立 channel_identity 关联，可由平台反查后传入）。

## ⚠️ 核心铁律

1. **凭证不落地**：App Secret 只能来自用户显式提供或环境变量 `FEISHU_APP_ID`/`FEISHU_APP_SECRET`；严禁写进输出、日志、报告。
2. **只发不收**：本技能仅发送文本，不做任何读取/审批/群管理等其他操作。
3. **禁止自行编写替代脚本**：一律运行本技能包内 `run.py`。

## 执行方式

**第 1 步：读取本技能包。** 调用 `general_skill` 能力（operation=`read`），取返回的 `entrypoint_path`。**必须用返回路径，不要虚构。**

**第 2 步：执行。** 用工具 `run_skill_script`，长正文优先落临时文件走 `--text-file`（避免 argv 转义问题）：

```json
{
  "script_path": "<上一步返回的 entrypoint_path>",
  "argv": ["--open-id", "<接收人open_id>", "--text-file", "<正文文件路径>"],
  "timeout_seconds": 45,
  "max_output_bytes": 65536
}
```

- 凭据：`--app-id/--app-secret` 或环境变量 `FEISHU_APP_ID/FEISHU_APP_SECRET`（推荐）。
- 接收人：`--open-id` 或环境变量 `FEISHU_OPEN_ID`。
- 也可不传 `--text`，直接依赖 runner 注入的 stdin `text`/`QUERY` 字段作为正文。

## 结果 JSON 规范（stdout）

- 成功：`{"status":"success","message":"发送成功","message_id":"om_…"}`
- 失败：`{"status":"error","message":"…"}`，其中 `message` 说明具体缺什么/错在哪：
  - 缺前置 → 提示需配置飞书 Binding / open_id，**如实转述给用户，不要编造发送结果**；
  - `code=230001` → open_id 无效；`code=99991672` → 应用缺少 `im:message` 发送权限。
- 超长正文（>9000 字符）会被截断并在末尾标注，属预期行为，汇报时说明即可。

## 严禁

- 严禁在回复中复述 App Secret；
- 严禁向未经用户确认的 open_id 发送消息；
- 回答后立即结束本轮任务。
