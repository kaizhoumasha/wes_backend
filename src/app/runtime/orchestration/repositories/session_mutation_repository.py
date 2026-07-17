"""普通 WorklineSession mutation Repository。"""

from __future__ import annotations

from typing import Any


class SessionMutationRepository:
    """只参与调用方外层事务，不 commit/rollback。"""

    @staticmethod
    async def persist(db: Any, session: Any) -> None:
        db.add(session)
        await db.flush()


session_mutation_repository = SessionMutationRepository()

__all__ = ["SessionMutationRepository", "session_mutation_repository"]
