# 阶段 2 burn-down C5b 镜像:src.workline_runtime.plugin_next 的平级副本
# wlr 目录在阶段 3 整体删除时,本镜像与 wlr 副本合并 / 删除。

"""Plugin-facing helpers for declaring the next runtime intent."""

from __future__ import annotations

from typing import Any

from src.app.runtime.orchestration.runtime_intent import BlockScope, Destination, RuntimeIntent


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

    def rack_operation_request(
        self,
        *,
        operation_type: str,
        operation_key: str,
        target_code: str,
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> RuntimeIntent:
        return RuntimeIntent.rack_operation_request(
            operation_type=operation_type,
            operation_key=operation_key,
            target_code=target_code,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )

    def bin_operation_request(
        self,
        *,
        operation_type: str,
        operation_key: str,
        moves: list[dict[str, Any]],
        carrier_type: str = "CTU",
        carrier_code: str | None = None,
        timeout_seconds: int | None = None,
    ) -> RuntimeIntent:
        return RuntimeIntent.bin_operation_request(
            operation_type=operation_type,
            operation_key=operation_key,
            moves=moves,
            carrier_type=carrier_type,
            carrier_code=carrier_code,
            timeout_seconds=timeout_seconds,
        )

    def rack_bin_exchange_request(
        self,
        *,
        operation_type: str,
        operation_key: str,
        moves: list[dict[str, Any]],
        rack_code: str | None = None,
        carrier_type: str = "CTU",
        carrier_code: str | None = None,
        timeout_seconds: int | None = None,
    ) -> RuntimeIntent:
        return RuntimeIntent.rack_bin_exchange_request(
            operation_type=operation_type,
            operation_key=operation_key,
            moves=moves,
            rack_code=rack_code,
            carrier_type=carrier_type,
            carrier_code=carrier_code,
            timeout_seconds=timeout_seconds,
        )

    def device_event(
        self,
        *,
        device_code: str,
        event_type: str,
        data: dict[str, Any] | None = None,
        timestamp: int | None = None,
        event_id: str | None = None,
        causation_id: str | None = None,
        canonical_event_type: str | None = None,
    ) -> RuntimeIntent:
        return RuntimeIntent.device_event(
            device_code=device_code,
            event_type=event_type,
            data=data,
            timestamp=timestamp,
            event_id=event_id,
            causation_id=causation_id,
            canonical_event_type=canonical_event_type,
        )

    def resource_fact(
        self,
        *,
        fact_type: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> RuntimeIntent:
        return RuntimeIntent.resource_fact(
            fact_type=fact_type,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    def resource_reservation(
        self,
        *,
        operation: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> RuntimeIntent:
        return RuntimeIntent.resource_reservation(
            operation=operation,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    def block(
        self,
        scope: BlockScope,
        reason_code: str,
        message: str,
        suggested_action: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> RuntimeIntent:
        return RuntimeIntent.block(
            scope=scope,
            reason_code=reason_code,
            message=message,
            suggested_action=suggested_action,
            payload=payload,
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
