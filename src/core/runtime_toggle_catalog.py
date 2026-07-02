"""Typed runtime toggle catalog evaluated by release gates."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.runtime_toggles import RuntimeToggleDefinition


# 当前基线没有活跃 Phase 3 runtime toggle；新增 toggle 必须声明在这里并通过发布门禁。
RUNTIME_TOGGLES: tuple[RuntimeToggleDefinition, ...] = ()


__all__ = ["RUNTIME_TOGGLES"]
