"""wms.inventory.query_inventory@v1 System Capability。"""

from src.app.runtime.system_capabilities.wms.query_definition import build_wms_query_capability_definition
from src.app.wms_integration.ports.inventory_operations import QUERY_INVENTORY

DEFINITION = build_wms_query_capability_definition(QUERY_INVENTORY)

__all__ = ["DEFINITION"]
