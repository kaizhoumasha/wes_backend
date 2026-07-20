"""DeviceCommand OUTBOX_ASYNC handler。"""

from __future__ import annotations

from src.app.runtime.system_capabilities.outcomes import BusinessReject, Success

from .contracts import DeviceCommandWriteAdmission, DeviceCommandWriteInput, DeviceCommandWriteOutput


class DeviceCommandWriteHandler:
    async def __call__(self, request: DeviceCommandWriteInput, *, execution: object) -> object:
        from src.app.runtime.orchestration.services.device_command_gateway import (
            StaleRuntimeDeviceCommandAdmission,
            prepare_runtime_device_command_effect,
        )

        ctx = execution.ctx  # type: ignore[attr-defined]
        admission = execution.admission  # type: ignore[attr-defined]
        if not isinstance(admission, DeviceCommandWriteAdmission):
            raise TypeError("device command effect requires typed admission")
        target_device_id, target_device_code = self._resolve_target_identity(ctx, request)
        if target_device_id is None and target_device_code is None:
            return BusinessReject(reason_code="TARGET_DEVICE_UNAVAILABLE", message="target device is unavailable")
        expected_workline_id = self._resolve_expected_workline_id(ctx)
        if expected_workline_id is None:
            return BusinessReject(
                reason_code="WORKLINE_SCOPE_UNAVAILABLE",
                message="runtime workline identity is unavailable",
            )
        try:
            command, outbox = await prepare_runtime_device_command_effect(
                ctx,
                request,
                target_device_id=target_device_id,
                target_device_code=target_device_code,
                expected_workline_id=expected_workline_id,
                admission=admission,
                execution=execution,
            )
        except StaleRuntimeDeviceCommandAdmission:
            return BusinessReject(reason_code="STALE_PRECONDITION", message="device fact changed")
        return Success(
            payload=DeviceCommandWriteOutput(
                accepted=True,
                command_code=command.command_code,
                dispatch_key=outbox.dispatch_key,
            )
        )

    @staticmethod
    def _resolve_target_identity(
        ctx: dict[str, object], request: DeviceCommandWriteInput
    ) -> tuple[int | None, str | None]:
        if request.target_device_id is not None:
            return request.target_device_id, None
        devices_by_role = ctx.get("devices_by_role")
        if isinstance(devices_by_role, dict) and request.device_role is not None:
            candidates = devices_by_role.get(request.device_role) or []
            if candidates:
                candidate = candidates[0]
                candidate_id = getattr(candidate, "id", None)
                if isinstance(candidate_id, int) and not isinstance(candidate_id, bool):
                    return candidate_id, None
                candidate_code = getattr(candidate, "device_code", None)
                if isinstance(candidate_code, str) and candidate_code:
                    return None, candidate_code
        return None, None

    @staticmethod
    def _resolve_expected_workline_id(ctx: dict[str, object]) -> int | None:
        for owner, attribute in (
            (ctx.get("session"), "workline_id"),
            (ctx.get("work_item"), "workline_id"),
            (ctx.get("workline"), "id"),
        ):
            workline_id = getattr(owner, attribute, None)
            if isinstance(workline_id, int) and not isinstance(workline_id, bool) and workline_id > 0:
                return workline_id
        return None


__all__ = ["DeviceCommandWriteHandler"]
