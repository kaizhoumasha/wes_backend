"""北向 runtime operation 只读运维 API。"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query, Request, status

from src.app.runtime.orchestration.operational_models import (
    NorthboundOperationalPrincipal,
    NorthboundOperationalSnapshot,
)
from src.app.runtime.orchestration.services.query.northbound_operations_query_service import (
    northbound_operations_query_service,
)
from src.core.rbac import RequirePermission
from src.core.response import ResponseSchemaModel, response_builder
from src.core.security import require_auth
from src.database.dependencies import AsyncSessionDep  # noqa: TC001 - FastAPI runtime annotation

router = APIRouter(prefix="/runtime-operations", tags=["运行运维只读入口"])


def _northbound_operational_principal(
    request: Request,
    user_id: Annotated[int, Depends(require_auth)],
) -> NorthboundOperationalPrincipal:
    """当前版本以 WorkLine.created_by 对齐 tenant/owner scope。"""

    return NorthboundOperationalPrincipal(
        tenant_id=user_id,
        user_id=user_id,
        is_superuser=bool(getattr(request.state, "is_superuser", False)),
    )


@router.get(
    "/northbound",
    summary="[sys:runtime-operations:view] 获取北向 operation 运维快照",
    response_model=ResponseSchemaModel[NorthboundOperationalSnapshot],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("sys:runtime-operations:view"))],
)
async def get_northbound_runtime_operations(
    db: AsyncSessionDep,
    principal: Annotated[NorthboundOperationalPrincipal, Depends(_northbound_operational_principal)],
    workline_id: int | None = Query(default=None, ge=1),
) -> ResponseSchemaModel[NorthboundOperationalSnapshot]:
    """只允许 Service 读取 owner-scoped 聚合 SLI；不得返回 payload/trace/secret。"""

    snapshot = await northbound_operations_query_service.get_snapshot(
        db,
        principal=principal,
        workline_id=workline_id,
    )
    return cast(
        "ResponseSchemaModel[NorthboundOperationalSnapshot]",
        response_builder.success(data=snapshot),
    )


__all__ = ["router"]
