"""库存查询 author-time operation 声明。"""

from src.app.runtime.system_capabilities.wms.contracts import (
    OutboundAuthScheme,
    WmsHttpMethod,
    WmsOperationContract,
    WmsOperationMode,
    WmsPaginationContract,
    WmsRetryPolicy,
    WmsTransportBudget,
)
from src.app.wms_integration.ports.query_inventory_operation import (
    OPERATION_IDENTITY,
    InventoryQueryOperationRequest,
    InventoryQueryOperationResult,
)

CONTRACT = WmsOperationContract(
    identity=OPERATION_IDENTITY,
    mode=WmsOperationMode.QUERY,
    request_model=InventoryQueryOperationRequest,
    result_model=InventoryQueryOperationResult,
    endpoint_path="/inventory/query",
    target_code="WMS_INVENTORY_QUERY",
    http_method=WmsHttpMethod.GET,
    budget=WmsTransportBudget(
        timeout_seconds=10,
        max_wire_bytes=1_048_576,
        max_decoded_bytes=4_194_304,
        max_rows=10_000,
    ),
    retry_policy=WmsRetryPolicy(max_attempts=3, backoff_seconds=(1, 2)),
    outbound_auth_scheme=OutboundAuthScheme.HMAC_SHA256,
    pagination=WmsPaginationContract(
        request_cursor_field="cursor",
        response_cursor_field="next_cursor",
        response_items_field="items",
        max_pages=100,
    ),
)

__all__ = ["CONTRACT"]
