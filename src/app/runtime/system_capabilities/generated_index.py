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
from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.definition import (
    DEFINITION as _DEFINITION_3,
)

SYSTEM_CAPABILITY_IDENTITIES = (
    ("device.device_command_write", "v1"),
    ("material_flow.material_unit_write", "v1"),
    ("runtime.session_hold", "v1"),
    ("wms.rough_sorter_inventory_admission", "v1"),
)
SYSTEM_CAPABILITY_INDEX_DIGEST = "165471b480afa7161fbf7b392922adf10fbecd070a4c98bd26c4578b00bbc0b6"
SYSTEM_CAPABILITY_INDEX = MappingProxyType(
    {
        ("device.device_command_write", "v1"): _DEFINITION_0,
        ("material_flow.material_unit_write", "v1"): _DEFINITION_1,
        ("runtime.session_hold", "v1"): _DEFINITION_2,
        ("wms.rough_sorter_inventory_admission", "v1"): _DEFINITION_3,
    }
)

__all__ = [
    "SYSTEM_CAPABILITY_IDENTITIES",
    "SYSTEM_CAPABILITY_INDEX",
    "SYSTEM_CAPABILITY_INDEX_DIGEST",
]
