"""In-memory material flow state transition engine."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from itertools import count
from typing import Protocol

from src.workline_runtime.material_run import LifecycleState, MaterialRun
from src.workline_runtime.material_target_resolver import resolve_destination_device
from src.workline_runtime.runtime_event import RuntimeEvent, RuntimeEventType
from src.workline_runtime.runtime_intent import Destination, RuntimeIntent, RuntimeIntentKind


class RuntimeDevice(Protocol):
    id: int
    device_role: str
    upstream_device_id: int | None


@dataclass(frozen=True)
class ResolvedDevice:
    id: int
    device_role: str
    upstream_device_id: int | None
    sort_order: int = 0
    role_index: int = 0


@dataclass(frozen=True)
class MaterialFlowResult:
    run: MaterialRun
    command_id: int | None
    blocker_id: int | None
    events: list[RuntimeEvent]


IdFactory = Callable[[], int]


class MaterialFlowEngine:
    def __init__(
        self,
        command_id_factory: IdFactory | None = None,
        blocker_id_factory: IdFactory | None = None,
    ) -> None:
        command_id_sequence = count(1)
        blocker_id_sequence = count(1)
        self._command_id_factory = command_id_factory or command_id_sequence.__next__
        self._blocker_id_factory = blocker_id_factory or blocker_id_sequence.__next__

    def apply(
        self,
        *,
        run: MaterialRun,
        source_device: RuntimeDevice,
        devices: list[RuntimeDevice],
        plugin_key: str,
        trace_id: str,
        intent: RuntimeIntent,
    ) -> MaterialFlowResult:
        events = [
            RuntimeEvent(
                event_type=RuntimeEventType.PLUGIN_DECISION_MADE,
                trace_id=trace_id,
                material_identity_key=run.material_identity_key,
                workline_id=run.workline_id,
                device_id=source_device.id,
                device_role=source_device.device_role,
                plugin_key=plugin_key,
                action=intent.action,
                reason_code=intent.reason_code,
                payload_json=intent.model_dump(mode="json"),
            )
        ]

        if intent.kind == RuntimeIntentKind.COMMAND:
            return self._apply_command_intent(
                run=run,
                source_device=source_device,
                devices=devices,
                plugin_key=plugin_key,
                trace_id=trace_id,
                intent=intent,
                events=events,
            )

        if intent.kind == RuntimeIntentKind.BLOCK:
            return self._apply_block_intent(
                run=run,
                source_device=source_device,
                plugin_key=plugin_key,
                trace_id=trace_id,
                intent=intent,
                events=events,
            )

        raise ValueError(f"Unsupported intent kind: {intent.kind.value}")

    def _apply_command_intent(
        self,
        *,
        run: MaterialRun,
        source_device: RuntimeDevice,
        devices: list[RuntimeDevice],
        plugin_key: str,
        trace_id: str,
        intent: RuntimeIntent,
        events: list[RuntimeEvent],
    ) -> MaterialFlowResult:
        command_id = self._command_id_factory()
        target = self._resolve_target_device(
            destination=intent.destination or Destination.current(),
            source_device=source_device,
            devices=devices,
        )

        next_run = run.model_copy(
            update={
                "current_device_id": target.id,
                "current_device_role": target.device_role,
                "current_action": intent.action,
                "lifecycle_state": LifecycleState.WAITING,
                "awaiting_command_id": command_id,
                "wait_reason": "COMMAND_RESULT",
            }
        )

        events.extend(
            [
                RuntimeEvent(
                    event_type=RuntimeEventType.COMMAND_CREATED,
                    trace_id=trace_id,
                    material_identity_key=next_run.material_identity_key,
                    workline_id=next_run.workline_id,
                    device_id=target.id,
                    device_role=target.device_role,
                    plugin_key=plugin_key,
                    action=intent.action,
                    command_id=command_id,
                    payload_json=intent.payload_json,
                ),
                RuntimeEvent(
                    event_type=RuntimeEventType.MATERIAL_ENTERED_DEVICE,
                    trace_id=trace_id,
                    material_identity_key=next_run.material_identity_key,
                    workline_id=next_run.workline_id,
                    device_id=target.id,
                    device_role=target.device_role,
                    plugin_key=plugin_key,
                    action=intent.action,
                    command_id=command_id,
                ),
            ]
        )

        return MaterialFlowResult(run=next_run, command_id=command_id, blocker_id=None, events=events)

    def _apply_block_intent(
        self,
        *,
        run: MaterialRun,
        source_device: RuntimeDevice,
        plugin_key: str,
        trace_id: str,
        intent: RuntimeIntent,
        events: list[RuntimeEvent],
    ) -> MaterialFlowResult:
        blocker_id = self._blocker_id_factory()

        updated = run.model_copy(
            update={
                "lifecycle_state": LifecycleState.BLOCKED,
                "blocker_id": blocker_id,
            }
        )

        events.append(
            RuntimeEvent(
                event_type=RuntimeEventType.PROCESS_BLOCKED,
                trace_id=trace_id,
                material_identity_key=updated.material_identity_key,
                workline_id=updated.workline_id,
                device_id=source_device.id,
                device_role=source_device.device_role,
                plugin_key=plugin_key,
                reason_code=intent.reason_code,
                payload_json={
                    "message": intent.message,
                    "suggested_action": intent.suggested_action,
                },
            )
        )

        return MaterialFlowResult(run=updated, command_id=None, blocker_id=blocker_id, events=events)

    def _resolve_target_device(
        self,
        *,
        destination: Destination,
        source_device: RuntimeDevice,
        devices: list[RuntimeDevice],
    ) -> ResolvedDevice:
        resolved_source = ResolvedDevice(
            id=source_device.id,
            device_role=source_device.device_role,
            upstream_device_id=source_device.upstream_device_id,
        )
        resolved_devices = [
            ResolvedDevice(
                id=device.id,
                device_role=device.device_role,
                upstream_device_id=device.upstream_device_id,
            )
            for device in devices
        ]
        return resolve_destination_device(
            destination=destination,
            source_device=resolved_source,
            devices=resolved_devices,
        )


__all__ = ["MaterialFlowEngine", "MaterialFlowResult"]
