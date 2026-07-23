"""WMS Provider profile 与 typed operation 的 author-time 组合真源。"""

from types import MappingProxyType

from src.app.runtime.system_capabilities.wms.contracts import (
    ExternalContractProfile,
    OutboundAuthProfile,
    OutboundAuthScheme,
    WmsProviderOperationBinding,
    WmsProviderProfile,
)
from src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.contract import (
    CALLBACK_CONTRACT as FULL_BOX_EXCHANGE_CALLBACK,
)
from src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.contract import (
    CONTRACT as FULL_BOX_EXCHANGE_CONTRACT,
)
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.contract import (
    CALLBACK_CONTRACT as NOTIFY_PACKAGE_BINDING_CALLBACK,
)
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.contract import (
    CONTRACT as NOTIFY_PACKAGE_BINDING_CONTRACT,
)
from src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.contract import (
    CALLBACK_CONTRACT as CONFIRM_INBOUND_CALLBACK,
)
from src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.contract import (
    CONTRACT as CONFIRM_INBOUND_CONTRACT,
)
from src.app.runtime.system_capabilities.wms.inventory.query_inventory.contract import (
    CONTRACT as QUERY_INVENTORY_CONTRACT,
)
from src.app.runtime.system_capabilities.wms.scheduling_identity import WMS_MATERIAL_FLOW_CONTRACT_VERSION
from src.app.sys.external_http_binding import (
    ExternalHttpBindingDefinition,
    ExternalHttpProviderProfileDefinition,
    FrozenExternalHttpBinding,
    freeze_external_http_binding,
)
from src.app.sys.services.endpoint_registry import EndpointRegistry, endpoint_registry


def _binding(profile, outbound_auth, operation):
    return WmsProviderOperationBinding(
        profile=profile,
        operation=operation,
        outbound_auth=outbound_auth,
    )


def _profile(environment: str) -> WmsProviderProfile:
    identity = ExternalContractProfile(
        provider_code="WMS",
        contract_version=WMS_MATERIAL_FLOW_CONTRACT_VERSION,
        environment=environment,
    )
    outbound_auth = OutboundAuthProfile(
        scheme=OutboundAuthScheme.HMAC_SHA256,
        credential_reference=f"secret://wms/material-flow-{environment}-hmac@v1",
    )
    return WmsProviderProfile(
        identity=identity,
        bindings=tuple(
            _binding(identity, outbound_auth, operation)
            for operation in (
                QUERY_INVENTORY_CONTRACT,
                CONFIRM_INBOUND_CONTRACT,
                NOTIFY_PACKAGE_BINDING_CONTRACT,
                FULL_BOX_EXCHANGE_CONTRACT,
            )
        ),
        callbacks=(
            CONFIRM_INBOUND_CALLBACK,
            NOTIFY_PACKAGE_BINDING_CALLBACK,
            FULL_BOX_EXCHANGE_CALLBACK,
        ),
    )


WMS_PROVIDER_PROFILES = MappingProxyType(
    {
        profile.identity.identity: profile
        for profile in (
            _profile("sandbox"),
            _profile("staging"),
            _profile("production"),
        )
    }
)
WMS_PROVIDER_PROFILE = WMS_PROVIDER_PROFILES[f"wms.{WMS_MATERIAL_FLOW_CONTRACT_VERSION}.production"]
WMS_NORTHBOUND_IDENTITY = WMS_PROVIDER_PROFILE.identity
WMS_NORTHBOUND_AUTH = WMS_PROVIDER_PROFILE.bindings[0].outbound_auth


def _external_http_effect_profile(profile: WmsProviderProfile) -> ExternalHttpProviderProfileDefinition:
    return ExternalHttpProviderProfileDefinition(
        identity=profile.identity.identity,
        bindings=tuple(
            ExternalHttpBindingDefinition(
                operation_identity=binding.operation.identity,
                allowed_target_codes=(binding.operation.target_code,),
                http_method=binding.operation.http_method.value,
                timeout_seconds=binding.operation.budget.timeout_seconds,
                auth_scheme=binding.outbound_auth.scheme.value,
                credential_reference=str(binding.outbound_auth.credential_reference),
            )
            for binding in profile.bindings
            if binding.operation.mode.value == "EFFECT"
        ),
    )


WMS_EXTERNAL_HTTP_EFFECT_PROFILES = MappingProxyType(
    {identity: _external_http_effect_profile(profile) for identity, profile in WMS_PROVIDER_PROFILES.items()}
)


def freeze_wms_effect_binding(
    *,
    profile_identity: str,
    operation_identity: str,
    target_code: str,
    registry: EndpointRegistry = endpoint_registry,
) -> FrozenExternalHttpBinding:
    """从 WMS typed Provider catalog 冻结单个 EFFECT target/auth binding。"""

    profile = WMS_EXTERNAL_HTTP_EFFECT_PROFILES.get(profile_identity)
    if profile is None:
        raise ValueError("WMS EFFECT provider profile is not authored")
    return freeze_external_http_binding(
        profile=profile,
        operation_identity=operation_identity,
        target_code=target_code,
        endpoint_registry=registry,
    )


def resolve_wms_operation_binding(
    *,
    profile_identity: str,
    operation_identity: str,
) -> WmsProviderOperationBinding:
    """只按 attempt pin 的完整 profile identity 解析唯一 operation binding。"""

    profile = WMS_PROVIDER_PROFILES.get(profile_identity.strip().lower())
    if profile is None:
        raise LookupError("pinned WMS provider profile is not authored")
    bindings = tuple(binding for binding in profile.bindings if binding.operation.identity == operation_identity)
    if len(bindings) != 1:
        raise LookupError("pinned WMS operation binding must resolve exactly once")
    return bindings[0]


__all__ = [
    "WMS_EXTERNAL_HTTP_EFFECT_PROFILES",
    "WMS_MATERIAL_FLOW_CONTRACT_VERSION",
    "WMS_NORTHBOUND_AUTH",
    "WMS_NORTHBOUND_IDENTITY",
    "WMS_PROVIDER_PROFILE",
    "WMS_PROVIDER_PROFILES",
    "freeze_wms_effect_binding",
    "resolve_wms_operation_binding",
]
