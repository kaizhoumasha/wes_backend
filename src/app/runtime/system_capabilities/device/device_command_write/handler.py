"""DeviceCommand OUTBOX_ASYNC handler。"""

from __future__ import annotations

from src.app.runtime.system_capabilities.outcomes import BusinessReject, Success

from .contracts import DeviceCommandWriteAdmission, DeviceCommandWriteInput, DeviceCommandWriteOutput


class DeviceCommandWriteHandler:
    async def __call__(self, request: DeviceCommandWriteInput, *, execution: object) -> object:
        from src.app.runtime.orchestration.services.device_command_gateway import prepare_runtime_device_command_effect

        ctx = execution.ctx  # type: ignore[attr-defined]
        admission = execution.admission  # type: ignore[attr-defined]
        if not isinstance(admission, DeviceCommandWriteAdmission):
            raise TypeError("device command effect requires typed admission")
        target_device = self._resolve_target_device(ctx, request)
        actual_available = self._is_available(target_device)
        if (
            target_device is not None and self._fact_version(target_device) != admission.fact_version
        ) or actual_available != admission.precondition.expected_available:
            return BusinessReject(reason_code="STALE_PRECONDITION", message="device fact changed")
        if target_device is None:
            return BusinessReject(reason_code="TARGET_DEVICE_UNAVAILABLE", message="target device is unavailable")
        if not actual_available:
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

    @staticmethod
    def _fact_version(device: object) -> str:
        version = getattr(device, "version", None)
        if not isinstance(version, int) or isinstance(version, bool) or version < 0:
            return "device:v-1"
        return f"device:v{version}"

    @staticmethod
    def _is_available(device: object | None) -> bool:
        if device is None or getattr(device, "is_active", True) is not True:
            return False
        status = getattr(device, "device_status", "IDLE")
        status_value = str(getattr(status, "value", status))
        return (
            status_value == "IDLE"
            and getattr(device, "maintenance_mode", False) is False
            and getattr(device, "current_command_id", None) is None
        )


__all__ = ["DeviceCommandWriteHandler"]
