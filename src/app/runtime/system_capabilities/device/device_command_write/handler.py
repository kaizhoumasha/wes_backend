"""DeviceCommand OUTBOX_ASYNC handler。"""

from __future__ import annotations

from src.app.runtime.system_capabilities.outcomes import BusinessReject, Success

from .contracts import DeviceCommandWriteInput, DeviceCommandWriteOutput


class DeviceCommandWriteHandler:
    async def __call__(self, request: DeviceCommandWriteInput, *, execution: object) -> object:
        from src.app.runtime.orchestration.services.device_command_gateway import prepare_runtime_device_command_effect

        ctx = execution.ctx  # type: ignore[attr-defined]
        target_device = self._resolve_target_device(ctx, request)
        if target_device is None:
            return BusinessReject(reason_code="TARGET_DEVICE_UNAVAILABLE", message="target device is unavailable")
        command, outbox = await prepare_runtime_device_command_effect(
            ctx, request, target_device=target_device, execution=execution
        )
        return Success(
            payload=DeviceCommandWriteOutput(
                accepted=True,
                command_code=command.command_code,
                dispatch_key=outbox.dispatch_key,
            )
        )

    @staticmethod
    def _resolve_target_device(ctx: dict[str, object], request: DeviceCommandWriteInput) -> object | None:
        devices_by_role = ctx.get("devices_by_role")
        if isinstance(devices_by_role, dict):
            if request.device_role is not None:
                candidates = devices_by_role.get(request.device_role) or []
                return candidates[0] if candidates else None
            for candidates in devices_by_role.values():
                for candidate in candidates:
                    if getattr(candidate, "id", None) == request.target_device_id:
                        return candidate
        source_device = ctx.get("source_device")
        if getattr(source_device, "id", None) == request.target_device_id:
            return source_device
        return None


__all__ = ["DeviceCommandWriteHandler"]
