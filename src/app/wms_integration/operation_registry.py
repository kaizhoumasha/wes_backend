"""WMS 31 项 operation 的唯一静态注册表。"""

from __future__ import annotations

from types import MappingProxyType

from src.app.wms_integration.ports.document_operations import OPERATIONS as DOCUMENT_OPERATIONS
from src.app.wms_integration.ports.fulfillment_operations import OPERATIONS as FULFILLMENT_OPERATIONS
from src.app.wms_integration.ports.inventory_operations import (
    EFFECT_OPERATIONS as INVENTORY_EFFECT_OPERATIONS,
)
from src.app.wms_integration.ports.inventory_operations import (
    QUERY_OPERATIONS as INVENTORY_QUERY_OPERATIONS,
)
from src.app.wms_integration.ports.master_data_operations import OPERATIONS as MASTER_DATA_OPERATIONS
from src.app.wms_integration.ports.reconciliation_operations import OPERATIONS as RECONCILIATION_OPERATIONS

QUERY_OPERATIONS = (
    *MASTER_DATA_OPERATIONS,
    *DOCUMENT_OPERATIONS,
    *INVENTORY_QUERY_OPERATIONS,
    *RECONCILIATION_OPERATIONS,
)
EFFECT_OPERATIONS = (*INVENTORY_EFFECT_OPERATIONS, *FULFILLMENT_OPERATIONS)
WMS_OPERATIONS = (*QUERY_OPERATIONS, *EFFECT_OPERATIONS)
EFFECT_OPERATION_IDENTITIES = frozenset(operation.identity for operation in EFFECT_OPERATIONS)
ASYNC_EFFECT_OPERATIONS = tuple(operation for operation in EFFECT_OPERATIONS if operation.supports_status_query)
ASYNC_EFFECT_OPERATION_IDENTITIES = frozenset(operation.identity for operation in ASYNC_EFFECT_OPERATIONS)

_identities = tuple(operation.identity for operation in WMS_OPERATIONS)
if len(_identities) != 31 or len(_identities) != len(set(_identities)):
    raise RuntimeError("WMS operation registry must contain exactly 31 unique identities")
if len(ASYNC_EFFECT_OPERATIONS) != 4:
    raise RuntimeError("WMS operation registry must contain exactly 4 async EFFECT operations")

WMS_OPERATION_BY_IDENTITY = MappingProxyType({operation.identity: operation for operation in WMS_OPERATIONS})

__all__ = [
    "ASYNC_EFFECT_OPERATIONS",
    "ASYNC_EFFECT_OPERATION_IDENTITIES",
    "EFFECT_OPERATIONS",
    "EFFECT_OPERATION_IDENTITIES",
    "QUERY_OPERATIONS",
    "WMS_OPERATIONS",
    "WMS_OPERATION_BY_IDENTITY",
]
