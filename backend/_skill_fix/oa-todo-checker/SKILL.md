---
name: oa-todo-checker
description: "致远 OA 待办审批巡检：纯 HTTP 登录致远 OA（复刻 CryptoJS.DES 客户端加密），抓取当前待办审批列表并输出 JSON。"
---

# 致远 OA 待办审批巡检

登录致远 OA（默认 https://oa.xajuzi.com），抓取当前用户的待办审批列表，输出结构化 JSON。
登录为纯 HTTP 实现：复刻前端 `CryptoJS.DES.encrypt(pwd, _SecuritySeed)` 客户端加密（EVP_BytesToKey + DES-CBC），无需浏览器、无验证码。

## ⚠️ 核心铁律

1. **凭据不落地**：账号密码只能来自用户显式提供或环境变量 `OA_USERNAME`/`OA_PASSWORD`；严禁把明文密码写进任何输出、日志、报告或会话回复。
2. **只读不写**：本技能仅登录并读取待办列表，严禁对 OA 做任何审批、提交、删除等写操作。
3. **禁止自行编写替代脚本**：一律通过内置工具运行本技能包内的 `run.py`，不要用 code runner / write_file 另起炉灶。

## 执行方式

**第 1 步：读取本技能包。** 调用 `general_skill` 能力（operation=`read`），用返回的 `entrypoint_path`（workspace 的 `.harness/skill-packages/` 下）。**必须用返回路径，不要虚构。**

**第 2 步：执行。** 用工具 `run_skill_script`：

```json
{
  "script_path": "<上一步返回的 entrypoint_path>",
  "argv": [],
  "timeout_seconds": 90,
  "max_output_bytes": 262144
}
```

- 凭据经环境变量 `OA_USERNAME` / `OA_PASSWORD` 注入（亦可 `argv`: `--username <u> --password <p>`，仅限用户当场明示时使用）。
- `--base` 可覆盖 OA 地址（默认 `https://oa.xajuzi.com`，或环境变量 `OA_BASE`）。
- 若返回 `status=error` 且 `message` 提到缺少凭据，向用户说明需先配置环境变量，**不要**尝试猜测或代填凭据。

## 结果 JSON 规范（stdout）

```json
{
  "status": "success",
  "login": "登录成功(redirect 302)",
  "pending_count": 3,
  "todo_source": "命中 JSON 待办接口: /seeyon/rest/todo/list",
  "items": [{"title": "…", "url": "…", "time": "…", "raw": "…"}]
}
```

- `status=success`：用 `pending_count` 做一句话汇总，`items` 以 Markdown 表格呈现（标题、时间）；**不得编造**未在 `items` 中出现的待办。
- `status=error`：原样转述 `message`/`login` 字段，不得推测原因以外的结论。
- 若 `pending_count=0` 且 `todo_source` 说明"暂未定位到待办数据源"，如实告知用户数据源探测未命中，不要声称"无待办"。

## 严禁

- 严禁在回复中复述或回显明文密码；
- 严禁把待办数据发往 OA/飞书之外的第三方地址；
- 回答后立即结束本轮任务。
