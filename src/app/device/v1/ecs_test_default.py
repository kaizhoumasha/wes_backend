"""ECS_TEST 来源设备默认值 API。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import APIRouter, Body, Path, status
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from src.app.device.services.device_service import device_service
from src.core import rbac  # noqa: TC001 - FastAPI resolves permission dependencies at registration
from src.core.exceptions import NotFoundException, ValidationException
from src.core.response import ResponseSchemaModel, response_builder
from src.database.dependencies import (  # noqa: TC001 - FastAPI resolves route dependencies at registration
    AsyncSessionDep,
    CacheDep,
)

if TYPE_CHECKING:
    from src.app.device.models.device import Device

router = APIRouter(tags=["设备管理"])


class _EcsTestDefaultRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_device_code: StrictStr = Field(min_length=1, max_length=100)
    task_type: StrictStr = Field(min_length=1, max_length=100)
    params: dict[str, Any]


class _EcsTestDefaultPut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: _EcsTestDefaultRule | None = Field(...)


class _EcsTestDefaultResponse(BaseModel):
    device_id: int
    device_code: str
    device_version: int
    default: _EcsTestDefaultRule | None


def _data(device: Device) -> _EcsTestDefaultResponse:
    return _EcsTestDefaultResponse(
        device_id=device.id,
        device_code=device.device_code,
        device_version=device.version,
        default=device.ecs_test_default_json,
    )


@router.get(
    "/devices/{device_code}/ecs-test-default",
    summary="[biz:device:detail] 读取 ECS_TEST 来源设备默认值",
    response_model=ResponseSchemaModel[_EcsTestDefaultResponse],
    status_code=status.HTTP_200_OK,
    responses={404: {"model": ResponseSchemaModel[dict[str, Any]], "description": "Device 不存在"}},
)
async def get_ecs_test_default(
    device_code: Annotated[str, Path(min_length=1, max_length=100)],
    db: AsyncSessionDep,
    _permission: rbac.PermissionDep("biz:device:detail"),
) -> ResponseSchemaModel[_EcsTestDefaultResponse]:
    device = await device_service.get_device_by_code(db, device_code)
    if device is None:
        raise NotFoundException(resource_type="Device", resource_id=device_code)
    return cast("ResponseSchemaModel[_EcsTestDefaultResponse]", response_builder.success(data=_data(device)))


@router.put(
    "/devices/{device_code}/ecs-test-default",
    summary="[biz:device:update] 保存 ECS_TEST 来源设备默认值",
    response_model=ResponseSchemaModel[_EcsTestDefaultResponse],
    status_code=status.HTTP_200_OK,
    responses={404: {"model": ResponseSchemaModel[dict[str, Any]], "description": "Device 不存在"}},
)
async def put_ecs_test_default(
    device_code: Annotated[str, Path(min_length=1, max_length=100)],
    payload: Annotated[_EcsTestDefaultPut, Body()],
    db: AsyncSessionDep,
    cache: CacheDep,
    _permission: rbac.PermissionDep("biz:device:update"),
) -> ResponseSchemaModel[_EcsTestDefaultResponse]:
    default = None if payload.default is None else payload.default.model_dump()
    try:
        device = await device_service.save_ecs_test_default(db, device_code, default, cache)
    except ValueError as error:
        raise ValidationException(str(error)) from error
    if device is None:
        raise NotFoundException(resource_type="Device", resource_id=device_code)
    return cast("ResponseSchemaModel[_EcsTestDefaultResponse]", response_builder.success(data=_data(device)))


__all__ = ["router"]
