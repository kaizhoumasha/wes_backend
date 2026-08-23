"""Swagger 驱动的 DeviceCommand 无业务联调入口。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, Protocol, cast

from fastapi import APIRouter, Body, Depends, Path, Request, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, field_serializer

from src.app.device.contracts import EcsDeviceInfo, EcsDeviceRuntimeState  # noqa: TC001
from src.app.device.ecs_adapter import EcsStatusUnavailableError
from src.app.device.services.device_command_service import (
    DeviceCommandCapacityError,
    DeviceCommandIdentityConflictError,
    DeviceCommandNotFoundError,
)
from src.core.exceptions import ConflictException, NotFoundException, ServiceUnavailableException, ValidationException
from src.core.rbac import require_superuser
from src.core.response import ResponseSchemaModel, SuccessCode, response_builder

if TYPE_CHECKING:
    from src.app.device.contracts import DeviceCommandHandle, ManualDebugDeviceCommandSnapshot

router = APIRouter(tags=["DeviceCommand 联调"])

_UUID7_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
_TOKEN_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"  # noqa: S105  # nosec B105 - token regex
_UUID7 = Annotated[str, StringConstraints(min_length=36, max_length=36, pattern=_UUID7_PATTERN)]
_DEVICE_TOKEN = Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=_TOKEN_PATTERN)]
_REASON = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
_MANUAL_DEBUG_CONTRACT_KEY = "third_party_integration"
_MANUAL_DEBUG_CONTRACT_VERSION = "1.1"


class _StrictApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ManualDebugDeviceCommandCreate(_StrictApiModel):
    client_request_id: _UUID7
    endpoint_base_url: str = Field(min_length=1, max_length=255)
    device_code: _DEVICE_TOKEN
    timeout: StrictInt = Field(gt=0, le=2**31 - 1)
    task_type: _DEVICE_TOKEN
    params: dict[str, Any] = Field(default_factory=dict)
    reason: _REASON


class ManualDebugPreflightRequest(_StrictApiModel):
    endpoint_base_url: str = Field(min_length=1, max_length=255)


class ManualDebugPreflightDevice(_StrictApiModel):
    device: EcsDeviceInfo
    state: EcsDeviceRuntimeState
    admissible: bool
    rejection_code: str | None


class ManualDebugPreflightResponse(_StrictApiModel):
    endpoint_base_url: str
    devices: tuple[ManualDebugPreflightDevice, ...]


class ManualDebugDeviceCommandCreated(_StrictApiModel):
    command_code: str
    client_request_id: str
    status: str


class DeviceCommandCallbackResponse(_StrictApiModel):
    result: str
    data: dict[str, Any]
    error_detail: dict[str, Any] | None
    source_event_id: str
    received_at: datetime | str
    apply_status: str

    @field_serializer("received_at")
    def serialize_received_at(self, value: datetime | str) -> str:
        return _api_time(value)


class ManualDebugDeviceCommandResponse(_StrictApiModel):
    command_code: str
    client_request_id: str
    device_code: str
    endpoint_base_url: str
    contract_key: str
    contract_version: str
    command_timeout_ms: int
    task_type: str
    params: dict[str, Any]
    trace_id: str | None
    status: str
    attempt_count: int
    ack_received_at: datetime | str | None
    completed_at: datetime | str | None
    failure_code: str | None
    reconciliation_reason: str | None
    execution_reason: str
    created_by: int
    callback: DeviceCommandCallbackResponse | None

    @field_serializer("ack_received_at", "completed_at")
    def serialize_lifecycle_time(self, value: datetime | str | None) -> str | None:
        return None if value is None else _api_time(value)


class ManualDebugCommandServicePort(Protocol):
    async def preflight_manual_debug(self, endpoint_base_url: str): ...

    async def create_manual_debug_command(self, **values: Any) -> DeviceCommandHandle: ...

    async def get_command_snapshot(self, command_code: str) -> ManualDebugDeviceCommandSnapshot: ...


_OPENAPI_EXAMPLES = {
    "onsite_station_scan1_move_forward": {
        "summary": "现场扫描工位前进联调",
        "value": {
            "client_request_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4471",
            "endpoint_base_url": "http://10.24.209.26:8080",
            "device_code": "STATION_SCAN1",
            "timeout": 30000,
            "task_type": "MOVE_FORWARD",
            "params": {
                "source": {
                    "location_id": "STATION_SCAN1",
                    "location_type": "SCAN_PLATFORM",
                }
            },
            "reason": "现场供应商联调",
        },
    }
}


def _api_time(value: datetime | str) -> str:
    if isinstance(value, str):
        return value
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _command_service(request: Request) -> ManualDebugCommandServicePort:
    runtime = getattr(request.app.state, "device_command_runtime", None)
    if runtime is None:
        raise ServiceUnavailableException("DeviceCommand runtime 不可用")
    return cast("ManualDebugCommandServicePort", runtime.command_service)


@router.post(
    "/commands/debug/preflight",
    summary="枚举 ECS 设备并检查 MANUAL_DEBUG 运行态",
    response_model=ResponseSchemaModel[ManualDebugPreflightResponse],
    dependencies=[Depends(require_superuser)],
)
async def preflight_manual_debug_command(
    request: Request,
    payload: ManualDebugPreflightRequest,
) -> ResponseSchemaModel[ManualDebugPreflightResponse]:
    try:
        snapshot = await _command_service(request).preflight_manual_debug(payload.endpoint_base_url)
    except EcsStatusUnavailableError as error:
        raise ServiceUnavailableException(str(error)) from error
    except ValueError as error:
        raise ValidationException(str(error), code="2004", status_code=400) from error
    data = ManualDebugPreflightResponse(
        endpoint_base_url=snapshot.endpoint_base_url,
        devices=tuple(
            ManualDebugPreflightDevice(
                device=item.status.device,
                state=item.status.state,
                admissible=item.rejection_code is None,
                rejection_code=item.rejection_code,
            )
            for item in snapshot.devices
        ),
    )
    return cast(
        "ResponseSchemaModel[ManualDebugPreflightResponse]",
        response_builder.success(data=data),
    )


@router.post(
    "/commands/debug",
    summary="创建 DeviceCommand 联调命令",
    response_model=ResponseSchemaModel[ManualDebugDeviceCommandCreated],
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        400: {"model": ResponseSchemaModel[dict[str, Any]], "description": "联调命令合同无效"},
        409: {"model": ResponseSchemaModel[dict[str, Any]], "description": "幂等身份或设备占用冲突"},
        503: {"model": ResponseSchemaModel[dict[str, Any]], "description": "DeviceCommand runtime 不可用"},
    },
    dependencies=[Depends(require_superuser)],
)
async def create_manual_debug_command(
    request: Request,
    payload: Annotated[ManualDebugDeviceCommandCreate, Body(openapi_examples=_OPENAPI_EXAMPLES)],
) -> ResponseSchemaModel[ManualDebugDeviceCommandCreated]:
    try:
        handle = await _command_service(request).create_manual_debug_command(
            client_request_id=payload.client_request_id,
            endpoint_base_url=payload.endpoint_base_url,
            device_code=payload.device_code,
            command_timeout_ms=payload.timeout,
            task_type=payload.task_type,
            params=payload.params,
            contract_key=_MANUAL_DEBUG_CONTRACT_KEY,
            contract_version=_MANUAL_DEBUG_CONTRACT_VERSION,
            trace_id=None,
            execution_reason=payload.reason,
            created_by=request.state.user_id,
        )
    except (DeviceCommandIdentityConflictError, DeviceCommandCapacityError) as error:
        raise ConflictException(str(error)) from error
    except EcsStatusUnavailableError as error:
        raise ServiceUnavailableException(str(error)) from error
    except ValueError as error:
        raise ValidationException(str(error), code="2004", status_code=400) from error
    data = ManualDebugDeviceCommandCreated(
        command_code=handle.command_code,
        client_request_id=payload.client_request_id,
        status=handle.status,
    )
    return cast(
        "ResponseSchemaModel[ManualDebugDeviceCommandCreated]",
        response_builder.success(data=data, code=SuccessCode.ACCEPTED),
    )


@router.get(
    "/commands/{command_code}",
    summary="查询 DeviceCommand 联调结果",
    response_model=ResponseSchemaModel[ManualDebugDeviceCommandResponse],
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": ResponseSchemaModel[dict[str, Any]], "description": "MANUAL_DEBUG DeviceCommand 不存在"},
        503: {"model": ResponseSchemaModel[dict[str, Any]], "description": "DeviceCommand runtime 不可用"},
    },
    dependencies=[Depends(require_superuser)],
)
async def get_manual_debug_command(
    request: Request,
    command_code: Annotated[str, Path(min_length=1, max_length=100)],
) -> ResponseSchemaModel[ManualDebugDeviceCommandResponse]:
    try:
        snapshot = await _command_service(request).get_command_snapshot(command_code)
    except DeviceCommandNotFoundError as error:
        raise NotFoundException(resource_type="DeviceCommand", resource_id=command_code) from error
    data = ManualDebugDeviceCommandResponse.model_validate(snapshot, from_attributes=True)
    return cast("ResponseSchemaModel[ManualDebugDeviceCommandResponse]", response_builder.success(data=data))


__all__ = ["router"]
