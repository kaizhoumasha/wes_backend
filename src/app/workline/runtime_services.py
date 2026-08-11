# WorkLine 运行时基础服务的正式装配入口。

"""工作线运行时服务容器。

运行时基础能力通过这个容器显式装配，避免业务规则直接依赖 HTTP、Repository 或 SQL。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from src.app.resource.models import RackKind
    from src.app.runtime.system_capabilities.definition import SystemCapabilityDefinition
    from src.app.wms_integration.ports.effect_preparation import WmsEffectPreparationPort
    from src.app.wms_integration.ports.query_execution import WmsQueryExecutionPort


@runtime_checkable
class ActiveRackSnapshotProvider(Protocol):
    """当前 active rack 快照恢复接口。"""

    def active_bin_rack(
        self,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any] | Awaitable[Mapping[str, Any] | None] | None:
        """按运行时上下文恢复当前 active_bin_rack。"""
        ...


@runtime_checkable
class StationLeaseStatusProvider(Protocol):
    """Station lease 状态读取接口。"""

    def station_lease_status(
        self,
        position_code: str,
        *,
        rack_kind: RackKind | None = None,
        allow_active_rack_bound: bool = False,
    ) -> Awaitable[Any]:
        """按 position_code 读取当前 Station lease 状态。"""
        ...


class BoundStationLeaseStatusProvider:
    """绑定当前 DB 会话和工作线的 Station lease 状态读取器。"""

    def __init__(self, *, db: Any, workline: Any, service: Any) -> None:
        self._db = db
        self._workline = workline
        self._service = service

    async def station_lease_status(
        self,
        position_code: str,
        *,
        rack_kind: RackKind | None = None,
        allow_active_rack_bound: bool = False,
    ) -> Any:
        from src.app.resource.models import RackKind

        return await self._service.get_station_lease_status(
            self._db,
            workline_id=self._workline.id,
            workline_code=self._workline.line_code,
            position_code=position_code,
            rack_kind=rack_kind or RackKind.SINGLE_LAYER,
            allow_active_rack_bound=allow_active_rack_bound,
        )


@dataclass(frozen=True, slots=True)
class WorklineRuntimeServices:
    """核心运行时可访问的基础能力集合。"""

    active_rack_snapshot_provider: ActiveRackSnapshotProvider | None = None
    station_lease_status_provider: StationLeaseStatusProvider | None = None
    wms_query_execution_port: WmsQueryExecutionPort | None = None
    wms_effect_preparation_port: WmsEffectPreparationPort | None = None
    allow_new_system_capability_claim: Callable[[SystemCapabilityDefinition], bool] | None = None


def build_workline_runtime_services(
    *,
    db: Any | None = None,
    workline: Any | None = None,
    session: Any | None = None,
    wms_query_execution_port: WmsQueryExecutionPort | None = None,
    wms_effect_preparation_port: WmsEffectPreparationPort | None = None,
) -> WorklineRuntimeServices:
    """构建当前 worker 使用的运行时服务集合。"""

    del session
    active_rack_snapshot_provider = None
    station_lease_status_provider = None
    if db is not None and workline is not None:
        from src.app.resource.services.active_rack_snapshot_service import smt_active_rack_snapshot_service
        from src.app.runtime.capabilities.material_flow.station_lease_service import station_lease_service

        active_rack_snapshot_provider = smt_active_rack_snapshot_service.bind(db=db, workline=workline)
        station_lease_status_provider = BoundStationLeaseStatusProvider(
            db=db,
            workline=workline,
            service=station_lease_service,
        )

    if wms_query_execution_port is None and db is not None:
        from src.app.wms_integration.query_runtime import get_wms_data_lane_query_runtime

        wms_query_execution_port = get_wms_data_lane_query_runtime()
    if wms_effect_preparation_port is None and db is not None:
        from src.app.wms_integration.effect_preparation_runtime import get_wms_effect_preparation_runtime

        wms_effect_preparation_port = get_wms_effect_preparation_runtime()
    allow_new_claim = getattr(wms_effect_preparation_port, "allow_new_claim", None)
    if wms_effect_preparation_port is not None and not callable(allow_new_claim):
        raise RuntimeError("bound WMS EFFECT preparation port requires callable allow_new_claim policy")

    return WorklineRuntimeServices(
        active_rack_snapshot_provider=active_rack_snapshot_provider,
        station_lease_status_provider=station_lease_status_provider,
        wms_query_execution_port=wms_query_execution_port,
        wms_effect_preparation_port=wms_effect_preparation_port,
        allow_new_system_capability_claim=allow_new_claim,
    )


__all__ = [
    "ActiveRackSnapshotProvider",
    "BoundStationLeaseStatusProvider",
    "StationLeaseStatusProvider",
    "WorklineRuntimeServices",
    "build_workline_runtime_services",
]
