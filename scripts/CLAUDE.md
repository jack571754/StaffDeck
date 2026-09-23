[根目录](../CLAUDE.md) > **scripts**

# scripts — 服务生命周期脚本

## 模块职责

跨平台启动/停止/状态封装（PS 与 bash 包装），统一收敛到 Python 入口 `dev.py`。

## 文件清单

| 文件 | 说明 |
|---|---|
| `dev.py` | 统一生命周期入口（`up` / `up --detach` / `status` / `down`），驱动 `dev_supervisor.py` |
| `dev_supervisor.py` | 进程监督 |
| `process_utils.py` | 进程工具 |
| `dev_up.sh` / `dev_up.ps1` | 后台/前台启动包装 |
| `dev_down.sh` / `dev_down.ps1` | 停止 |
| `dev_status.sh` / `dev_status.ps1` | 状态 |
| `generate_long_knowledge_docx.py` | 生成长文档用例 |

## 用法

macOS/Linux/WSL：`scripts/dev_up.sh --detach`；Windows：`.\scripts\dev_up.ps1 --detach`。相同操作可直接 `python scripts/dev.py up --detach`。

## 测试与质量

相关测试 `backend/tests/test_dev_scripts.py`。

## 相关文件清单

`dev.py`、`dev_supervisor.py`、`process_utils.py`、`dev_up/down/status.{sh,ps1}`。

## 变更记录 (Changelog)

- 2026-09-23T18:06 — 初始化架构师重建模块文档。