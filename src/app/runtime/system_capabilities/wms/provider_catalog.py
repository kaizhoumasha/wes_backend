"""WMS Provider profile 与 typed operation 的 author-time 组合真源。"""

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

WMS_NORTHBOUND_IDENTITY = ExternalContractProfile(
    provider_code="WMS",
    contract_version="northbound.v1",
    environment="production",
)
WMS_NORTHBOUND_AUTH = OutboundAuthProfile(
    scheme=OutboundAuthScheme.HMAC_SHA256,
    credential_reference="secret://wms/northbound-hmac@v1",
)


def _binding(operation):
    return WmsProviderOperationBinding(
        profile=WMS_NORTHBOUND_IDENTITY,
        operation=operation,
        outbound_auth=WMS_NORTHBOUND_AUTH,
    )


WMS_PROVIDER_PROFILE = WmsProviderProfile(
    identity=WMS_NORTHBOUND_IDENTITY,
    bindings=tuple(
        _binding(operation)
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

__all__ = ["WMS_NORTHBOUND_AUTH", "WMS_NORTHBOUND_IDENTITY", "WMS_PROVIDER_PROFILE"]
