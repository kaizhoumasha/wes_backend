"""Typed runtime toggle governance."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date


class RuntimeToggleKind(str, Enum):
    """Allowed runtime toggle kind."""

    RELEASE = "release"
    OPS = "ops"


@dataclass(frozen=True, slots=True)
class RuntimeToggleDefinition:
    """Typed runtime toggle declaration."""

    name: str
    kind: RuntimeToggleKind
    owner: str
    expiry: date
    scope: str
    default: bool
    rollback: str
    test_matrix: tuple[str, ...]
    protected_capabilities: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class RuntimeToggleValidation:
    """Runtime toggle validation result."""

    valid: bool
    reason: str = "OK"
    toggle_name: str | None = None


class RuntimeToggleRegistry:
    """Validate typed release/ops toggles before release."""

    _FORBIDDEN_BYPASS: frozenset[str] = frozenset(
        {
            "idle_admission",
            "hmac",
            "idempotency",
            "evidence",
            "runtime_hold",
        }
    )

    def __init__(self, toggles: list[RuntimeToggleDefinition] | None = None) -> None:
        self.toggles = toggles or []

    def validate(self, *, today: date) -> RuntimeToggleValidation:
        for toggle in self.toggles:
            if not toggle.owner or not toggle.scope or not toggle.rollback or not toggle.test_matrix:
                return RuntimeToggleValidation(False, "MISSING_REQUIRED_FIELD", toggle.name)
            if toggle.expiry < today:
                return RuntimeToggleValidation(False, "TOGGLE_EXPIRED", toggle.name)
            if toggle.protected_capabilities & self._FORBIDDEN_BYPASS:
                return RuntimeToggleValidation(False, "PROTECTED_CAPABILITY_BYPASS", toggle.name)
        return RuntimeToggleValidation(True)


__all__ = [
    "RuntimeToggleDefinition",
    "RuntimeToggleKind",
    "RuntimeToggleRegistry",
    "RuntimeToggleValidation",
]
