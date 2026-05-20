"""工作线运行时服务容器。

插件只能通过这个容器访问明确允许的内部领域能力，避免直接依赖 HTTP、
Repository 或 SQL。未注入的能力必须由插件自身使用确定性领域 fallback。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Mapping

    from src.app.resource.services.smt_rack_bin_scheduling_service import SmtRackBinSchedulingDecision


@runtime_checkable
class BinAllocator(Protocol):
    """料箱分配领域服务接口。"""

    def allocate(self, barcode: str) -> Mapping[str, Any] | None:
        """按条码分配料箱。"""

    def plan_allocation(
        self,
        barcode: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> SmtRackBinSchedulingDecision | None:
        """按条码和运行时上下文规划料箱调度。"""


@runtime_checkable
class ActiveRackSnapshotProvider(Protocol):
    """当前 active rack 快照恢复接口。"""

    def active_bin_rack(
        self,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any] | Awaitable[Mapping[str, Any] | None] | None:
        """按运行时上下文恢复当前 active_bin_rack。"""


@dataclass(frozen=True, slots=True)
class WorklineRuntimeServices:
    """插件运行时可访问的领域服务集合。"""

    bin_allocator: BinAllocator | None = None
    active_rack_snapshot_provider: ActiveRackSnapshotProvider | None = None


def build_workline_runtime_services(*, db: Any | None = None, workline: Any | None = None) -> WorklineRuntimeServices:
    """构建当前 worker 使用的运行时服务集合。"""

    from src.app.resource.services.smt_rack_bin_scheduling_service import smt_rack_bin_scheduling_service

    active_rack_snapshot_provider = None
    if db is not None and workline is not None:
        from src.app.resource.services.active_rack_snapshot_service import smt_active_rack_snapshot_service

        active_rack_snapshot_provider = smt_active_rack_snapshot_service.bind(db=db, workline=workline)

    return WorklineRuntimeServices(
        bin_allocator=smt_rack_bin_scheduling_service,
        active_rack_snapshot_provider=active_rack_snapshot_provider,
    )


__all__ = [
    "ActiveRackSnapshotProvider",
    "BinAllocator",
    "WorklineRuntimeServices",
    "build_workline_runtime_services",
]
