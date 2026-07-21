"""由 scripts/generate_wms_operation_index.py 生成；禁止手工编辑。"""

from types import MappingProxyType

from src.app.runtime.system_capabilities.wms.provider_catalog import WMS_PROVIDER_PROFILE

WMS_OPERATION_IDENTITIES = tuple(binding.operation.identity for binding in WMS_PROVIDER_PROFILE.bindings)
WMS_OPERATION_INDEX_DIGEST = "3782ce41aee2041d88328816b23fa29338259b3484dbe3cae6234705afd639e1"
WMS_OPERATION_INDEX = MappingProxyType(
    {binding.operation.identity: binding.operation for binding in WMS_PROVIDER_PROFILE.bindings}
)

__all__ = [
    "WMS_OPERATION_IDENTITIES",
    "WMS_OPERATION_INDEX",
    "WMS_OPERATION_INDEX_DIGEST",
]
