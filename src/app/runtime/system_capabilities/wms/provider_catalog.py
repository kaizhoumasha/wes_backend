"""当前部署唯一 WMS Provider profile 与 typed operation 的组合真源。"""

from __future__ import annotations

from typing import Any, Literal

from src.app.runtime.orchestration.operation_observability import require_northbound_operation_slo
from src.app.runtime.system_capabilities.wms.contracts import (
    ExternalContractProfile,
    InboundCallbackContract,
    OutboundAuthProfile,
    OutboundAuthScheme,
    WmsEffectStatusHint,
    WmsProviderOperationBinding,
    WmsProviderProfile,
)
from src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.contract import (
    CONTRACT as FULL_BOX_EXCHANGE_CONTRACT,
)
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.contract import (
    CONTRACT as NOTIFY_PACKAGE_BINDING_CONTRACT,
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
from src.app.sys.external_http_credentials import (
    EXTERNAL_HTTP_CREDENTIAL_ENV_BY_REFERENCE,
    CredentialResolutionError,
    build_environment_external_http_credential_provider,
)
from src.app.sys.services.endpoint_registry import ENDPOINT_SETTING_BY_TARGET_CODE, EndpointRegistry
from src.app.wms_integration.transport_url import validate_wms_base_url
from src.core.conf import settings

WMS_EFFECT_STATUS_HINT_CALLBACK = InboundCallbackContract(
    callback_type="WMS_EFFECT_STATUS_HINT",
    payload_model=WmsEffectStatusHint,
)


def _binding(
    profile: ExternalContractProfile,
    outbound_auth: OutboundAuthProfile,
    operation: Any,
) -> WmsProviderOperationBinding:
    _ = require_northbound_operation_slo(operation.identity)
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
        callbacks=(WMS_EFFECT_STATUS_HINT_CALLBACK,),
    )


# 进程 composition root 只构建一次；endpoint/credential rotation 由冻结 binding revision 表达，
# 不创建可路由的 Provider catalog。
WMS_PROVIDER_PROFILE = build_active_wms_provider_profile(settings)
WMS_NORTHBOUND_IDENTITY = WMS_PROVIDER_PROFILE.identity
WMS_NORTHBOUND_AUTH = WMS_PROVIDER_PROFILE.bindings[0].outbound_auth
WMS_TYPED_EFFECT_CALLBACK_TYPES = frozenset(callback.callback_type for callback in WMS_PROVIDER_PROFILE.callbacks)


def wms_sync_base_url(*, settings_source: Any | None = None) -> str:
    """返回当前部署 typed WMS operation 共用的唯一同步服务根地址。"""

    active_settings = settings if settings_source is None else settings_source
    base_url = active_settings.WMS_SYNC_BASE_URL.strip().rstrip("/")
    if not base_url:
        raise ValueError("WMS_SYNC_BASE_URL 必须显式配置")
    return base_url


def _validate_wms_sla_configuration(settings_source: Any) -> None:
    minimum_retention = (
        settings_source.WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS
        + settings_source.WES_EFFECT_STATUS_SAFETY_MARGIN_SECONDS
    )
    if minimum_retention > settings_source.WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS:
        raise ValueError("WMS EFFECT idempotency retention 不得小于 WES confirmation age 与 safety margin 之和")
    if settings_source.WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS > settings_source.WES_EFFECT_NOT_FOUND_GRACE_SECONDS:
        raise ValueError("WMS EFFECT visibility SLA 不得大于 WES NOT_FOUND grace period")


def validate_wms_transport_configuration(*, settings_source: Any | None = None) -> None:
    """在 API/Celery 开始接收任务前校验同一 Settings 构建的 active profile。"""

    active_settings = settings if settings_source is None else settings_source
    active_profile = build_active_wms_provider_profile(active_settings)
    environment = active_profile.identity.environment
    base_url = wms_sync_base_url(settings_source=active_settings)
    parsed = validate_wms_base_url(base_url)
    if environment == "production" and parsed.scheme.lower() != "https":
        raise ValueError("production WMS_SYNC_BASE_URL 必须使用 HTTPS")

    status_url = validate_wms_base_url(active_settings.WMS_EFFECT_STATUS_URL)
    if environment == "production" and status_url.scheme.lower() != "https":
        raise ValueError("production WMS_EFFECT_STATUS_URL 必须使用 HTTPS")
    _validate_wms_sla_configuration(active_settings)

    endpoint_registry = EndpointRegistry(settings_source=active_settings)
    for target_code, setting_name in ENDPOINT_SETTING_BY_TARGET_CODE.items():
        try:
            endpoint = endpoint_registry.resolve(target_code)
            parsed_endpoint = validate_wms_base_url(endpoint.url)
        except ValueError as exc:
            raise ValueError(f"{setting_name} 必须为活动运行环境显式配置为合法 HTTP(S) endpoint") from exc
        if environment == "production" and parsed_endpoint.scheme.lower() != "https":
            raise ValueError(f"production {setting_name} 必须使用 HTTPS")

    credential_provider = build_environment_external_http_credential_provider(settings_source=active_settings)
    credential_references = frozenset(
        str(binding.outbound_auth.credential_reference)
        for binding in active_profile.bindings
        if binding.outbound_auth.credential_reference is not None
    )
    for credential_reference in credential_references:
        env_name = EXTERNAL_HTTP_CREDENTIAL_ENV_BY_REFERENCE[credential_reference]
        try:
            credential_provider.resolve(credential_reference)
        except (CredentialResolutionError, LookupError) as exc:
            raise ValueError(f"{env_name} 必须为活动运行环境显式配置且未被撤销") from exc


def _typed_wms_endpoint_registry(
    profile: WmsProviderProfile,
    *,
    settings_source: Any | None = None,
) -> EndpointRegistry:
    base_url = wms_sync_base_url(settings_source=settings_source)
    return EndpointRegistry(
        {
            binding.operation.target_code: f"{base_url}/{binding.operation.endpoint_path.lstrip('/')}"
            for binding in profile.bindings
            if binding.operation.mode.value == "EFFECT"
        }
    )


def _external_http_effect_profile(profile: WmsProviderProfile) -> ExternalHttpProviderProfileDefinition:
    return ExternalHttpProviderProfileDefinition(
        identity=profile.identity.identity,
        environment=profile.identity.environment,
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
    return freeze_external_http_binding(
        profile=WMS_EXTERNAL_HTTP_EFFECT_PROFILE,
        operation_identity=operation_identity,
        target_code=target_code,
        endpoint_registry=registry or _typed_wms_endpoint_registry(WMS_PROVIDER_PROFILE),
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
    "wms_sync_base_url",
]
