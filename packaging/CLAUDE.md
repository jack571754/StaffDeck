[根目录](../CLAUDE.md) > **packaging**

# packaging — 桌面端打包与签名

## 模块职责

macOS / Linux / Windows 桌面客户端打包、沙箱运行时获取、签名与冒烟测试。

## 文件清单

| 文件 | 说明 |
|---|---|
| `ultrarag.spec` | PyInstaller spec（macOS/windows）|
| `build_macos.sh` / `build_linux.sh` / `build_windows.ps1` | 各平台构建 |
| `fetch_runtime_python.py` / `fetch_sandbox_runtime.py` | 下载附带 Python 与沙箱运行时 |
| `make_dmg_background.py` | macOS DMG 背景 |
| `sign_windows.ps1` / `WINDOWS_SIGNING.md` | Windows 签名 |
| `smoke_sandbox_runtime.py` / `smoke_sandbox_bundle.py` | 沙箱冒烟 |
| `_docker_test_linux.sh` | Linux Docker 测试 |

## 构建说明

- `.gitignore` 忽略 `packaging/out/ build/ runtime/ runtime_dl/ sandbox_runtime/` 等生成物（含附带的 node.exe / python 二进制）。
- 预编译 `.pyc`/缓存不进 git。

## 测试与质量

相关测试 `backend/tests/test_fetch_sandbox_runtime.py`、`test_packaging_deps.py`；`smoke_*` 脚本做冒烟。

## 相关文件清单

`ultrarag.spec`、`build_*.{sh,ps1}`、`fetch_runtime_python.py`、`fetch_sandbox_runtime.py`、`sign_windows.ps1`、`WINDOWS_SIGNING.md`。

## 变更记录 (Changelog)

- 2026-09-23T18:06 — 初始化架构师重建模块文档。