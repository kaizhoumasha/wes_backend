"""粗分机 Q19 首次准入事实 Repository。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.repositories.session_mutation_repository import (
    SessionMutationRepository,
    session_mutation_repository,
)
from src.app.runtime.orchestration.repositories.session_repository import (
    WorklineSessionRepository,
    workline_session_repository,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.runtime.capabilities.material_flow.contracts.rough_sorter_context import (
        RoughSorterQ19AdmissionDecision,
    )
    from src.app.runtime.orchestration.models.session import WorklineSession


class RoughSorterQ19AdmissionRepository:
    """封装 Session 行锁与 context_json 写入，Service 不接触 ORM 细节。"""

    def __init__(
        self,
        *,
        session_repository: WorklineSessionRepository = workline_session_repository,
        mutation_repository: SessionMutationRepository = session_mutation_repository,
    ) -> None:
        self._session_repository = session_repository
        self._mutation_repository = mutation_repository

    async def get_for_update(self, db: AsyncSession, session_id: int) -> WorklineSession | None:
        return await self._session_repository.get_for_update(db, session_id, populate_existing=True)

    @staticmethod
    def load_decision(session: WorklineSession) -> Any | None:
        context = session.context_json
        if not isinstance(context, dict):
            raise TypeError("Q19 admission requires an object session context")
        return context.get("wms_admission_decision")

    async def persist_decision(
        self,
        db: AsyncSession,
        session: WorklineSession,
        decision: RoughSorterQ19AdmissionDecision,
    ) -> None:
        if not isinstance(session.context_json, dict):
            raise TypeError("Q19 admission requires an object session context")
        context = dict(session.context_json)
        context["wms_admission_decision"] = decision.model_dump(mode="json")
        session.context_json = context
        await self._mutation_repository.persist(db, session)


rough_sorter_q19_admission_repository = RoughSorterQ19AdmissionRepository()

__all__ = ["RoughSorterQ19AdmissionRepository", "rough_sorter_q19_admission_repository"]
