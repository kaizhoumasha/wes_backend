"""Callback 域本地工具函数 (Phase 2 launch PR,跨域 import 修复)。

`src.workline_runtime.utils` 的本地副本,仅承载 callback 域真正使用的轻量
工具 (JsonDict 类型别名 + resolve_first_str/ensure_dict/payload_dict)。

设计:
- 故意不引入 `src.workline_runtime` 反向依赖,避免跨域逆向耦合
- 函数实现与 `src.workline_runtime.utils` 一一对应 (稳定 utility,无副作用)
- runtime migration 阶段再评估:若 callback 域迁入 runtime orchestration,
  本文件可删,改回 `src.app.runtime.orchestration.utils`
"""

from __future__ import annotations

from typing import Any, cast

JsonDict = dict[str, Any]


def non_empty_str(value: Any) -> str | None:
    """返回非空字符串,否则 None。"""
    return value if isinstance(value, str) and value else None


def payload_dict(value: Any) -> JsonDict:
    """安全转换为 dict,无效值返回空 dict。"""
    return cast("JsonDict", value) if isinstance(value, dict) else {}


def ensure_dict(value: Any) -> JsonDict:
    """将值转换为 JsonDict,非字典返回空字典。"""
    return cast("JsonDict", value) if isinstance(value, dict) else {}


def resolve_first_str(payload: JsonDict, aliases: tuple[str, ...]) -> str:
    """从 payload 中按别名优先级提取第一个有效字符串。"""
    for alias in aliases:
        value = payload.get(alias)
        if isinstance(value, str) and value:
            return value
    return ""


__all__ = ["JsonDict", "ensure_dict", "non_empty_str", "payload_dict", "resolve_first_str"]
