"""由 scripts/generate_runtime_extensions.py 生成；禁止手工编辑。"""

from types import MappingProxyType

from src.app.runtime.system_capabilities.device.device_command_write.definition import (
    DEFINITION as _DEFINITION_0,
)
from src.app.runtime.system_capabilities.material_flow.material_unit_write.definition import (
    DEFINITION as _DEFINITION_1,
)
from src.app.runtime.system_capabilities.material_flow.smt_source_pick_ledger.definition import (
    DEFINITION as _DEFINITION_2,
)
from src.app.runtime.system_capabilities.runtime.session_hold.definition import (
    DEFINITION as _DEFINITION_3,
)
from src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.definition import (
    DEFINITION as _DEFINITION_4,
)
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.definition import (
    DEFINITION as _DEFINITION_5,
)
from src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.definition import (
    DEFINITION as _DEFINITION_6,
)
from src.app.runtime.system_capabilities.wms.inventory.query_inventory.definition import (
    DEFINITION as _DEFINITION_7,
)

SYSTEM_CAPABILITY_IDENTITIES = (
    ("device.device_command_write", "v1"),
    ("material_flow.material_unit_write", "v1"),
    ("material_flow.smt_source_pick_ledger", "v1"),
    ("runtime.session_hold", "v1"),
    ("wms.fulfillment.full_box_exchange", "v1"),
    ("wms.fulfillment.notify_pkg_binding", "v1"),
    ("wms.inventory.confirm_inbound", "v1"),
    ("wms.inventory.query_inventory", "v1"),
)
SYSTEM_CAPABILITY_INDEX_DIGEST = "1393602f121e640bfe565149f5b1eb15df97819c8e43402ed2e8c8071e863a55"
SYSTEM_CAPABILITY_INDEX = MappingProxyType(
    {
        ("device.device_command_write", "v1"): _DEFINITION_0,
        ("material_flow.material_unit_write", "v1"): _DEFINITION_1,
        ("material_flow.smt_source_pick_ledger", "v1"): _DEFINITION_2,
        ("runtime.session_hold", "v1"): _DEFINITION_3,
        ("wms.fulfillment.full_box_exchange", "v1"): _DEFINITION_4,
        ("wms.fulfillment.notify_pkg_binding", "v1"): _DEFINITION_5,
        ("wms.inventory.confirm_inbound", "v1"): _DEFINITION_6,
        ("wms.inventory.query_inventory", "v1"): _DEFINITION_7,
    }
)

__all__ = [
    "SYSTEM_CAPABILITY_IDENTITIES",
    "SYSTEM_CAPABILITY_INDEX",
    "SYSTEM_CAPABILITY_INDEX_DIGEST",
]
