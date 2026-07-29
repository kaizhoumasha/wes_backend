"""由 scripts/generate_runtime_extensions.py 生成；禁止手工编辑。"""

from types import MappingProxyType

from src.app.runtime.system_capabilities.device.device_command_write.definition import (
    DEFINITION as _DEFINITION_0,
)
from src.app.runtime.system_capabilities.material_flow.material_unit_write.definition import (
    DEFINITION as _DEFINITION_1,
)
from src.app.runtime.system_capabilities.material_flow.smt_source_pick_command.definition import (
    DEFINITION as _DEFINITION_2,
)
from src.app.runtime.system_capabilities.material_flow.smt_source_pick_ledger.definition import (
    DEFINITION as _DEFINITION_3,
)
from src.app.runtime.system_capabilities.runtime.session_hold.definition import (
    DEFINITION as _DEFINITION_4,
)
from src.app.runtime.system_capabilities.wms.document.get_grn.definition import (
    DEFINITION as _DEFINITION_5,
)
from src.app.runtime.system_capabilities.wms.document.get_outbound_order.definition import (
    DEFINITION as _DEFINITION_6,
)
from src.app.runtime.system_capabilities.wms.document.get_pick_order.definition import (
    DEFINITION as _DEFINITION_7,
)
from src.app.runtime.system_capabilities.wms.document.get_task_snapshot.definition import (
    DEFINITION as _DEFINITION_8,
)
from src.app.runtime.system_capabilities.wms.document.get_wave.definition import (
    DEFINITION as _DEFINITION_9,
)
from src.app.runtime.system_capabilities.wms.document.list_grn_packages.definition import (
    DEFINITION as _DEFINITION_10,
)
from src.app.runtime.system_capabilities.wms.document.validate_rough_sorter_admission.definition import (
    DEFINITION as _DEFINITION_11,
)
from src.app.runtime.system_capabilities.wms.inventory.get_reservation.definition import (
    DEFINITION as _DEFINITION_12,
)
from src.app.runtime.system_capabilities.wms.inventory.query_inventory.definition import (
    DEFINITION as _DEFINITION_13,
)
from src.app.runtime.system_capabilities.wms.master_data.get_bin.definition import (
    DEFINITION as _DEFINITION_14,
)
from src.app.runtime.system_capabilities.wms.master_data.get_material.definition import (
    DEFINITION as _DEFINITION_15,
)
from src.app.runtime.system_capabilities.wms.master_data.get_rack.definition import (
    DEFINITION as _DEFINITION_16,
)
from src.app.runtime.system_capabilities.wms.master_data.list_locations.definition import (
    DEFINITION as _DEFINITION_17,
)
from src.app.runtime.system_capabilities.wms.master_data.list_materials.definition import (
    DEFINITION as _DEFINITION_18,
)
from src.app.runtime.system_capabilities.wms.master_data.list_racks.definition import (
    DEFINITION as _DEFINITION_19,
)
from src.app.runtime.system_capabilities.wms.master_data.list_zones.definition import (
    DEFINITION as _DEFINITION_20,
)
from src.app.runtime.system_capabilities.wms.reconciliation.check_bin_drift.definition import (
    DEFINITION as _DEFINITION_21,
)
from src.app.runtime.system_capabilities.wms.reconciliation.check_full_drift.definition import (
    DEFINITION as _DEFINITION_22,
)
from src.app.runtime.system_capabilities.wms.reconciliation.check_rack_drift.definition import (
    DEFINITION as _DEFINITION_23,
)

SYSTEM_CAPABILITY_IDENTITIES = (
    ("device.device_command_write", "v1"),
    ("material_flow.material_unit_write", "v1"),
    ("material_flow.smt_source_pick_command", "v1"),
    ("material_flow.smt_source_pick_ledger", "v1"),
    ("runtime.session_hold", "v1"),
    ("wms.document.get_grn", "v1"),
    ("wms.document.get_outbound_order", "v1"),
    ("wms.document.get_pick_order", "v1"),
    ("wms.document.get_task_snapshot", "v1"),
    ("wms.document.get_wave", "v1"),
    ("wms.document.list_grn_packages", "v1"),
    ("wms.document.validate_rough_sorter_admission", "v1"),
    ("wms.inventory.get_reservation", "v1"),
    ("wms.inventory.query_inventory", "v1"),
    ("wms.master_data.get_bin", "v1"),
    ("wms.master_data.get_material", "v1"),
    ("wms.master_data.get_rack", "v1"),
    ("wms.master_data.list_locations", "v1"),
    ("wms.master_data.list_materials", "v1"),
    ("wms.master_data.list_racks", "v1"),
    ("wms.master_data.list_zones", "v1"),
    ("wms.reconciliation.check_bin_drift", "v1"),
    ("wms.reconciliation.check_full_drift", "v1"),
    ("wms.reconciliation.check_rack_drift", "v1"),
)
SYSTEM_CAPABILITY_INDEX_DIGEST = "6dee94983a3c9f6a941e5ed686fe7e3fad0c13994087af78e58e1f7be3e2c813"
SYSTEM_CAPABILITY_INDEX = MappingProxyType(
    {
        ("device.device_command_write", "v1"): _DEFINITION_0,
        ("material_flow.material_unit_write", "v1"): _DEFINITION_1,
        ("material_flow.smt_source_pick_command", "v1"): _DEFINITION_2,
        ("material_flow.smt_source_pick_ledger", "v1"): _DEFINITION_3,
        ("runtime.session_hold", "v1"): _DEFINITION_4,
        ("wms.document.get_grn", "v1"): _DEFINITION_5,
        ("wms.document.get_outbound_order", "v1"): _DEFINITION_6,
        ("wms.document.get_pick_order", "v1"): _DEFINITION_7,
        ("wms.document.get_task_snapshot", "v1"): _DEFINITION_8,
        ("wms.document.get_wave", "v1"): _DEFINITION_9,
        ("wms.document.list_grn_packages", "v1"): _DEFINITION_10,
        ("wms.document.validate_rough_sorter_admission", "v1"): _DEFINITION_11,
        ("wms.inventory.get_reservation", "v1"): _DEFINITION_12,
        ("wms.inventory.query_inventory", "v1"): _DEFINITION_13,
        ("wms.master_data.get_bin", "v1"): _DEFINITION_14,
        ("wms.master_data.get_material", "v1"): _DEFINITION_15,
        ("wms.master_data.get_rack", "v1"): _DEFINITION_16,
        ("wms.master_data.list_locations", "v1"): _DEFINITION_17,
        ("wms.master_data.list_materials", "v1"): _DEFINITION_18,
        ("wms.master_data.list_racks", "v1"): _DEFINITION_19,
        ("wms.master_data.list_zones", "v1"): _DEFINITION_20,
        ("wms.reconciliation.check_bin_drift", "v1"): _DEFINITION_21,
        ("wms.reconciliation.check_full_drift", "v1"): _DEFINITION_22,
        ("wms.reconciliation.check_rack_drift", "v1"): _DEFINITION_23,
    }
)

__all__ = [
    "SYSTEM_CAPABILITY_IDENTITIES",
    "SYSTEM_CAPABILITY_INDEX",
    "SYSTEM_CAPABILITY_INDEX_DIGEST",
]
