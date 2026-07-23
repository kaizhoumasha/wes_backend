"""满箱交换领域 request 到现有 DispatchEnvelope 的唯一映射。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.runtime.system_capabilities.wms.effect_binding import freeze_wms_effect_binding
from src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.contract import CONTRACT
from src.app.runtime.system_capabilities.wms.scheduling_identity import WMS_PRODUCTION_PROFILE_IDENTITY
from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.sys.models import DispatchEnvelope, SystemOutboxDispatchType, SystemOutboxTargetType

if TYPE_CHECKING:
    from src.app.sys.services.endpoint_registry import EndpointRegistry
    from src.app.wms_integration.ports.full_box_exchange_operation import FullBoxExchangeOperationRequest


class FullBoxExchangeDispatchGateway:
    """构造满箱交换 EXTERNAL_HTTP 包络，不执行外部 I/O。"""

    def __init__(self, *, registry: EndpointRegistry | None = None) -> None:
        self._registry = registry

    def build_envelope(self, request: FullBoxExchangeOperationRequest) -> DispatchEnvelope:
        payload_json = {
            "rack_id": request.rack_id,
            "empty_box_id": request.empty_box_id,
            "full_box_id": request.full_box_id,
        }
        canonical = CanonicalPayload.from_projection(payload_json)
        frozen_binding = freeze_wms_effect_binding(
            profile_identity=WMS_PRODUCTION_PROFILE_IDENTITY,
            operation_identity=CONTRACT.identity,
            target_code=CONTRACT.target_code,
            registry=self._registry,
        )
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
            frozen_binding=frozen_binding,
            operation_domain="WMS_FULFILLMENT",
            operation_key=(f"{request.provider_code}:{request.rack_id}:{request.empty_box_id}:{request.full_box_id}"),
            workline_id=request.workline_id,
            session_id=request.session_id,
            trace_id=request.trace_id,
        )


__all__ = ["FullBoxExchangeDispatchGateway"]
