"""入库确认 author-time operation 声明。"""

from src.app.runtime.system_capabilities.wms.contracts import (
    OutboundAuthScheme,
    WmsHttpMethod,
    WmsOperationContract,
    WmsOperationMode,
    WmsRetryPolicy,
    WmsTransportBudget,
)
from src.app.wms_integration.ports.confirm_inbound_operation import (
    OPERATION_IDENTITY,
    ConfirmInboundOperationRequest,
    ConfirmInboundOperationResult,
)

CONTRACT = WmsOperationContract(
    identity=OPERATION_IDENTITY,
    mode=WmsOperationMode.EFFECT,
    request_model=ConfirmInboundOperationRequest,
    result_model=ConfirmInboundOperationResult,
    endpoint_path="/inventory/confirm-inbound",
    target_code="WMS_INBOUND_CONFIRM",
    http_method=WmsHttpMethod.POST,
    budget=WmsTransportBudget(timeout_seconds=30, max_wire_bytes=262_144, max_decoded_bytes=262_144),
    retry_policy=WmsRetryPolicy(max_attempts=3, backoff_seconds=(1, 4)),
    outbound_auth_scheme=OutboundAuthScheme.HMAC_SHA256,
    supports_status_query=True,
)
__all__ = ["CONTRACT"]
