"""按现有权限目录分别声明实时流、近期记录与详情的只读权限。"""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from starlette.responses import StreamingResponse

from src.app.wms_diagnostics import WmsDiagnosticsService, build_diagnostics_service
from src.app.wms_diagnostics.contracts import ExchangeDetail, ExchangeFilters, ExchangePage, ExchangeQuery
from src.core.rbac import RequirePermission
from src.core.response import ResponseSchemaModel, response_builder
from src.core.sse import BoundedStreamingResponse

router = APIRouter(prefix="/v1/wms-diagnostics/exchanges", tags=["WMS diagnostics"])
_READ = "ops:wms-diagnostics:read"


def _service(request: Request) -> WmsDiagnosticsService:
    return getattr(request.app.state, "wms_diagnostics_service", None) or build_diagnostics_service()


@router.get(
    "/stream",
    summary="[ops:wms-diagnostics:stream] 实时观察 WMS 请求与响应",
    dependencies=[Depends(RequirePermission("ops:wms-diagnostics:stream"))],
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def stream_exchanges(request: Request, query: Annotated[ExchangeFilters, Query()]) -> StreamingResponse:
    return BoundedStreamingResponse(
        _service(request).stream_events(query),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(
    "",
    summary="[ops:wms-diagnostics:query] 查询近期 WMS 交互",
    dependencies=[Depends(RequirePermission("ops:wms-diagnostics:query"))],
    response_model=ResponseSchemaModel[ExchangePage],
)
async def list_exchanges(
    request: Request, query: Annotated[ExchangeQuery, Query()]
) -> ResponseSchemaModel[ExchangePage]:
    try:
        page = await _service(request).list_exchanges(query)
    except Exception as error:
        raise HTTPException(status_code=503, detail="诊断存储暂不可用") from error
    return cast("ResponseSchemaModel[ExchangePage]", response_builder.success(data=page))


@router.get(
    "/{exchange_id}",
    summary="[ops:wms-diagnostics:read] 查看 WMS 交互详情",
    dependencies=[Depends(RequirePermission(_READ))],
    response_model=ResponseSchemaModel[ExchangeDetail],
)
async def get_exchange(
    request: Request, exchange_id: Annotated[str, Path(pattern=r"^\d+-\d+$", max_length=48)]
) -> ResponseSchemaModel[ExchangeDetail]:
    try:
        detail = await _service(request).get_exchange(exchange_id)
    except Exception as error:
        raise HTTPException(status_code=503, detail="诊断存储暂不可用") from error
    if detail is None:
        raise HTTPException(status_code=404, detail="记录不存在或已过期")
    return cast("ResponseSchemaModel[ExchangeDetail]", response_builder.success(data=detail))
