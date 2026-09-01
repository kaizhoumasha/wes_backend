"""WMS package-binding typed contract retained by the target adapter."""

from __future__ import annotations

from pydantic import Field

from src.app.wms_integration.ports.operation_common import EffectRequest, EffectResult, StableText


class NotifyPkgBindingRequest(EffectRequest):
    pkg_id: StableText = Field(max_length=160)
    bin_id: StableText = Field(max_length=120)
    slot_id: StableText = Field(max_length=120)
    rack_id: StableText = Field(max_length=120)
    station_code: StableText = Field(max_length=120)


class NotifyPkgBindingResult(EffectResult):
    pkg_id: StableText = Field(max_length=160)
    binding_reference: StableText = Field(max_length=160)


def validate_notify_pkg_binding_terminal_identity(
    request: NotifyPkgBindingRequest,
    result: NotifyPkgBindingResult,
) -> None:
    if request.pkg_id != result.pkg_id:
        raise ValueError("package binding terminal identity differs from request")


__all__ = [
    "NotifyPkgBindingRequest",
    "NotifyPkgBindingResult",
    "validate_notify_pkg_binding_terminal_identity",
]
