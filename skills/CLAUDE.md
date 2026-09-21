[根目录](../CLAUDE.md) > **skills**

# skills — 仓库级 Agent 技能包

## 模块职责

供外部 AI 编码代理（如 Codex）通过 StaffDeck 开放 API v1 操纵数字员工的技能包，遵循 SKILL.md 约定（frontmatter：name/description + 步骤化说明）。另有仓库根 `skills.zip` 为打包分发产物。

## 技能包清单

| 技能包 | 说明 |
|---|---|
| `staffdeck-api-auth` | Open API v1 认证与凭证准备（`STAFFDECK_BASE_URL` / `STAFFDECK_API_KEY`） |
| `staffdeck-api-manage-resources` | 资源管理 API（references/resources-api.md） |
| `staffdeck-api-manage-sops` | SOP 管理 API（references/sop-api.md） |
| `staffdeck-api-run-agent` | 运行数字员工：创建/续接会话、有状态/无状态 run、SSE 流（`runs:stream`）、detached 运行 + 轮询、`awaiting_input` 续接、取消、Harness artifacts 下载、Idempotency-Key 约定（references/run-api.md） |

每个技能包结构：`SKILL.md` + `references/*.md` + `agents/openai.yaml`。

## 变更记录 (Changelog)

- 2026-09-11T15:42:50 — 初始化架构师首次生成。
