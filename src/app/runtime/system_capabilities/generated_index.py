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
from src.app.runtime.system_capabilities.wms.document.get_grn.definition import (
    DEFINITION as _DEFINITION_3,
)
from src.app.runtime.system_capabilities.wms.document.get_outbound_order.definition import (
    DEFINITION as _DEFINITION_4,
)
from src.app.runtime.system_capabilities.wms.document.get_pick_order.definition import (
    DEFINITION as _DEFINITION_5,
)
from src.app.runtime.system_capabilities.wms.document.get_task_snapshot.definition import (
    DEFINITION as _DEFINITION_6,
)
from src.app.runtime.system_capabilities.wms.document.get_wave.definition import (
    DEFINITION as _DEFINITION_7,
)
from src.app.runtime.system_capabilities.wms.document.list_grn_packages.definition import (
    DEFINITION as _DEFINITION_8,
)
from src.app.runtime.system_capabilities.wms.fulfillment.cancel_request.definition import (
    DEFINITION as _DEFINITION_9,
)
from src.app.runtime.system_capabilities.wms.fulfillment.change_rack_face.definition import (
    DEFINITION as _DEFINITION_10,
)
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.definition import (
    DEFINITION as _DEFINITION_11,
)
from src.app.runtime.system_capabilities.wms.fulfillment.publish_manual_task.definition import (
    DEFINITION as _DEFINITION_12,
)
from src.app.runtime.system_capabilities.wms.fulfillment.request_rack_supply.definition import (
    DEFINITION as _DEFINITION_13,
)
from src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.definition import (
    DEFINITION as _DEFINITION_14,
)
from src.app.runtime.system_capabilities.wms.inventory.confirm_outbound.definition import (
    DEFINITION as _DEFINITION_15,
)
from src.app.runtime.system_capabilities.wms.inventory.confirm_return_putaway.definition import (
    DEFINITION as _DEFINITION_16,
)
from src.app.runtime.system_capabilities.wms.inventory.get_reservation.definition import (
    DEFINITION as _DEFINITION_17,
)
from src.app.runtime.system_capabilities.wms.inventory.query_inventory.definition import (
    DEFINITION as _DEFINITION_18,
)
from src.app.runtime.system_capabilities.wms.inventory.release_reservation.definition import (
    DEFINITION as _DEFINITION_19,
)
from src.app.runtime.system_capabilities.wms.inventory.reserve_inventory.definition import (
    DEFINITION as _DEFINITION_20,
)
from src.app.runtime.system_capabilities.wms.inventory.transfer_inventory.definition import (
    DEFINITION as _DEFINITION_21,
)
from src.app.runtime.system_capabilities.wms.master_data.get_bin.definition import (
    DEFINITION as _DEFINITION_22,
)
from src.app.runtime.system_capabilities.wms.master_data.get_material.definition import (
    DEFINITION as _DEFINITION_23,
)
from src.app.runtime.system_capabilities.wms.master_data.get_rack.definition import (
    DEFINITION as _DEFINITION_24,
)
from src.app.runtime.system_capabilities.wms.master_data.list_locations.definition import (
    DEFINITION as _DEFINITION_25,
)
from src.app.runtime.system_capabilities.wms.master_data.list_materials.definition import (
    DEFINITION as _DEFINITION_26,
)
from src.app.runtime.system_capabilities.wms.master_data.list_racks.definition import (
    DEFINITION as _DEFINITION_27,
)
from src.app.runtime.system_capabilities.wms.master_data.list_zones.definition import (
    DEFINITION as _DEFINITION_28,
)
from src.app.runtime.system_capabilities.wms.reconciliation.check_bin_drift.definition import (
    DEFINITION as _DEFINITION_29,
)
from src.app.runtime.system_capabilities.wms.reconciliation.check_full_drift.definition import (
    DEFINITION as _DEFINITION_30,
)
from src.app.runtime.system_capabilities.wms.reconciliation.check_rack_drift.definition import (
    DEFINITION as _DEFINITION_31,
)

