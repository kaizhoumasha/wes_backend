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

    def external_request(
        self,
        dispatch_key: str,
        target_code: str,
        payload: dict[str, Any],
        timeout_seconds: int,
        source_system: str | None = None,
    ) -> RuntimeIntent:
        return RuntimeIntent.external_request(
            dispatch_key=dispatch_key,
            target_code=target_code,
            payload=payload,
            timeout_seconds=timeout_seconds,
            source_system=source_system,
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

    def update_context(self, patch: dict[str, Any]) -> RuntimeIntent:
        return RuntimeIntent.update_context(patch)

    def complete(self, patch: dict[str, Any] | None = None) -> RuntimeIntent:
        return RuntimeIntent.complete(patch)

    def mark_ng(
        self,
        reason_code: str,
        message: str,
        payload: dict[str, Any] | None = None,
        destination: Destination | None = None,
    ) -> RuntimeIntent:
        return RuntimeIntent.mark_ng(
            reason_code=reason_code,
            message=message,
            payload=payload,
            destination=destination,
        )

    def continue_next(
        self,
        action: str | None = None,
        payload: dict[str, Any] | None = None,
        destination: Destination | None = None,
    ) -> RuntimeIntent:
        return RuntimeIntent.continue_next(
            action=action,
            payload=payload,
            destination=destination,
        )


__all__ = ["PluginNext"]
