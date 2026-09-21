[根目录](../CLAUDE.md) > **packaging**

# packaging — 三平台打包与发布

## 模块职责

将 StaffDeck 打包为 macOS / Linux / Windows 桌面应用与安装包：PyInstaller 收集后端 + 前端 dist + 沙箱运行时，配合平台签名与安装器脚本。

## 入口与启动（构建流程）

| 文件 | 说明 |
|---|---|
| `ultrarag.spec` | PyInstaller spec（**约定在 `backend/` 下执行**：`pyinstaller ../packaging/ultrarag.spec --noconfirm`）；要求先 `npm --prefix frontend-enterprise run build`；datas 收集前端 dist、`app/llm/prompts`、`app/db/seed_fixtures`、`mock_servers`、tzdata；图标 icns/ico；版本取 `VERSION` 环境变量并校验 |
| `build_macos.sh` / `build_linux.sh` / `build_windows.ps1` | 各平台构建入口 |
| `sign_windows.ps1` | Windows 签名（另见 `WINDOWS_SIGNING.md`） |
| `installer/ultrarag.iss` | Windows Inno Setup 安装脚本 |
| `fetch_runtime_python.py` | 下载打包用附带 Python 运行时 |
| `fetch_sandbox_runtime.py` | 获取经审查的 Anthropic Sandbox Runtime（SRT）+ 匹配 Node 二进制（源码部署亦可用：`python3 packaging/fetch_sandbox_runtime.py packaging/sandbox_runtime`；dev_up 会自动准备） |
| `make_dmg_background.py` | macOS DMG 背景 |
| `smoke_macos_app.sh` / `smoke_sandbox_bundle.py` / `smoke_sandbox_runtime.py` | 打包产物冒烟测试 |
| `assets/` | 图标、横幅、二维码等资源 |

## 关键依赖与配置

- 后端安装 `pyproject.toml` 的 `packaging` extra（pyinstaller；macOS 另需 pyobjc）。
- **生成物（勿扫描、勿提交）**：`packaging/out/`、`packaging/build/`、`packaging/runtime/`、`packaging/runtime_dl/`、`packaging/sandbox_runtime/`（含 node_modules 与 node.exe，~860 文件）。

## 测试与质量

`backend/tests/test_fetch_sandbox_runtime.py` 覆盖 SRT 获取逻辑；发布前跑 `smoke_*` 冒烟。

## 相关文件清单

`ultrarag.spec`、`build_*.sh`、`build_windows.ps1`、`sign_windows.ps1`、`installer/ultrarag.iss`、`fetch_runtime_python.py`、`fetch_sandbox_runtime.py`、`WINDOWS_SIGNING.md`

## 变更记录 (Changelog)

- 2026-09-11T15:42:50 — 初始化架构师首次生成。
