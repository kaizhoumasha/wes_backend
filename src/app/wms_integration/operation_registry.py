"""WMS 35 项 operation 的唯一静态注册表。"""

from __future__ import annotations

from types import MappingProxyType

from src.app.wms_integration.ports.document_operations import ADMISSION_OPERATION, STANDARD_OPERATIONS
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
    *STANDARD_OPERATIONS,
    *INVENTORY_QUERY_OPERATIONS,
    *RECONCILIATION_OPERATIONS,
    ADMISSION_OPERATION,
)
EFFECT_OPERATIONS = (*INVENTORY_EFFECT_OPERATIONS, *FULFILLMENT_OPERATIONS)
WMS_OPERATIONS = (*QUERY_OPERATIONS, *EFFECT_OPERATIONS)

_identities = tuple(operation.identity for operation in WMS_OPERATIONS)
if len(_identities) != 35 or len(_identities) != len(set(_identities)):
    raise RuntimeError("WMS operation registry must contain exactly 35 unique identities")

WMS_OPERATION_BY_IDENTITY = MappingProxyType({operation.identity: operation for operation in WMS_OPERATIONS})

__all__ = [
    "EFFECT_OPERATIONS",
    "QUERY_OPERATIONS",
    "WMS_OPERATIONS",
    "WMS_OPERATION_BY_IDENTITY",
]
