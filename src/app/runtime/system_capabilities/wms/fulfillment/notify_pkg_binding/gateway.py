"""料盘绑定领域 request 到现有 DispatchEnvelope 的唯一映射。"""

from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.contract import CONTRACT
from src.app.sys.models import DispatchEnvelope, SystemOutboxDispatchType, SystemOutboxTargetType
from src.app.wms_integration.ports.notify_pkg_binding_operation import NotifyPackageBindingOperationRequest

REQUEST_MODEL_MODULE = NotifyPackageBindingOperationRequest.__module__


class NotifyPackageBindingDispatchGateway:
    """构造料盘绑定 EXTERNAL_HTTP 包络，不执行外部 I/O。"""

    def build_envelope(self, request: NotifyPackageBindingOperationRequest) -> DispatchEnvelope:
        return DispatchEnvelope(
            dispatch_key=request.dispatch_key,
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code=CONTRACT.target_code,
            payload_json={
                "package_id": request.package_id,
                "pallet_id": request.pallet_id,
                "station_code": request.station_code,
            },
            operation_domain="WMS_FULFILLMENT",
            operation_key=request.dispatch_key,
            workline_id=request.workline_id,
            session_id=request.session_id,
            trace_id=request.trace_id,
        )


__all__ = ["REQUEST_MODEL_MODULE", "NotifyPackageBindingDispatchGateway"]
