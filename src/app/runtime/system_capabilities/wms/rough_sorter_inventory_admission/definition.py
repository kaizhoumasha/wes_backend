"""粗分机 WMS 库存准入静态 Definition。"""

from src.app.runtime.system_capabilities.definition import (
    EffectCompletionMode,
    SystemCapabilityDefinition,
    SystemCapabilityMode,
)
from src.app.wms_integration.ports.inventory_query import WmsInventoryQueryPort

from .contracts import (
    PROFILE_FAMILY,
    PROFILE_IDENTITY,
    RoughSorterInventoryAdmissionInput,
    RoughSorterInventoryAdmissionOutput,
)
from .handler import RoughSorterInventoryAdmissionHandler

DEFINITION = SystemCapabilityDefinition(
    capability_key="wms.rough_sorter_inventory_admission",
    contract_version="v1",
    mode=SystemCapabilityMode.QUERY,
    input_model=RoughSorterInventoryAdmissionInput,
    output_model=RoughSorterInventoryAdmissionOutput,
    handler_factory=RoughSorterInventoryAdmissionHandler,
    required_ports=(WmsInventoryQueryPort,),
    # Definition 固定合同族，binding 再按部署环境固定具体 profile identity。
    admission=PROFILE_FAMILY,
    timeout_seconds=10,
    completion_mode=EffectCompletionMode.LOCAL_TRANSACTIONAL,
    audit_policy="metadata",
)

__all__ = ["DEFINITION", "PROFILE_IDENTITY"]
