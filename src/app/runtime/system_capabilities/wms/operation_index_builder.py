"""WMS typed operation 确定性静态索引构建器。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.app.runtime.system_capabilities.wms.contracts import WmsProviderProfile


@dataclass(frozen=True, slots=True)
class GeneratedWmsOperationIndex:
    """可写入 generated_operation_index.py 的确定性产物。"""

    identities: tuple[str, ...]
    digest: str
    source: str


class WmsOperationIndexBuilder:
    """从 typed Provider profile 派生静态 operation index。"""

    @staticmethod
    def build(profile: WmsProviderProfile) -> GeneratedWmsOperationIndex:
        identities = tuple(binding.operation.identity for binding in profile.bindings)
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate WMS operation identity")
        payload = tuple(_contract_payload(binding.operation) for binding in profile.bindings)
        canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        return GeneratedWmsOperationIndex(
            identities=identities,
            digest=digest,
            source=_render_source(digest),
        )


def _contract_payload(operation) -> dict[str, object]:
    return {
        "budget": operation.budget.model_dump(mode="json"),
        "endpoint_path": operation.endpoint_path,
        "http_method": operation.http_method.value,
        "identity": operation.identity,
        "mode": operation.mode.value,
        "outbound_auth_scheme": operation.outbound_auth_scheme.value,
        "request_model": f"{operation.request_model.__module__}.{operation.request_model.__qualname__}",
        "result_model": f"{operation.result_model.__module__}.{operation.result_model.__qualname__}",
        "retry_policy": operation.retry_policy.model_dump(mode="json"),
        "target_code": operation.target_code,
    }


def _render_source(digest: str) -> str:
    return f'''"""由 scripts/generate_wms_operation_index.py 生成；禁止手工编辑。"""

from types import MappingProxyType

from src.app.runtime.system_capabilities.wms.provider_catalog import WMS_PROVIDER_PROFILE

WMS_OPERATION_IDENTITIES = tuple(binding.operation.identity for binding in WMS_PROVIDER_PROFILE.bindings)
WMS_OPERATION_INDEX_DIGEST = "{digest}"
WMS_OPERATION_INDEX = MappingProxyType(
    {{binding.operation.identity: binding.operation for binding in WMS_PROVIDER_PROFILE.bindings}}
)

__all__ = [
    "WMS_OPERATION_IDENTITIES",
    "WMS_OPERATION_INDEX",
    "WMS_OPERATION_INDEX_DIGEST",
]
'''


__all__ = ["GeneratedWmsOperationIndex", "WmsOperationIndexBuilder"]
