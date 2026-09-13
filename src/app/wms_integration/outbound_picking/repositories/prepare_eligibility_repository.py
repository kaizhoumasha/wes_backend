"""PickingTask prepare 的 WorkLine 静态装配事实读取。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wes_plugin_sdk.prepare_policy import PrepareRuntimeFacts

from src.app.workline.repositories import WorkLineRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class PickingWorklineFactsRepository:
    """从 WorkLine 冻结绑定构造不可变事实，不判断业务准入。"""

    def __init__(
        self,
        *,
        workline_repository: WorkLineRepository | None = None,
    ) -> None:
        self._worklines = workline_repository or WorkLineRepository()

    async def read_facts(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
    ) -> PrepareRuntimeFacts:
        bindings = await self._worklines.list_bindings(db, workline_id)
        position_bindings = await self._worklines.list_position_bindings(db, workline_id)
        return PrepareRuntimeFacts(
            device_roles=tuple(binding.device_role for binding in bindings),
            position_roles=tuple(binding.position_role for binding in position_bindings),
        )


picking_workline_facts_repository = PickingWorklineFactsRepository()

__all__ = ["PickingWorklineFactsRepository", "picking_workline_facts_repository"]
