"""满箱交换领域 request 到现有 DispatchEnvelope 的唯一映射。"""

from src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.contract import CONTRACT
from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.sys.models import DispatchEnvelope, SystemOutboxDispatchType, SystemOutboxTargetType
from src.app.wms_integration.ports.full_box_exchange_operation import FullBoxExchangeOperationRequest


class FullBoxExchangeDispatchGateway:
    """构造满箱交换 EXTERNAL_HTTP 包络，不执行外部 I/O。"""

    def build_envelope(self, request: FullBoxExchangeOperationRequest) -> DispatchEnvelope:
        payload_json = {
            "rack_id": request.rack_id,
            "empty_box_id": request.empty_box_id,
            "full_box_id": request.full_box_id,
        }
        canonical = CanonicalPayload.from_projection(payload_json)
        return DispatchEnvelope(
            dispatch_key=request.dispatch_key,
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code=CONTRACT.target_code,
            payload_json=payload_json,
            canonical_payload_bytes=canonical.body,
            payload_hash=canonical.sha256,
            operation_domain="WMS_FULFILLMENT",
            operation_key=request.dispatch_key,
            workline_id=request.workline_id,
            session_id=request.session_id,
            trace_id=request.trace_id,
        )


__all__ = ["FullBoxExchangeDispatchGateway"]
