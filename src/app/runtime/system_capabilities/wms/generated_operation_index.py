"""WMS runtime 对静态 35 项 registry 的只读索引视图。"""

from __future__ import annotations

import hashlib
import json

from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY, WMS_OPERATIONS

WMS_OPERATION_IDENTITIES = tuple(operation.identity for operation in WMS_OPERATIONS)
WMS_OPERATION_INDEX = WMS_OPERATION_BY_IDENTITY
_digest_payload = tuple(
    {
        "budget": operation.budget.model_dump(mode="json"),
        "completion_mode": operation.completion_mode.value if operation.completion_mode is not None else None,
        "error_codes": operation.error_codes,
        "execution_lane": operation.execution_lane.value,
        "http_method": operation.http_method.value,
        "identity": operation.identity,
        "mode": operation.mode.value,
        "pagination": operation.pagination.model_dump(mode="json") if operation.pagination is not None else None,
        "path_template": operation.path_template,
        "reject_codes": operation.reject_codes,
        "request_model": f"{operation.request_model.__module__}.{operation.request_model.__qualname__}",
        "result_model": f"{operation.result_model.__module__}.{operation.result_model.__qualname__}",
        "target_code": operation.target_code,
    }
    for operation in WMS_OPERATIONS
)
WMS_OPERATION_INDEX_DIGEST = hashlib.sha256(
    json.dumps(_digest_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
).hexdigest()

__all__ = [
    "WMS_OPERATION_IDENTITIES",
    "WMS_OPERATION_INDEX",
    "WMS_OPERATION_INDEX_DIGEST",
]
