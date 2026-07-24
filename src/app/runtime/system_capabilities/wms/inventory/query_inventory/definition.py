"""通用 inventory QUERY System Capability Definition。"""

from src.app.runtime.system_capabilities.definition import (
    EffectCompletionMode,
    SystemCapabilityDefinition,
    SystemCapabilityMode,
)
from src.app.runtime.system_capabilities.wms.provider_catalog import WMS_MATERIAL_FLOW_CONTRACT_VERSION
from src.app.wms_integration.ports.query_inventory_operation import (
    OPERATION_IDENTITY,
    InventoryQueryOperationPort,
    InventoryQueryOperationRequest,
    InventoryQueryOperationResult,
)

from .handler import InventoryQueryCapabilityHandler

CAPABILITY_KEY, CONTRACT_VERSION = OPERATION_IDENTITY.rsplit("@", maxsplit=1)

DEFINITION = SystemCapabilityDefinition(
    capability_key=CAPABILITY_KEY,
    contract_version=CONTRACT_VERSION,
    mode=SystemCapabilityMode.QUERY,
    input_model=InventoryQueryOperationRequest,
    output_model=InventoryQueryOperationResult,
    handler_factory=InventoryQueryCapabilityHandler,
    required_ports=(InventoryQueryOperationPort,),
    admission=f"wms.{WMS_MATERIAL_FLOW_CONTRACT_VERSION}",
    timeout_seconds=10,
    completion_mode=EffectCompletionMode.LOCAL_TRANSACTIONAL,
    audit_policy="metadata",
)

__all__ = ["CAPABILITY_KEY", "CONTRACT_VERSION", "DEFINITION"]
