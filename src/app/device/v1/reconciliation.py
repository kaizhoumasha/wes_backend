"""Device EVENT 阻塞因果的受限人工对账 API。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, Protocol, cast

from fastapi import APIRouter, Depends, Path, Request, status
from pydantic import BaseModel, ConfigDict, StringConstraints, field_serializer

from src.app.device.ecs_adapter import EcsStatusUnavailableError
from src.app.device.services.device_command_admission import DeviceCommandAdmissionError
from src.app.device.services.device_command_service import (
    DeviceCommandManualReconciliationConflictError,
    DeviceCommandManualReconciliationNotFoundError,
)
from src.app.device.services.device_evidence_service import (
    EventCommandBlockConflictError,
    EventCommandBlockNotFoundError,
)
from src.core.exceptions import ConflictException, NotFoundException, ServiceUnavailableException
from src.core.rbac import require_superuser
from src.core.response import ResponseSchemaModel, SuccessCode, response_builder

if TYPE_CHECKING:
    from src.app.device.contracts import DeviceCommandHandle
    from src.app.device.event_block_contracts import EventCommandBlockSnapshot, ReprocessedEventSnapshot

router = APIRouter(tags=["Device EVENT 对账"])

_REASON = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class _StrictApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ManualReconcileDeviceIdleRequest(_StrictApiModel):
    reason: _REASON


class ManualReconcileDeviceIdleResponse(_StrictApiModel):
    command_code: str
    status: str
    failure_code: str


class EventCommandBlockResponse(_StrictApiModel):
    block_id: int
    status: str
    source_event_id: str
    device_code: str
    blocking_command_code: str
    blocking_command_detected_status: str
    blocking_command_detected_reconciliation_reason: str | None
    blocking_command_current_status: str | None
    blocking_command_terminal: bool
    reason_code: str
    blocked_at: datetime
    requeued_at: datetime | None
    reconcile_device_idle_path: str
    reprocess_path: str

    @field_serializer("blocked_at", "requeued_at")
    def serialize_event_time(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


class ReprocessBlockedEventRequest(_StrictApiModel):
    reason: _REASON


class ReprocessBlockedEventResponse(_StrictApiModel):
    source_event_id: str
    block_id: int
    apply_status: str


class ReconciliationCommandServicePort(Protocol):
    async def reconcile_delivery_unknown_as_device_idle(
        self,
        *,
        source_event_id: str,
        block_id: int,
        reason: str,
        actor_id: int,
    ) -> DeviceCommandHandle: ...


class ReconciliationEvidenceServicePort(Protocol):
    async def get_event_command_block(self, source_event_id: str) -> EventCommandBlockSnapshot: ...

    async def reprocess_blocked_event(
        self,
        *,
        source_event_id: str,
        block_id: int,
        reason: str,
        actor_id: int,
    ) -> ReprocessedEventSnapshot: ...


def _command_service(request: Request) -> ReconciliationCommandServicePort:
    runtime = getattr(request.app.state, "device_command_runtime", None)
    if runtime is None:
        raise ServiceUnavailableException("DeviceCommand runtime 不可用")
    return cast("ReconciliationCommandServicePort", runtime.command_service)


def _evidence_service(request: Request) -> ReconciliationEvidenceServicePort:
    runtime = getattr(request.app.state, "device_command_runtime", None)
    if runtime is None:
        raise ServiceUnavailableException("DeviceCommand runtime 不可用")
    return cast("ReconciliationEvidenceServicePort", runtime.evidence_service)


@router.get(
    "/evidences/{source_event_id}/blocker",
    summary="查询 Device EVENT 最新命令阻塞因果",
    response_model=ResponseSchemaModel[EventCommandBlockResponse],
    responses={
        404: {"model": ResponseSchemaModel[dict[str, Any]], "description": "EVENT blocker 不存在"},
        409: {"model": ResponseSchemaModel[dict[str, Any]], "description": "阻塞因果不可用"},
    },
    dependencies=[Depends(require_superuser)],
)
async def get_event_command_block(
    request: Request,
    source_event_id: Annotated[str, Path(min_length=1, max_length=300)],
) -> ResponseSchemaModel[EventCommandBlockResponse]:
    try:
        snapshot = await _evidence_service(request).get_event_command_block(source_event_id)
    except EventCommandBlockNotFoundError as error:
        raise NotFoundException(resource_type="DeviceEventCommandBlock", resource_id=source_event_id) from error
    except EventCommandBlockConflictError as error:
        raise ConflictException(str(error)) from error
    data = EventCommandBlockResponse.model_validate(snapshot)
    return cast("ResponseSchemaModel[EventCommandBlockResponse]", response_builder.success(data=data))


@router.post(
    "/evidences/{source_event_id}/blockers/{block_id}/reprocess",
    summary="显式重新处理已闭合 blocker 的 Device EVENT",
    response_model=ResponseSchemaModel[ReprocessBlockedEventResponse],
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        404: {"model": ResponseSchemaModel[dict[str, Any]], "description": "EVENT blocker 不存在"},
        409: {"model": ResponseSchemaModel[dict[str, Any]], "description": "阻塞因果不允许重处理"},
    },
    dependencies=[Depends(require_superuser)],
)
async def reprocess_blocked_event(
    request: Request,
    payload: ReprocessBlockedEventRequest,
    source_event_id: Annotated[str, Path(min_length=1, max_length=300)],
    block_id: Annotated[int, Path(gt=0)],
) -> ResponseSchemaModel[ReprocessBlockedEventResponse]:
    try:
        snapshot = await _evidence_service(request).reprocess_blocked_event(
            source_event_id=source_event_id,
            block_id=block_id,
            reason=payload.reason,
            actor_id=request.state.user_id,
        )
    except EventCommandBlockNotFoundError as error:
        raise NotFoundException(resource_type="DeviceEventCommandBlock", resource_id=str(block_id)) from error
    except EventCommandBlockConflictError as error:
        raise ConflictException(str(error)) from error
    data = ReprocessBlockedEventResponse.model_validate(snapshot)
    return cast(
        "ResponseSchemaModel[ReprocessBlockedEventResponse]",
        response_builder.success(data=data, code=SuccessCode.ACCEPTED),
    )


@router.post(
    "/evidences/{source_event_id}/blockers/{block_id}/reconcile-device-idle",
    summary="以设备实时空闲证明闭合 DELIVERY_UNKNOWN 命令",
    response_model=ResponseSchemaModel[ManualReconcileDeviceIdleResponse],
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": ResponseSchemaModel[dict[str, Any]], "description": "EVENT blocker 不存在"},
        409: {"model": ResponseSchemaModel[dict[str, Any]], "description": "因果或设备状态不允许人工闭合"},
        503: {"model": ResponseSchemaModel[dict[str, Any]], "description": "ECS 状态查询不可用"},
    },
    dependencies=[Depends(require_superuser)],
)
async def reconcile_delivery_unknown_as_device_idle(
    request: Request,
    payload: ManualReconcileDeviceIdleRequest,
    source_event_id: Annotated[str, Path(min_length=1, max_length=300)],
    block_id: Annotated[int, Path(gt=0)],
) -> ResponseSchemaModel[ManualReconcileDeviceIdleResponse]:
    try:
        handle = await _command_service(request).reconcile_delivery_unknown_as_device_idle(
            source_event_id=source_event_id,
            block_id=block_id,
            reason=payload.reason,
            actor_id=request.state.user_id,
        )
    except DeviceCommandManualReconciliationNotFoundError as error:
        raise NotFoundException(resource_type="DeviceEventCommandBlock", resource_id=str(block_id)) from error
    except (DeviceCommandManualReconciliationConflictError, DeviceCommandAdmissionError) as error:
        raise ConflictException(str(error)) from error
    except EcsStatusUnavailableError as error:
        raise ServiceUnavailableException(str(error)) from error
    data = ManualReconcileDeviceIdleResponse(
        command_code=handle.command_code,
        status=handle.status,
        failure_code="MANUAL_RECONCILIATION_DEVICE_IDLE",
    )
    return cast("ResponseSchemaModel[ManualReconcileDeviceIdleResponse]", response_builder.success(data=data))


__all__ = ["router"]
