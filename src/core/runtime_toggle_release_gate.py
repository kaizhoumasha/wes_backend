"""Release gate adapter for typed runtime toggles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.core.runtime_toggles import RuntimeToggleDefinition, RuntimeToggleKind, RuntimeToggleRegistry

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date


@dataclass(frozen=True, slots=True)
class RuntimeToggleReleaseDecision:
    """Decision returned by the runtime toggle release gate."""

    ready: bool
    reason: str = "OK"
    toggle_name: str | None = None
    missing_checks: tuple[str, ...] = ()


class RuntimeToggleReleaseBlocked(Exception):
    """Raised when typed runtime toggles are not release-ready."""

    def __init__(self, decision: RuntimeToggleReleaseDecision) -> None:
        self.decision = decision
        message = decision.reason
        if decision.toggle_name:
            message = f"{message}: {decision.toggle_name}"
        super().__init__(message)


class RuntimeToggleReleaseGate:
    """Convert runtime toggle governance violations into release blockers."""

    def __init__(self, toggles: Iterable[RuntimeToggleDefinition] | None = None) -> None:
        self.toggles = tuple(toggles or ())

    def evaluate(
        self,
        *,
        today: date,
        passed_checks: Iterable[str] | None = None,
    ) -> RuntimeToggleReleaseDecision:
        registry_decision = RuntimeToggleRegistry(list(self.toggles)).validate(today=today)
        if not registry_decision.valid:
            return RuntimeToggleReleaseDecision(
                ready=False,
                reason=registry_decision.reason,
                toggle_name=registry_decision.toggle_name,
            )

        normalized_passed_checks = frozenset(check.strip() for check in passed_checks or () if check.strip())
        for toggle in self.toggles:
            if toggle.kind != RuntimeToggleKind.RELEASE:
                continue
            if toggle.default:
                return RuntimeToggleReleaseDecision(
                    ready=False,
                    reason="RELEASE_TOGGLE_DEFAULT_ON",
                    toggle_name=toggle.name,
                )

            missing_checks = tuple(check for check in toggle.test_matrix if check not in normalized_passed_checks)
            if missing_checks:
                return RuntimeToggleReleaseDecision(
                    ready=False,
                    reason="TOGGLE_TEST_MATRIX_NOT_VERIFIED",
                    toggle_name=toggle.name,
                    missing_checks=missing_checks,
                )

        return RuntimeToggleReleaseDecision(ready=True)

    def assert_release_ready(
        self,
        *,
        today: date,
        passed_checks: Iterable[str] | None = None,
    ) -> RuntimeToggleReleaseDecision:
        decision = self.evaluate(today=today, passed_checks=passed_checks)
        if not decision.ready:
            raise RuntimeToggleReleaseBlocked(decision)
        return decision


__all__ = [
    "RuntimeToggleReleaseBlocked",
    "RuntimeToggleReleaseDecision",
    "RuntimeToggleReleaseGate",
]
