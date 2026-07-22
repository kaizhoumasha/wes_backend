"""入库确认领域 request 到现有 DispatchEnvelope 的唯一映射。"""

from src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.contract import CONTRACT
from src.app.runtime.system_capabilities.wms.scheduling_identity import WMS_PRODUCTION_PROFILE_IDENTITY
from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.sys.models import DispatchEnvelope, SystemOutboxDispatchType, SystemOutboxTargetType
from src.app.wms_integration.ports.confirm_inbound_operation import ConfirmInboundOperationRequest


class ConfirmInboundDispatchGateway:
    """构造入库确认 EXTERNAL_HTTP 包络，不执行外部 I/O。"""

    def build_envelope(self, request: ConfirmInboundOperationRequest) -> DispatchEnvelope:
        payload = {
            "inbound_key": request.inbound_key,
            "material_code": request.material_code,
            "quantity": str(request.quantity),
            "warehouse_code": request.warehouse_code,
            "owner_code": request.owner_code,
            "lot_no": request.lot_no,
        }
        payload_json = {key: value for key, value in payload.items() if value is not None}
        canonical = CanonicalPayload.from_projection(payload_json)
        return DispatchEnvelope(
            dispatch_key=request.dispatch_key,
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code=CONTRACT.target_code,
            provider_profile_identity=WMS_PRODUCTION_PROFILE_IDENTITY,
            operation_identity=CONTRACT.identity,
            payload_json=payload_json,
            canonical_payload_bytes=canonical.body,
            payload_hash=canonical.sha256,
            operation_domain="WMS_INVENTORY",
            operation_key=request.inbound_key,
            workline_id=request.workline_id,
            session_id=request.session_id,
            trace_id=request.trace_id,
        )


__all__ = ["ConfirmInboundDispatchGateway"]
