"""WMS provider DTO 到稳定 Port contract 的适配层。"""

from .inventory_query_port_adapter import (
    WmsInventoryQueryPortAdapter,
    build_wms_inventory_query_port_factory,
)

__all__ = ["WmsInventoryQueryPortAdapter", "build_wms_inventory_query_port_factory"]
