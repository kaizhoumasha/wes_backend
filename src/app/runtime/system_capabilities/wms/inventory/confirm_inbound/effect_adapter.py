"""`confirm_inbound` DispatchEnvelope 到既有 T8 双账本的薄适配。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.sys.models import SystemOutbox

from .gateway import ConfirmInboundDispatchGateway

if TYPE_CHECKING:
    from src.app.wms_integration.ports.confirm_inbound_operation import ConfirmInboundOperationRequest


class ConfirmInboundEffectAdapter:
    """只冻结出站 binding 并构造 SystemOutbox；持久化由 orchestration Service 负责。"""

    def __init__(self, *, gateway: ConfirmInboundDispatchGateway | None = None) -> None:
        self._gateway = gateway or ConfirmInboundDispatchGateway()

    def build_outbox(
        self,
        request: ConfirmInboundOperationRequest,
    ) -> SystemOutbox:
        envelope = self._gateway.build_envelope(request)
        frozen_binding = envelope.frozen_binding
        if frozen_binding is None:
            raise ValueError("confirm_inbound requires frozen EXTERNAL_HTTP binding")
        return SystemOutbox(
            session_id=envelope.session_id,
            workline_id=envelope.workline_id,
            device_id=envelope.device_id,
            operation_domain=envelope.operation_domain,
            operation_key=envelope.operation_key,
            dispatch_type=envelope.dispatch_type,
            dispatch_key=envelope.dispatch_key,
            target_type=envelope.target_type,
            target_code=frozen_binding.target_snapshot.code,
            provider_profile_identity=frozen_binding.provider_profile_identity,
            operation_identity=frozen_binding.operation_identity,
            provider_profile_hash=frozen_binding.provider_profile_hash,
            binding_revision=frozen_binding.binding_revision,
            target_snapshot_json=frozen_binding.target_snapshot.as_json(),
            target_snapshot_hash=frozen_binding.target_snapshot_hash,
            auth_scheme=frozen_binding.auth_scheme,
            credential_reference=frozen_binding.credential_reference,
            payload_json=envelope.payload_json,
            canonical_payload_bytes=envelope.canonical_payload_bytes,
            payload_hash=envelope.payload_hash,
            trace_id=envelope.trace_id,
        )


confirm_inbound_effect_adapter = ConfirmInboundEffectAdapter()

__all__ = [
    "ConfirmInboundEffectAdapter",
    "confirm_inbound_effect_adapter",
]
