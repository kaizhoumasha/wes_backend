"""MaterialUnit 只读 API facade。"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Query, status

from src.app.runtime.orchestration.services.query.material_location_query_service import (
    MaterialLocationResult,
    material_location_query_service,
)
from src.core.rbac import RequirePermission
from src.core.response import ClientErrorCode, ResponseSchemaModel, response_builder
from src.database.dependencies import AsyncSessionDep  # noqa: TC001 - FastAPI needs runtime annotation

router = APIRouter(tags=["物料运行视图"])


@router.get(
    "/material-units/location-query",
    summary="[biz:material:location-query] 查询物料作业期位置",
    response_model=ResponseSchemaModel[MaterialLocationResult],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:material:location-query"))],
)
async def query_material_unit_location(
    db: AsyncSessionDep,
    package_id: str | None = Query(default=None, description="PkgID / package_id"),
    bin_code: str | None = Query(default=None, description="料箱编码"),
    material_identity_key: str | None = Query(default=None, description="物料身份键"),
    rack_code: str | None = Query(default=None, description="货架编码"),
    rack_side: str | None = Query(default=None, description="货架面"),
    external_reference_type: str | None = Query(default=None, description="外部引用类型"),
    external_reference_value: str | None = Query(default=None, description="外部引用值"),
    provider_code: str | None = Query(default=None, description="provider code"),
    correlation_id: str | None = Query(default=None, description="ExecutionCorrelation.correlation_id"),
) -> ResponseSchemaModel[MaterialLocationResult]:
    """统一 MaterialLocationQuery 入口，API 层只委托查询 service。"""

    if package_id or bin_code:
        result = await material_location_query_service.query_by_package_or_bin(
            db,
            package_id=package_id,
            bin_code=bin_code,
        )
    elif material_identity_key:
        result = await material_location_query_service.query_by_material_identity(
            db,
            material_identity_key=material_identity_key,
        )
    elif rack_code and rack_side:
        result = await material_location_query_service.query_by_rack_and_side(
            db,
            rack_code=rack_code,
            rack_side=rack_side,
        )
    elif external_reference_type and external_reference_value:
        result = await material_location_query_service.query_by_external_reference(
            db,
            external_reference_type=external_reference_type,
            external_reference_value=external_reference_value,
            provider_code=provider_code,
        )
    elif correlation_id:
        result = await material_location_query_service.query_by_correlation_id(db, correlation_id=correlation_id)
    else:
        return cast(
            "ResponseSchemaModel[MaterialLocationResult]",
            response_builder.fail(
                code=ClientErrorCode.VALIDATION_ERROR,
                message="至少提供 package_id、bin_code、material_identity_key、rack_code+rack_side、ExternalReference 或 correlation_id",
            ),
        )

    return cast("ResponseSchemaModel[MaterialLocationResult]", response_builder.success(data=result))


__all__ = ["router"]
