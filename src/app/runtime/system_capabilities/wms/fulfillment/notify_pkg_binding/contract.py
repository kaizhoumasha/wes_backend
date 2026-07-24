"""料盘绑定通知 author-time operation 声明。"""

from src.app.runtime.system_capabilities.wms.contracts import (
    InboundCallbackContract,
    OutboundAuthScheme,
    WmsHttpMethod,
    WmsOperationContract,
    WmsOperationMode,
    WmsRetryPolicy,
    WmsTransportBudget,
)
from src.app.wms_integration.ports.notify_pkg_binding_operation import (
    OPERATION_IDENTITY,
    NotifyPackageBindingOperationRequest,
    NotifyPackageBindingOperationResult,
)

CONTRACT = WmsOperationContract(
    identity=OPERATION_IDENTITY,
    mode=WmsOperationMode.EFFECT,
    request_model=NotifyPackageBindingOperationRequest,
    result_model=NotifyPackageBindingOperationResult,
    endpoint_path="/fulfillment/package-binding",
    target_code="WMS_PACKAGE_BINDING",
    http_method=WmsHttpMethod.POST,
    budget=WmsTransportBudget(timeout_seconds=30, max_wire_bytes=262_144, max_decoded_bytes=262_144),
    retry_policy=WmsRetryPolicy(max_attempts=3, backoff_seconds=(1, 4)),
    outbound_auth_scheme=OutboundAuthScheme.HMAC_SHA256,
    supports_status_query=True,
)
CALLBACK_CONTRACT = InboundCallbackContract(
    operation=CONTRACT,
    callback_type="WMS_PACKAGE_BOUND",
    payload_model=NotifyPackageBindingOperationResult,
)

__all__ = ["CALLBACK_CONTRACT", "CONTRACT"]
