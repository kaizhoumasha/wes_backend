"""Workline plugin EXTERNAL_HTTP 的 author-time binding。"""

from src.app.sys.external_http_binding import (
    ExternalHttpBindingDefinition,
    ExternalHttpProviderProfileDefinition,
    FrozenExternalHttpBinding,
    freeze_external_http_binding,
)
from src.app.sys.services.endpoint_registry import EndpointRegistry, endpoint_registry

PLUGIN_EXTERNAL_HTTP_PROFILE = ExternalHttpProviderProfileDefinition(
    identity="workline.plugin-runtime.v1",
    bindings=(
        ExternalHttpBindingDefinition(
            operation_identity="workline.external-http.v1",
            allowed_target_codes=(
                "WMS_FULFILLMENT",
                "WMS_INVENTORY_TRANSACTION",
                "WMS_RCS_RACK_OPERATION",
                "WMS_RCS_BIN_OPERATION",
                "WMS_RCS_FULL_BOX_EXCHANGE",
            ),
            http_method="POST",
            timeout_seconds=30,
            auth_scheme="HMAC_SHA256",
            credential_reference="secret://workline/plugin-runtime-hmac@v1",
        ),
    ),
)


def freeze_plugin_external_http_binding(
    target_code: str,
    *,
    registry: EndpointRegistry = endpoint_registry,
) -> FrozenExternalHttpBinding:
    return freeze_external_http_binding(
        profile=PLUGIN_EXTERNAL_HTTP_PROFILE,
        operation_identity="workline.external-http.v1",
        target_code=target_code,
        endpoint_registry=registry,
    )


__all__ = ["PLUGIN_EXTERNAL_HTTP_PROFILE", "freeze_plugin_external_http_binding"]