SYSTEM_CAPABILITY_IDENTITIES = (
    ("device.device_command_write", "v1"),
    ("material_flow.material_unit_write", "v1"),
    ("runtime.session_hold", "v1"),
    ("wms.document.get_grn", "v1"),
    ("wms.document.get_outbound_order", "v1"),
    ("wms.document.get_pick_order", "v1"),
    ("wms.document.get_task_snapshot", "v1"),
    ("wms.document.get_wave", "v1"),
    ("wms.document.list_grn_packages", "v1"),
    ("wms.fulfillment.cancel_request", "v1"),
    ("wms.fulfillment.change_rack_face", "v1"),
    ("wms.fulfillment.notify_pkg_binding", "v1"),
    ("wms.fulfillment.publish_manual_task", "v1"),
    ("wms.fulfillment.request_rack_supply", "v1"),
    ("wms.inventory.confirm_inbound", "v1"),
    ("wms.inventory.confirm_outbound", "v1"),
    ("wms.inventory.confirm_return_putaway", "v1"),
    ("wms.inventory.get_reservation", "v1"),
    ("wms.inventory.query_inventory", "v1"),
    ("wms.inventory.release_reservation", "v1"),
    ("wms.inventory.reserve_inventory", "v1"),
    ("wms.inventory.transfer_inventory", "v1"),
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
SYSTEM_CAPABILITY_INDEX_DIGEST = "24fdbe3b8281ea30a374dad9acfbc227d5f3e50d99b74dc0face37ca99db1d63"
SYSTEM_CAPABILITY_INDEX = MappingProxyType(
    {
        ("device.device_command_write", "v1"): _DEFINITION_0,
        ("material_flow.material_unit_write", "v1"): _DEFINITION_1,
        ("runtime.session_hold", "v1"): _DEFINITION_2,
        ("wms.document.get_grn", "v1"): _DEFINITION_3,
        ("wms.document.get_outbound_order", "v1"): _DEFINITION_4,
        ("wms.document.get_pick_order", "v1"): _DEFINITION_5,
        ("wms.document.get_task_snapshot", "v1"): _DEFINITION_6,
        ("wms.document.get_wave", "v1"): _DEFINITION_7,
        ("wms.document.list_grn_packages", "v1"): _DEFINITION_8,
        ("wms.fulfillment.cancel_request", "v1"): _DEFINITION_9,
        ("wms.fulfillment.change_rack_face", "v1"): _DEFINITION_10,
        ("wms.fulfillment.notify_pkg_binding", "v1"): _DEFINITION_11,
        ("wms.fulfillment.publish_manual_task", "v1"): _DEFINITION_12,
        ("wms.fulfillment.request_rack_supply", "v1"): _DEFINITION_13,
        ("wms.inventory.confirm_inbound", "v1"): _DEFINITION_14,
        ("wms.inventory.confirm_outbound", "v1"): _DEFINITION_15,
        ("wms.inventory.confirm_return_putaway", "v1"): _DEFINITION_16,
        ("wms.inventory.get_reservation", "v1"): _DEFINITION_17,
        ("wms.inventory.query_inventory", "v1"): _DEFINITION_18,
        ("wms.inventory.release_reservation", "v1"): _DEFINITION_19,
        ("wms.inventory.reserve_inventory", "v1"): _DEFINITION_20,
        ("wms.inventory.transfer_inventory", "v1"): _DEFINITION_21,
        ("wms.master_data.get_bin", "v1"): _DEFINITION_22,
        ("wms.master_data.get_material", "v1"): _DEFINITION_23,
        ("wms.master_data.get_rack", "v1"): _DEFINITION_24,
        ("wms.master_data.list_locations", "v1"): _DEFINITION_25,
        ("wms.master_data.list_materials", "v1"): _DEFINITION_26,
        ("wms.master_data.list_racks", "v1"): _DEFINITION_27,
        ("wms.master_data.list_zones", "v1"): _DEFINITION_28,
        ("wms.reconciliation.check_bin_drift", "v1"): _DEFINITION_29,
        ("wms.reconciliation.check_full_drift", "v1"): _DEFINITION_30,
        ("wms.reconciliation.check_rack_drift", "v1"): _DEFINITION_31,
    }
)

__all__ = [
    "SYSTEM_CAPABILITY_IDENTITIES",
    "SYSTEM_CAPABILITY_INDEX",
    "SYSTEM_CAPABILITY_INDEX_DIGEST",
]
