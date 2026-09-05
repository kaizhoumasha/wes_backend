"""粗分业务仍占用工作线位置资源时阻止停用或切换。"""

from __future__ import annotations

from typing import Any, Protocol, cast

from src.app.execution.repositories.position_projection_repository import position_projection_repository
from src.app.resource.repositories import bin_placement_repository, rack_placement_repository


class PlacementRepositoryPort(Protocol):
    async def get_active_workline_summary(self, db: Any, workline_id: int) -> dict[str, Any]: ...


_DEFAULT_RACK_PLACEMENTS = cast("PlacementRepositoryPort", rack_placement_repository)
_DEFAULT_BIN_PLACEMENTS = cast("PlacementRepositoryPort", bin_placement_repository)
_DEFAULT_POSITION_PROJECTIONS = cast("PlacementRepositoryPort", position_projection_repository)


class RoughSorterBusinessBlocker:
    """汇总粗分插件拥有的 active 位置投影。"""

    def __init__(
        self,
        *,
        rack_placements: PlacementRepositoryPort = _DEFAULT_RACK_PLACEMENTS,
        bin_placements: PlacementRepositoryPort = _DEFAULT_BIN_PLACEMENTS,
        position_projections: PlacementRepositoryPort = _DEFAULT_POSITION_PROJECTIONS,
    ) -> None:
        self._rack_placements = rack_placements
        self._bin_placements = bin_placements
        self._position_projections = position_projections

    async def get_unfinished_workload_summary(self, db: Any, workline_id: int) -> dict[str, Any]:
        rack = await self._rack_placements.get_active_workline_summary(db, workline_id)
        bin_ = await self._bin_placements.get_active_workline_summary(db, workline_id)
        projection = await self._position_projections.get_active_workline_summary(db, workline_id)
        by_type = {
            "rack_placements": int(rack["count"]),
            "bin_placements": int(bin_["count"]),
            "position_projections": int(projection["count"]),
        }
        samples = {
            owner: summary["sample"]
            for owner, summary in (
                ("rack_placements", rack),
                ("bin_placements", bin_),
                ("position_projections", projection),
            )
            if summary["sample"] is not None
        }
        return {
            "count": sum(by_type.values()),
            "by_type": by_type,
            "sample": next(iter(samples.values()), None),
            "samples": samples,
        }


__all__ = ["RoughSorterBusinessBlocker"]
