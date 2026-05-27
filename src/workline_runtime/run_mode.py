"""WORKLINE 运行模式辅助。"""

from __future__ import annotations

from typing import Any

from src.utils.value_normalization import enum_value

SANDBOX_ALLOWED_ENVS = frozenset({"dev", "test"})


def normalize_run_mode(value: Any, *, default: str = "AUTO") -> str:
    """将模型枚举/字符串运行模式规范化为大写字符串。"""

    raw_value = enum_value(value)
    if not isinstance(raw_value, str) or not raw_value:
        return default
    return raw_value.strip().upper() or default


def is_simulation_run_mode(value: Any) -> bool:
    """判断是否为 WORKLINE 级沙箱模拟模式。"""

    return normalize_run_mode(value) == "SIMULATION"


def is_sandbox_allowed_environment(app_env: Any) -> bool:
    """沙箱只允许在开发/测试环境开启。"""

    return isinstance(app_env, str) and app_env.strip().lower() in SANDBOX_ALLOWED_ENVS


__all__ = [
    "SANDBOX_ALLOWED_ENVS",
    "is_sandbox_allowed_environment",
    "is_simulation_run_mode",
    "normalize_run_mode",
]
