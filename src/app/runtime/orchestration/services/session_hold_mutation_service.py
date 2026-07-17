"""普通 WorklineSession Hold 的外层事务参与型写服务。"""

from __future__ import annotations

from typing import Any

from src.app.runtime.orchestration.repositories.session_mutation_repository import (
    SessionMutationRepository,
    session_mutation_repository,
)
from src.utils.timezone import timezone
from src.utils.value_normalization import optional_int


class StaleSessionPrecondition(ValueError):
    """Session version 已变化，Plugin 必须读取新事实重新决策。"""


class SessionHoldMutationService:
    """只更新普通 Session；RuntimeHold 仍由 TIMER reconciliation owner 创建。"""

    def __init__(self, repository: SessionMutationRepository = session_mutation_repository) -> None:
        self._repository = repository

    async def hold(
        self,
        db: Any,
        *,
        session: Any,
        failure_domain: str,
        reason_code: str,
        message: str,
        fact_version: str | int | None,
        expected_status: str | None = None,
    ) -> Any:
        from src.app.workline.domain.services.session_lifecycle_service import workline_session_lifecycle_service

        actual_status = str(getattr(getattr(session, "status", None), "value", getattr(session, "status", "")))
        if expected_status is not None and actual_status != expected_status:
            raise StaleSessionPrecondition("session status changed")
        expected = self._version_value(fact_version)
        actual = optional_int(getattr(session, "version", None))
        if expected is not None and actual != expected:
            raise StaleSessionPrecondition("session fact version changed")
        workline_session_lifecycle_service.manual_hold(session, occurred_at=timezone.now_for_db())
        session.failure_domain = failure_domain
        session.failure_code = reason_code
        session.failure_message = message
        await self._repository.persist(db, session)
        return session

    @staticmethod
    def _version_value(value: str | int | None) -> int | None:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and value.rsplit(":", 1)[-1].isdigit():
            return int(value.rsplit(":", 1)[-1])
        return None


session_hold_mutation_service = SessionHoldMutationService()

__all__ = ["SessionHoldMutationService", "StaleSessionPrecondition", "session_hold_mutation_service"]
