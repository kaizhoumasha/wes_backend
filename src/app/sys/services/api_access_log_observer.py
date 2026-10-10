"""WMS/ECS 共用完成观察，仅冻结传输元数据并登记诊断。"""

from src.app.sys.models.api_access_log import APIAccessLogCreate
from src.app.sys.services.api_access_log_service import api_access_log_service
from src.core.outbound_http import OutboundHttpRequest, OutboundHttpResult
from src.utils.audit import get_request_id


def observe_outbound_api_access(
    *,
    system_id: str,
    peer_address: str,
    request: OutboundHttpRequest,
    result: OutboundHttpResult,
    duration_ms: int,
) -> None:
    api_access_log_service.defer_record(
        APIAccessLogCreate(
            system_id=system_id,
            direction="OUTBOUND",
            method=request.method.value,
            path=request.path,
            peer_address=peer_address,
            request_id=get_request_id(),
            status_code=result.status_code,
            response_time_ms=duration_ms,
            delivery_state=result.delivery_state.value,
            error_code=result.failure_kind.value if result.failure_kind is not None else None,
        )
    )


__all__ = ["observe_outbound_api_access"]
