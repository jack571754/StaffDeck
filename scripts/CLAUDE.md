[根目录](../CLAUDE.md) > **scripts**

# scripts — 跨平台开发生命周期

## 模块职责

统一管理 StaffDeck 的开发环境拉起、状态查看与停止；跨平台（Windows PowerShell + macOS/Linux bash），核心是 Python supervisor 模型。

## 入口与启动

| 文件 | 说明 |
|---|---|
| `dev.py` | 跨平台生命周期命令入口；supervisor 管理服务：`supervisor`、`app`、`backend`、`enterprise`、`chat`；运行目录 `.dev/`（pid 文件、`app.port`、日志）；默认端口区间 5173-5199；自动检测前端/后端依赖是否就绪、自动准备沙箱运行时 |
| `dev_supervisor.py` | supervisor 进程实现（端口分配、进程拉起、日志轮转、健康检测） |
| `process_utils.py` | PID 存活检测等进程工具 |
| `dev_up.ps1` / `dev_up.sh` | 启动（构建前端 + 启动单端口应用；`--detach` 后台；`PUBLIC_APP_ORIGIN` 可加公共隧道源） |
| `dev_down.ps1` / `dev_down.sh` | 停止 |
| `dev_status.ps1` / `dev_status.sh` | 查看状态 |
| `dev.ps1` | Windows 下直接调用 dev.py 的便捷包装 |
| `generate_long_knowledge_docx.py` | 生成长文档测试数据（知识库压测） |

```bash
scripts/dev_up.sh --detach   # Windows: scripts\dev_up.ps1 -Detach
scripts/dev_status.sh
scripts/dev_down.sh
```

## 对外接口

无（内部工具）。`dev.py` 同时被后端测试覆盖（`backend/tests/test_dev_scripts.py`、`test_desktop_launcher.py`）。

## 关键依赖与配置

仅依赖标准库（argparse/subprocess/urllib 等）；从仓库根定位各模块，无需安装。

## 测试与质量

`backend/tests/test_dev_scripts.py` 覆盖脚本行为；修改启动流程后务必跑后端测试套件。

## 相关文件清单

`dev.py`、`dev_supervisor.py`、`process_utils.py`、`dev_up.*`、`dev_down.*`、`dev_status.*`、`dev.ps1`、`generate_long_knowledge_docx.py`

## 变更记录 (Changelog)

- 2026-09-17T10:09:30+08:00 增量更新：补充 dev.ps1 说明、dev.py 自动准备沙箱运行时与依赖检测说明。
- 2026-09-11T15:42:50 — 初始化架构师首次生成。
