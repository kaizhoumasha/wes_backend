"""库存查询直接消费 35 项 registry 中的唯一静态 Definition。"""

from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY

CONTRACT = WMS_OPERATION_BY_IDENTITY["wms.inventory.query_inventory@v1"]

__all__ = ["CONTRACT"]
