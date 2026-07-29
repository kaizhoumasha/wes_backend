# 旧 runtime 镜像实现:src.workline_runtime.services 的平级副本
# 旧 runtime 入口删除后,本模块承载正式实现。
# 自引用 src.workline_runtime.{run_mode, sandbox_catalog} 已重定向到
# src.app.workline.domain.run_mode (stable run-mode mirror)
# + src.app.runtime.orchestration.sandbox_catalog_bridge。

"""工作线运行时服务容器。

插件只能通过这个容器访问明确允许的内部领域能力，避免直接依赖 HTTP、
Repository 或 SQL。未注入的能力必须由插件自身使用确定性领域 fallback。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from src.app.runtime.orchestration.sandbox_catalog_bridge import query_sandbox_wms_inventory_rows
from src.app.workline.domain.run_mode import is_simulation_run_mode
from src.core.conf import settings

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from src.app.resource.models import RackKind
    from src.app.resource.services.smt_rack_bin_scheduling_service import SmtRackBinSchedulingDecision
    from src.app.wms_integration.endpoint_compiler import CompiledWmsProviderProfile
    from src.app.wms_integration.ports.query_inventory_operation import InventoryQueryOperationPort


@runtime_checkable
class BinAllocator(Protocol):
    """料箱分配领域服务接口。"""

    def allocate(self, barcode: str) -> Mapping[str, Any] | None:
        """按条码分配料箱。"""
        ...

    def plan_allocation(
        self,
        barcode: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> SmtRackBinSchedulingDecision | None:
        """按条码和运行时上下文规划料箱调度。"""
        ...


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
class RackOperationStatusProvider(Protocol):
    """货架 operation 派生状态读取接口。"""

    def derive_operation_status(self, operation_key: str) -> Awaitable[str]:
        """按 operation_key 读取派生状态。"""
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


class BoundRackOperationStatusProvider:
    """绑定当前 DB 会话的 operation 状态读取器。"""

    def __init__(self, *, db: Any, service: Any) -> None:
        self._db = db
        self._service = service

    async def derive_operation_status(self, operation_key: str) -> str:
        return str(await self._service.derive_operation_status(self._db, operation_key=operation_key))


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
    """插件运行时可访问的领域服务集合。"""

    bin_allocator: BinAllocator | None = None
    active_rack_snapshot_provider: ActiveRackSnapshotProvider | None = None
    rack_operation_status_provider: RackOperationStatusProvider | None = None
    station_lease_status_provider: StationLeaseStatusProvider | None = None
    inventory_query_port_factory: Callable[[], Callable[[], InventoryQueryOperationPort]] | None = None


def build_workline_runtime_services(
    *,
    db: Any | None = None,
    workline: Any | None = None,
    session: Any | None = None,
    compiled_wms_profile: CompiledWmsProviderProfile | None = None,
) -> WorklineRuntimeServices:
    """构建当前 worker 使用的运行时服务集合。"""

    from src.app.resource.services.smt_rack_bin_scheduling_service import smt_rack_bin_scheduling_service

    active_rack_snapshot_provider = None
    rack_operation_status_provider = None
    station_lease_status_provider = None
    if db is not None and workline is not None:
        from src.app.rack.services import rack_operation_service
        from src.app.resource.services.active_rack_snapshot_service import smt_active_rack_snapshot_service
        from src.app.runtime.capabilities.material_flow.station_lease_service import station_lease_service

        active_rack_snapshot_provider = smt_active_rack_snapshot_service.bind(db=db, workline=workline)
        rack_operation_status_provider = BoundRackOperationStatusProvider(
            db=db,
            service=rack_operation_service,
        )
        station_lease_status_provider = BoundStationLeaseStatusProvider(
            db=db,
            workline=workline,
            service=station_lease_service,
        )

    inventory_query_port_factory = None
    if db is not None:
        from src.app.wms_integration.runtime_factory import build_inventory_query_port_factory

        simulation_requested = is_simulation_run_mode(getattr(workline, "run_mode", None)) or is_simulation_run_mode(
            getattr(session, "run_mode", None)
        )
        if simulation_requested and not settings.WMS_QUERY_IN_PROCESS_SIMULATION_ENABLED:
            raise ValueError("WMS QUERY in-process simulation is disabled for this deployment")
        inventory_query_port_factory = partial(
            build_inventory_query_port_factory,
            simulation=simulation_requested,
            sandbox_rows_provider=query_sandbox_wms_inventory_rows,
            compiled_profile=compiled_wms_profile,
        )

    return WorklineRuntimeServices(
        bin_allocator=smt_rack_bin_scheduling_service,
        active_rack_snapshot_provider=active_rack_snapshot_provider,
        rack_operation_status_provider=rack_operation_status_provider,
        station_lease_status_provider=station_lease_status_provider,
        inventory_query_port_factory=inventory_query_port_factory,
    )


__all__ = [
    "ActiveRackSnapshotProvider",
    "BinAllocator",
    "BoundRackOperationStatusProvider",
    "BoundStationLeaseStatusProvider",
    "RackOperationStatusProvider",
    "StationLeaseStatusProvider",
    "WorklineRuntimeServices",
    "build_workline_runtime_services",
]
