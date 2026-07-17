"""由 scripts/generate_runtime_extensions.py 生成；禁止手工编辑。"""

from types import MappingProxyType

from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.definition import (
    DEFINITION as _DEFINITION_0,
)

SYSTEM_CAPABILITY_IDENTITIES = (("wms.rough_sorter_inventory_admission", "v1"),)
SYSTEM_CAPABILITY_INDEX_DIGEST = "50d15c853cb7d91d47a013772dde0361d5011e687a76e4c7dc2af9ee459e35bc"
SYSTEM_CAPABILITY_INDEX = MappingProxyType(
    {
        ("wms.rough_sorter_inventory_admission", "v1"): _DEFINITION_0,
    }
)

__all__ = [
    "SYSTEM_CAPABILITY_IDENTITIES",
    "SYSTEM_CAPABILITY_INDEX",
    "SYSTEM_CAPABILITY_INDEX_DIGEST",
]
