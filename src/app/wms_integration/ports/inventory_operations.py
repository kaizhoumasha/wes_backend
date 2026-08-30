"""WMS inbound-confirmation typed contract retained by the target adapter."""

from __future__ import annotations

from pydantic import Field

from src.app.wms_integration.ports.operation_common import EffectRequest, EffectResult, PositiveDecimal, StableText


class ConfirmInboundRequest(EffectRequest):
    inbound_key: StableText = Field(max_length=160)
    material_code: StableText = Field(max_length=120)
    quantity: PositiveDecimal
    pkg_id: StableText = Field(max_length=160)
    location_code: StableText = Field(max_length=120)


class ConfirmInboundResult(EffectResult):
    inbound_key: StableText = Field(max_length=160)
    wms_document_no: StableText = Field(max_length=160)
    inventory_source_version: StableText = Field(max_length=160)


def validate_confirm_inbound_terminal_identity(
    request: ConfirmInboundRequest,
    result: ConfirmInboundResult,
) -> None:
    if request.inbound_key != result.inbound_key:
        raise ValueError("confirm inbound terminal identity differs from request")


__all__ = [
    "ConfirmInboundRequest",
    "ConfirmInboundResult",
    "validate_confirm_inbound_terminal_identity",
]
