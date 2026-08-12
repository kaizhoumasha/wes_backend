"""WMS runtime 对静态 29 项 registry 的只读索引视图。"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY, WMS_OPERATIONS

if TYPE_CHECKING:
    from src.app.wms_integration.operation_contract import WmsOperationDefinition

WMS_OPERATION_IDENTITIES = tuple(operation.identity for operation in WMS_OPERATIONS)
WMS_OPERATION_INDEX = WMS_OPERATION_BY_IDENTITY


def _operation_index_digest(operations: tuple[WmsOperationDefinition, ...]) -> str:
    """摘要覆盖全部冻结 Definition 字段，任何 provider-visible 漂移都必须改变索引身份。"""

    digest_payload = tuple(
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
            "request_schema": operation.request_model.model_json_schema(),
            "result_schema": operation.result_model.model_json_schema(),
            "target_code": operation.target_code,
        }
        for operation in operations
    )
    return hashlib.sha256(
        json.dumps(digest_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


WMS_OPERATION_INDEX_DIGEST = _operation_index_digest(WMS_OPERATIONS)

__all__ = [
    "WMS_OPERATION_IDENTITIES",
    "WMS_OPERATION_INDEX",
    "WMS_OPERATION_INDEX_DIGEST",
]
