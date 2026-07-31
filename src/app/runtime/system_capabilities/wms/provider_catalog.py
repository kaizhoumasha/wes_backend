"""当前部署唯一 WMS Provider profile 与 typed operation 的组合真源。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.app.runtime.system_capabilities.wms.contracts import (
    ExternalContractProfile,
    InboundCallbackContract,
    OutboundAuthProfile,
    OutboundAuthScheme,
    WmsEffectStatusHint,
    WmsProviderOperationBinding,
)
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
    from src.app.wms_integration.endpoint_compiler import CompiledWmsOperationEndpoint, CompiledWmsProviderProfile
    from src.app.wms_integration.provider_startup import WmsProviderStartupConfiguration

WMS_EFFECT_STATUS_HINT_CALLBACK = InboundCallbackContract(
    callback_type="WMS_EFFECT_STATUS_HINT",
    payload_model=WmsEffectStatusHint,
)
WMS_TYPED_EFFECT_CALLBACK_TYPES = frozenset({WMS_EFFECT_STATUS_HINT_CALLBACK.callback_type})


@dataclass(frozen=True, slots=True)
class WmsProviderCatalog:
    """由一个 compiled profile 派生的 typed operation catalog。"""

    compiled_profile: CompiledWmsProviderProfile
    identity: ExternalContractProfile
    bindings: tuple[WmsProviderOperationBinding, ...]
    callbacks: tuple[InboundCallbackContract, ...]

    @property
    def profile_identity(self) -> str:
        return self.compiled_profile.profile.profile.identity

    @property
    def profile_digest(self) -> str:
        return self.compiled_profile.profile_digest


def build_wms_provider_catalog(compiled_profile: CompiledWmsProviderProfile) -> WmsProviderCatalog:
    """只从显式 compiled profile 派生 catalog，不读取 Settings 或模块级默认。"""

    profile_settings = compiled_profile.profile
    identity = ExternalContractProfile(
        provider_code=profile_settings.profile.provider_code,
        contract_version=profile_settings.profile.contract_version,
    )
    outbound_auth = OutboundAuthProfile(
        scheme=OutboundAuthScheme(profile_settings.outbound_auth.scheme.value),
        credential_reference=profile_settings.outbound_auth.credential_reference,
    )
    bindings = tuple(
        WmsProviderOperationBinding(
            profile=identity,
            operation=operation,
            outbound_auth=outbound_auth,
        )
        for operation in WMS_OPERATIONS
    )
    require_full_factory_registry(tuple(binding.operation.identity for binding in bindings))
    return WmsProviderCatalog(
        compiled_profile=compiled_profile,
        identity=identity,
        bindings=bindings,
        callbacks=(WMS_EFFECT_STATUS_HINT_CALLBACK,),
    )


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


def _external_http_effect_profile(catalog: WmsProviderCatalog) -> ExternalHttpProviderProfileDefinition:
    return _external_http_profile(
        catalog,
        bindings=tuple(
            _external_http_effect_binding(binding)
            for binding in catalog.bindings
            if binding.operation.mode.value == "EFFECT"
        ),
    )


def _external_http_profile(
    catalog: WmsProviderCatalog,
    *,
    bindings: tuple[ExternalHttpBindingDefinition, ...],
) -> ExternalHttpProviderProfileDefinition:
    # 该字段只承接通用 EXTERNAL_HTTP 的内部安全策略，不表示 WMS 外部环境。
    return ExternalHttpProviderProfileDefinition(
        identity=catalog.profile_identity,
        environment="production",
        network_trust_mode=catalog.compiled_profile.profile.network_trust_mode,
        bindings=bindings,
    )


def _external_http_effect_binding(binding: WmsProviderOperationBinding) -> ExternalHttpBindingDefinition:
    """把通用 WMS binding 收窄为共享 EXTERNAL_HTTP EFFECT 合同。"""

    http_method = binding.operation.http_method.value
    if http_method != "POST":
        raise ValueError("WMS EXTERNAL_HTTP EFFECT binding only supports POST")
    return _external_http_binding(binding, http_method=http_method)


def _external_http_binding(
    binding: WmsProviderOperationBinding,
    *,
    http_method: str,
) -> ExternalHttpBindingDefinition:
    """把静态 operation 和 compiled profile 安全事实映射为共享 HTTP binding。"""

    auth_scheme = binding.outbound_auth.scheme.value
    return ExternalHttpBindingDefinition(
        operation_identity=binding.operation.identity,
        allowed_target_codes=(binding.operation.target_code,),
        http_method=http_method,
        timeout_seconds=binding.operation.budget.deadline_seconds,
        auth_scheme=auth_scheme,
        credential_reference=binding.outbound_auth.credential_reference,
    )


def _external_http_query_profile(catalog: WmsProviderCatalog) -> ExternalHttpProviderProfileDefinition:
    return _external_http_profile(
        catalog,
        bindings=tuple(
            _external_http_binding(binding, http_method=binding.operation.http_method.value)
            for binding in catalog.bindings
            if binding.operation.mode.value == "QUERY"
        ),
    )


def _external_http_effect_status_profile(catalog: WmsProviderCatalog) -> ExternalHttpProviderProfileDefinition:
    return _external_http_profile(
        catalog,
        bindings=tuple(
            _external_http_binding(binding, http_method="GET")
            for binding in catalog.bindings
            if binding.operation.supports_status_query
        ),
    )


def _compiled_operation_binding(
    *,
    catalog: WmsProviderCatalog,
    operation_identity: str,
) -> tuple[WmsProviderOperationBinding, CompiledWmsOperationEndpoint]:
    bindings = tuple(binding for binding in catalog.bindings if binding.operation.identity == operation_identity)
    if len(bindings) != 1:
        raise ValueError("WMS operation binding must resolve exactly once from compiled profile")
    try:
        endpoint = catalog.compiled_profile.operations[operation_identity]
    except KeyError as exc:
        raise ValueError("WMS operation is absent from compiled profile") from exc
    return bindings[0], endpoint


def _freeze_wms_compiled_binding(
    *,
    catalog: WmsProviderCatalog,
    operation_identity: str,
    profile: ExternalHttpProviderProfileDefinition,
    endpoint_url: str,
    target_code: str,
) -> FrozenExternalHttpBinding:
    from src.app.sys.services.endpoint_registry import EndpointRegistry

    return freeze_external_http_binding(
        profile=profile,
        operation_identity=operation_identity,
        target_code=target_code,
        endpoint_registry=EndpointRegistry({target_code: endpoint_url}),
    )


def freeze_wms_query_binding(
    *,
    catalog: WmsProviderCatalog,
    operation_identity: str,
) -> FrozenExternalHttpBinding:
    """只从 compiled profile 冻结 QUERY endpoint/auth/method 结构。"""

    binding, endpoint = _compiled_operation_binding(
        catalog=catalog,
        operation_identity=operation_identity,
    )
    if binding.operation.mode.value != "QUERY":
        raise ValueError("query binding requires a QUERY operation")
    return _freeze_wms_compiled_binding(
        catalog=catalog,
        operation_identity=operation_identity,
        profile=_external_http_query_profile(catalog),
        endpoint_url=endpoint.endpoint_template,
        target_code=binding.operation.target_code,
    )


def freeze_wms_effect_status_binding(
    *,
    catalog: WmsProviderCatalog,
    operation_identity: str,
) -> FrozenExternalHttpBinding:
    """只为 compiled profile 中的七项异步 EFFECT 冻结 GET status binding。"""

    binding, endpoint = _compiled_operation_binding(
        catalog=catalog,
        operation_identity=operation_identity,
    )
    if binding.operation.mode.value != "EFFECT" or not binding.operation.supports_status_query:
        raise ValueError("status binding requires an async EFFECT operation")
    if endpoint.status_endpoint is None:
        raise ValueError("async EFFECT compiled profile requires a status endpoint")
    return _freeze_wms_compiled_binding(
        catalog=catalog,
        operation_identity=operation_identity,
        profile=_external_http_effect_status_profile(catalog),
        endpoint_url=endpoint.status_endpoint,
        target_code=binding.operation.target_code,
    )


def freeze_wms_effect_binding(
    *,
    catalog: WmsProviderCatalog,
    profile_identity: str,
    operation_identity: str,
    target_code: str,
) -> FrozenExternalHttpBinding:
    """从当前部署 profile 冻结新 Intent 的 submit target/auth binding。"""

    if profile_identity.strip().lower() != catalog.profile_identity:
        raise ValueError("WMS EFFECT provider profile is not active in this deployment")
    from src.app.sys.services.endpoint_registry import EndpointRegistry

    try:
        endpoint = catalog.compiled_profile.operations[operation_identity]
    except KeyError as exc:
        raise ValueError("WMS EFFECT operation is absent from compiled profile") from exc
    registry = EndpointRegistry({target_code: endpoint.endpoint_template})
    return freeze_external_http_binding(
        profile=_external_http_effect_profile(catalog),
        operation_identity=operation_identity,
        target_code=target_code,
        endpoint_registry=registry,
    )


def resolve_wms_operation_binding(
    *,
    catalog: WmsProviderCatalog,
    profile_identity: str,
    operation_identity: str,
) -> WmsProviderOperationBinding:
    """只在当前部署 active profile 内解析唯一 operation binding。"""

    if profile_identity.strip().lower() != catalog.profile_identity:
        raise LookupError("pinned WMS provider profile is not active in this deployment")
    bindings = tuple(binding for binding in catalog.bindings if binding.operation.identity == operation_identity)
    if len(bindings) != 1:
        raise LookupError("pinned WMS operation binding must resolve exactly once")
    return bindings[0]


__all__ = [
    "WMS_EFFECT_STATUS_HINT_CALLBACK",
    "WMS_TYPED_EFFECT_CALLBACK_TYPES",
    "WmsProviderCatalog",
    "build_wms_provider_catalog",
    "freeze_wms_effect_binding",
    "freeze_wms_effect_status_binding",
    "freeze_wms_query_binding",
    "resolve_wms_operation_binding",
    "validate_wms_transport_configuration",
]
