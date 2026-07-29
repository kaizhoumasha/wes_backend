"""按静态 execution lane 派生的 WMS Provider readiness 合同。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from src.app.wms_integration.operation_contract import WmsExecutionLane, WmsOperationMode

if TYPE_CHECKING:
    from src.app.wms_integration.endpoint_compiler import CompiledWmsProviderProfile


class WmsProviderProcessRole(str, Enum):
    """实际承载 WMS lane 的部署进程角色。"""

    WES = "wes"
    FULFILLMENT = "fulfillment"

    @property
    def execution_lane(self) -> WmsExecutionLane:
        if self is WmsProviderProcessRole.WES:
            return WmsExecutionLane.WMS_DATA
        return WmsExecutionLane.WMS_FULFILLMENT


@dataclass(frozen=True, slots=True)
class WmsProviderReadiness:
    """进程启动前可核对的纯配置 readiness 快照。"""

    process_role: WmsProviderProcessRole
    execution_lane: WmsExecutionLane
    profile_revision: str
    profile_digest: str
    operation_identities: tuple[str, ...]
    endpoint_keys: tuple[str, ...]
    operation_endpoint_digests: tuple[tuple[str, str], ...]


def build_wms_provider_readiness(
    compiled_profile: CompiledWmsProviderProfile,
    *,
    process_role: WmsProviderProcessRole,
) -> WmsProviderReadiness:
    """只从编译产物的静态 lane 构造 readiness，不创建 runtime transport。"""

    lane = process_role.execution_lane
    operations = tuple(
        operation for operation in compiled_profile.operations.values() if operation.execution_lane is lane
    )
    endpoint_keys: list[str] = []
    for operation in operations:
        endpoint_kind = "query" if operation.mode is WmsOperationMode.QUERY else "submit"
        endpoint_keys.append(f"{operation.identity}:{endpoint_kind}")
        if operation.status_endpoint is not None:
            endpoint_keys.append(f"{operation.identity}:status")
    return WmsProviderReadiness(
        process_role=process_role,
        execution_lane=lane,
        profile_revision=compiled_profile.profile_revision,
        profile_digest=compiled_profile.profile_digest,
        operation_identities=tuple(operation.identity for operation in operations),
        endpoint_keys=tuple(endpoint_keys),
        operation_endpoint_digests=tuple((operation.identity, operation.endpoint_digest) for operation in operations),
    )


__all__ = [
    "WmsProviderProcessRole",
    "WmsProviderReadiness",
    "build_wms_provider_readiness",
]
