"""Plugin-facing helpers for declaring the next runtime intent."""

from __future__ import annotations

from typing import Any

from src.workline_runtime.runtime_intent import BlockScope, Destination, RuntimeIntent


class PluginNext:
    """Build RuntimeIntent objects without owning runtime state."""

    def command(
        self,
        device_role: str,
        action: str,
        payload: dict[str, Any] | None = None,
        destination_role: str | None = None,
        timeout_seconds: int | None = None,
    ) -> RuntimeIntent:
        destination = Destination.role(destination_role) if destination_role is not None else Destination.next()
        return RuntimeIntent.command(
            device_role=device_role,
            action=action,
            payload={} if payload is None else payload,
            destination=destination,
            timeout_seconds=timeout_seconds,
        )

    def block(
        self,
        scope: BlockScope,
        reason_code: str,
        message: str,
        suggested_action: str | None = None,
    ) -> RuntimeIntent:
        return RuntimeIntent.block(
            scope=scope,
            reason_code=reason_code,
            message=message,
            suggested_action=suggested_action,
        )


__all__ = ["PluginNext"]
