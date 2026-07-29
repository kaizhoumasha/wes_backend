"""当前部署唯一 WMS Provider profile 与 typed operation 的组合真源。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from src.app.runtime.system_capabilities.wms.contracts import (
    ExternalContractProfile,
    InboundCallbackContract,
    OutboundAuthProfile,
    OutboundAuthScheme,
    WmsEffectStatusHint,
    WmsProviderOperationBinding,
    WmsProviderProfile,
)
from src.app.runtime.system_capabilities.wms.scheduling_identity import WMS_MATERIAL_FLOW_CONTRACT_VERSION
from src.app.sys.external_http_binding import (
    ExternalHttpBindingDefinition,
    ExternalHttpProviderProfileDefinition,
    FrozenExternalHttpBinding,
    freeze_external_http_binding,
)
from src.app.wms_integration.operation_registry import WMS_OPERATIONS
from src.app.wms_integration.provider_manifest import require_full_factory_registry
from src.core.conf import settings

if TYPE_CHECKING:
    from src.app.sys.services.endpoint_registry import EndpointRegistry
    from src.app.wms_integration.operation_contract import WmsOperationDefinition
    from src.app.wms_integration.provider_startup import WmsProviderStartupConfiguration

WMS_EFFECT_STATUS_HINT_CALLBACK = InboundCallbackContract(
    callback_type="WMS_EFFECT_STATUS_HINT",
    payload_model=WmsEffectStatusHint,
)


def _binding(
    profile: ExternalContractProfile,
    outbound_auth: OutboundAuthProfile,
    operation: WmsOperationDefinition,
) -> WmsProviderOperationBinding:
    return WmsProviderOperationBinding(
        profile=profile,
        operation=operation,
        outbound_auth=outbound_auth,
    )


def _deployment_provider_environment(app_env: object) -> Literal["sandbox", "production"]:
    """把部署环境收敛为一个 Provider profile；不提供请求期选择能力。"""

    if app_env in {"dev", "test"}:
        return "sandbox"
    if app_env == "prod":
        return "production"
    raise ValueError("APP_ENV must be dev, test or prod")


def build_active_wms_provider_profile(settings_source: Any) -> WmsProviderProfile:
    """仅从当前部署 Settings 构建一个 active WMS Provider profile。"""

    environment = _deployment_provider_environment(settings_source.APP_ENV)
    credential_version = settings_source.WMS_MATERIAL_FLOW_ACTIVE_HMAC_VERSION
    if credential_version not in {"v1", "v2"}:
        raise ValueError("WMS_MATERIAL_FLOW_ACTIVE_HMAC_VERSION must be v1 or v2")
    identity = ExternalContractProfile(
        provider_code="WMS",
        contract_version=WMS_MATERIAL_FLOW_CONTRACT_VERSION,
        environment=environment,
    )
    outbound_auth = OutboundAuthProfile(
        scheme=OutboundAuthScheme.HMAC_SHA256,
        credential_reference=f"secret://wms/material-flow-{environment}-hmac@{credential_version}",
    )
    profile = WmsProviderProfile(
        identity=identity,
        bindings=tuple(_binding(identity, outbound_auth, operation) for operation in WMS_OPERATIONS),
        callbacks=(WMS_EFFECT_STATUS_HINT_CALLBACK,),
    )
    require_full_factory_registry(tuple(binding.operation.identity for binding in profile.bindings))
    return profile


# 进程 composition root 只构建一次；endpoint/credential rotation 由冻结 binding revision 表达，
# 不创建可路由的 Provider catalog。
WMS_PROVIDER_PROFILE = build_active_wms_provider_profile(settings)
WMS_NORTHBOUND_IDENTITY = WMS_PROVIDER_PROFILE.identity
WMS_NORTHBOUND_AUTH = WMS_PROVIDER_PROFILE.bindings[0].outbound_auth
WMS_TYPED_EFFECT_CALLBACK_TYPES = frozenset(callback.callback_type for callback in WMS_PROVIDER_PROFILE.callbacks)


def _validate_wms_sla_configuration(settings_source: Any) -> None:
    minimum_retention = (
        settings_source.WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS
        + settings_source.WES_EFFECT_STATUS_SAFETY_MARGIN_SECONDS
    )
    if minimum_retention > settings_source.WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS:
        raise ValueError("WMS EFFECT idempotency retention 不得小于 WES confirmation age 与 safety margin 之和")
    if settings_source.WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS > settings_source.WES_EFFECT_NOT_FOUND_GRACE_SECONDS:
        raise ValueError("WMS EFFECT visibility SLA 不得大于 WES NOT_FOUND grace period")


def validate_wms_transport_configuration(
    *,
    settings_source: Any | None = None,
) -> WmsProviderStartupConfiguration:
    """在 API/Celery 接收任务前编译并校验部署拥有的 Provider profile。"""

    active_settings = settings if settings_source is None else settings_source
    if active_settings.APP_ENV == "prod" and getattr(
        active_settings,
        "WMS_QUERY_IN_PROCESS_SIMULATION_ENABLED",
        False,
    ):
        raise ValueError("production WMS QUERY in-process simulation is forbidden")
    _validate_wms_sla_configuration(active_settings)
    from src.app.wms_integration.provider_startup import assemble_wms_provider_startup

    return assemble_wms_provider_startup(active_settings)


def _external_http_effect_profile(profile: WmsProviderProfile) -> ExternalHttpProviderProfileDefinition:
    return ExternalHttpProviderProfileDefinition(
        identity=profile.identity.identity,
        environment=profile.identity.environment,
        bindings=tuple(
            _external_http_effect_binding(binding)
            for binding in profile.bindings
            if binding.operation.mode.value == "EFFECT"
        ),
    )


def _external_http_effect_binding(binding: WmsProviderOperationBinding) -> ExternalHttpBindingDefinition:
    """把通用 WMS binding 收窄为 EXTERNAL_HTTP EFFECT 支持的 POST/HMAC 合同。"""

    http_method = binding.operation.http_method.value
    if http_method != "POST":
        raise ValueError("WMS EXTERNAL_HTTP EFFECT binding only supports POST")
    auth_scheme = binding.outbound_auth.scheme.value
    if auth_scheme != "HMAC_SHA256":
        raise ValueError("WMS EXTERNAL_HTTP EFFECT binding requires HMAC_SHA256")
    return ExternalHttpBindingDefinition(
        operation_identity=binding.operation.identity,
        allowed_target_codes=(binding.operation.target_code,),
        http_method=http_method,
        timeout_seconds=binding.operation.budget.deadline_seconds,
        auth_scheme=auth_scheme,
        credential_reference=str(binding.outbound_auth.credential_reference),
    )


WMS_EXTERNAL_HTTP_EFFECT_PROFILE = _external_http_effect_profile(WMS_PROVIDER_PROFILE)


def freeze_wms_effect_binding(
    *,
    profile_identity: str,
    operation_identity: str,
    target_code: str,
    registry: EndpointRegistry | None = None,
) -> FrozenExternalHttpBinding:
    """从当前部署 profile 冻结新 Intent 的 submit target/auth binding。"""

    if profile_identity.strip().lower() != WMS_PROVIDER_PROFILE.identity.identity:
        raise ValueError("WMS EFFECT provider profile is not active in this deployment")
    if registry is None:
        raise RuntimeError("compiled WMS EFFECT endpoint registry must be injected by the T3 runtime composition root")
    return freeze_external_http_binding(
        profile=WMS_EXTERNAL_HTTP_EFFECT_PROFILE,
        operation_identity=operation_identity,
        target_code=target_code,
        endpoint_registry=registry,
    )


def resolve_wms_operation_binding(
    *,
    profile_identity: str,
    operation_identity: str,
) -> WmsProviderOperationBinding:
    """只在当前部署 active profile 内解析唯一 operation binding。"""

    if profile_identity.strip().lower() != WMS_PROVIDER_PROFILE.identity.identity:
        raise LookupError("pinned WMS provider profile is not active in this deployment")
    bindings = tuple(
        binding for binding in WMS_PROVIDER_PROFILE.bindings if binding.operation.identity == operation_identity
    )
    if len(bindings) != 1:
        raise LookupError("pinned WMS operation binding must resolve exactly once")
    return bindings[0]


__all__ = [
    "WMS_EFFECT_STATUS_HINT_CALLBACK",
    "WMS_EXTERNAL_HTTP_EFFECT_PROFILE",
    "WMS_MATERIAL_FLOW_CONTRACT_VERSION",
    "WMS_NORTHBOUND_AUTH",
    "WMS_NORTHBOUND_IDENTITY",
    "WMS_PROVIDER_PROFILE",
    "WMS_TYPED_EFFECT_CALLBACK_TYPES",
    "build_active_wms_provider_profile",
    "freeze_wms_effect_binding",
    "resolve_wms_operation_binding",
    "validate_wms_transport_configuration",
]
