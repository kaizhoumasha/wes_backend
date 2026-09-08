"""Runtime service 按需导出，避免无关调用拉起完整执行闭包。"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

_MODULE_EXPORTS = {
    "session_hold_mutation_service": (
        "SessionHoldMutationService",
        "StaleSessionPrecondition",
        "session_hold_mutation_service",
    ),
}
_EXPORTS = {name: module for module, names in _MODULE_EXPORTS.items() for name in names}
if TYPE_CHECKING:
    from .session_hold_mutation_service import (
        SessionHoldMutationService,
        StaleSessionPrecondition,
        session_hold_mutation_service,
    )

__all__ = ["SessionHoldMutationService", "StaleSessionPrecondition", "session_hold_mutation_service"]


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    value = getattr(import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value
    return value
