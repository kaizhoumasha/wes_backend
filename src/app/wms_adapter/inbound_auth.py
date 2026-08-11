"""WMS 入站认证的启动时冻结策略。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.app.contracts.wms_inbound import WMS_BUSINESS_EVENT_TYPES
from src.app.wms_integration.provider_profile import WmsProviderAuthScheme

if TYPE_CHECKING:
    from src.app.wms_integration.endpoint_compiler import CompiledWmsProviderProfile


_WMS_EFFECT_STATUS_HINT = "WMS_EFFECT_STATUS_HINT"


@dataclass(frozen=True, slots=True)
class WmsInboundAuthPolicy:
    """由已编译 Provider profile 派生的、进程生命周期内不变的认证策略。"""

    profile_digest: str
    network_trust_mode: str
    inbound_auth_scheme: WmsProviderAuthScheme

    @classmethod
    def from_compiled_profile(cls, compiled_profile: CompiledWmsProviderProfile) -> WmsInboundAuthPolicy:
        profile = compiled_profile.profile
        return cls(
            profile_digest=compiled_profile.profile_digest,
            network_trust_mode=profile.network_trust_mode,
            inbound_auth_scheme=profile.inbound_auth.scheme,
        )

    @property
    def allows_unsigned_wms_callbacks(self) -> bool:
        return self.network_trust_mode == "isolated_lan" and self.inbound_auth_scheme is WmsProviderAuthScheme.NONE

    def permits_unsigned_event(self, payload: dict[str, Any]) -> bool:
        source_system = payload.get("source_system")
        event_type = payload.get("event_type")
        return (
            self.allows_unsigned_wms_callbacks
            and isinstance(source_system, str)
            and source_system == "WMS"
            and isinstance(event_type, str)
            and event_type in WMS_BUSINESS_EVENT_TYPES
        )

    def permits_unsigned_external(self, payload: dict[str, Any]) -> bool:
        source_system = payload.get("source_system")
        callback_type = payload.get("callback_type")
        return (
            self.allows_unsigned_wms_callbacks
            and isinstance(source_system, str)
            and source_system == "WMS"
            and isinstance(callback_type, str)
            and callback_type == _WMS_EFFECT_STATUS_HINT
        )


__all__ = ["WmsInboundAuthPolicy"]
