"""满箱交换 author-time operation 声明。"""

from src.app.runtime.system_capabilities.wms.contracts import (
    InboundCallbackContract,
    OutboundAuthScheme,
    WmsHttpMethod,
    WmsOperationContract,
    WmsOperationMode,
    WmsRetryPolicy,
    WmsTransportBudget,
)
from src.app.wms_integration.ports.full_box_exchange_operation import (
    OPERATION_IDENTITY,
    FullBoxExchangeOperationRequest,
    FullBoxExchangeOperationResult,
)

CONTRACT = WmsOperationContract(
    identity=OPERATION_IDENTITY,
    mode=WmsOperationMode.EFFECT,
    request_model=FullBoxExchangeOperationRequest,
    result_model=FullBoxExchangeOperationResult,
    endpoint_path="/fulfillment/full-box-exchange",
    target_code="WMS_FULL_BOX_EXCHANGE",
    http_method=WmsHttpMethod.POST,
    budget=WmsTransportBudget(timeout_seconds=30, max_wire_bytes=262_144, max_decoded_bytes=262_144),
    retry_policy=WmsRetryPolicy(max_attempts=3, backoff_seconds=(1, 4)),
    outbound_auth_scheme=OutboundAuthScheme.HMAC_SHA256,
    supports_status_query=True,
)
CALLBACK_CONTRACT = InboundCallbackContract(
    operation=CONTRACT,
    callback_type="WMS_FULL_BOX_EXCHANGE_COMPLETED",
    payload_model=FullBoxExchangeOperationResult,
)

__all__ = ["CALLBACK_CONTRACT", "CONTRACT"]
