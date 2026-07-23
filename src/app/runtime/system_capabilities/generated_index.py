"""由 scripts/generate_runtime_extensions.py 生成；禁止手工编辑。"""

from types import MappingProxyType

from src.app.runtime.system_capabilities.device.device_command_write.definition import (
    DEFINITION as _DEFINITION_0,
)
from src.app.runtime.system_capabilities.material_flow.material_unit_write.definition import (
    DEFINITION as _DEFINITION_1,
)
from src.app.runtime.system_capabilities.runtime.session_hold.definition import (
    DEFINITION as _DEFINITION_2,
)
from src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.definition import (
    DEFINITION as _DEFINITION_3,
)
from src.app.runtime.system_capabilities.wms.inventory.query_inventory.definition import (
    DEFINITION as _DEFINITION_4,
)

SYSTEM_CAPABILITY_IDENTITIES = (
    ("device.device_command_write", "v1"),
    ("material_flow.material_unit_write", "v1"),
    ("runtime.session_hold", "v1"),
    ("wms.inventory.confirm_inbound", "v1"),
    ("wms.inventory.query_inventory", "v1"),
)
SYSTEM_CAPABILITY_INDEX_DIGEST = "44871a2f5b463b45b09c01b799c7822de3535bd56d8c4c00aa4cd0b606aece1f"
SYSTEM_CAPABILITY_INDEX = MappingProxyType(
    {
        ("device.device_command_write", "v1"): _DEFINITION_0,
        ("material_flow.material_unit_write", "v1"): _DEFINITION_1,
        ("runtime.session_hold", "v1"): _DEFINITION_2,
        ("wms.inventory.confirm_inbound", "v1"): _DEFINITION_3,
        ("wms.inventory.query_inventory", "v1"): _DEFINITION_4,
    }
)

__all__ = [
    "SYSTEM_CAPABILITY_IDENTITIES",
    "SYSTEM_CAPABILITY_INDEX",
    "SYSTEM_CAPABILITY_INDEX_DIGEST",
]
