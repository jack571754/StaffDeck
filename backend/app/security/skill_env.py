from __future__ import annotations

import os
from pathlib import Path

from app import paths
from app.config import get_settings


def skill_secret_allowlist() -> frozenset[str]:
    return get_settings().general_skill_env_passthrough_keys


def skill_secret_environment() -> dict[str, str]:
    """返回白名单内的密钥环境变量：os.environ 优先，缺失键用 .env 补齐。

    绝不修改 os.environ；非白名单键一律不返回。
    """
    keys = skill_secret_allowlist()
    if not keys:
        return {}
    resolved: dict[str, str] = {}
    for key in keys:
        value = os.environ.get(key)
        if value is not None:
            resolved[key] = value
    dotenv_path = _dotenv_path()
    if dotenv_path is not None and dotenv_path.is_file():
        try:
            from dotenv import dotenv_values
        except ImportError:  # pydantic-settings 保证存在，防御性兜底
            return resolved
        for key, value in dotenv_values(dotenv_path).items():
            if key in keys and value is not None and key not in resolved:
                resolved[key] = value
    return resolved


def _dotenv_path() -> Path | None:
    raw = os.environ.get("ULTRARAG_DOTENV", ".env")
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    from_cwd = Path.cwd() / candidate
    if from_cwd.is_file():
        return from_cwd
    # Settings 由 uvicorn 从 backend/ 启动；cwd 不是 backend 时回退到 backend 目录
    return paths.app_root() / candidate
