"""Workline plugin EXTERNAL_HTTP 的 author-time binding。"""

from src.app.sys.external_http_binding import (
    ExternalHttpBindingDefinition,
    ExternalHttpProviderProfileDefinition,
    FrozenExternalHttpBinding,
    freeze_external_http_binding,
)
from src.app.sys.services.endpoint_registry import EndpointRegistry, endpoint_registry
from src.core.conf import settings
from src.utils.value_normalization import runtime_profile_environment


def _plugin_external_http_profile() -> ExternalHttpProviderProfileDefinition:
    environment = runtime_profile_environment(settings.APP_ENV)
    return ExternalHttpProviderProfileDefinition(
        identity=f"workline.plugin-runtime.v1.{environment}",
        environment=environment,
        bindings=(
            ExternalHttpBindingDefinition(
                operation_identity="workline.external-http.v1",
                allowed_target_codes=(
                    "WMS_RCS_RACK_OPERATION",
                    "WMS_RCS_BIN_OPERATION",
                    "WMS_RCS_FULL_BOX_EXCHANGE",
                ),
                http_method="POST",
                timeout_seconds=30,
                auth_scheme="HMAC_SHA256",
                credential_reference=f"secret://workline/plugin-runtime-{environment}-hmac@v1",
            ),
        ),
    )


def freeze_plugin_external_http_binding(
    target_code: str,
    *,
    registry: EndpointRegistry = endpoint_registry,
) -> FrozenExternalHttpBinding:
    return freeze_external_http_binding(
        profile=_plugin_external_http_profile(),
        operation_identity="workline.external-http.v1",
        target_code=target_code,
        endpoint_registry=registry,
    )


__all__ = ["freeze_plugin_external_http_binding"]
