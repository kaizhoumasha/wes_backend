"""Workline Unit of Work."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.sys.repositories import SystemOutboxRepository
from src.app.workline.repositories import (
    RuntimeHoldRepository,
    WorklineDiagnosticRepository,
    WorklineDispatchAttemptRepository,
    WorklineInboxRepository,
    WorkLineRepository,
    WorklineSafetyIncidentRepository,
    WorklineSessionRepository,
)
from src.database.db import get_db_context

if TYPE_CHECKING:
    from types import TracebackType

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class WorklineUnitOfWork:
    """Workline 写链事务边界。

    支持绑定 FastAPI 注入的外部 AsyncSession，也支持后台任务内部复用 get_db_context。
    """

    def __init__(
        self,
        *,
        db: AsyncSession | None = None,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self._external_db = db
        self._session_factory = session_factory or get_db_context
        self._session_context: AbstractAsyncContextManager[AsyncSession] | None = None
        self._db: AsyncSession | None = None

        self.sessions = WorklineSessionRepository()
        self.inboxes = WorklineInboxRepository()
        self.worklines = WorkLineRepository()
        self.runtime_holds = RuntimeHoldRepository()
        self.diagnostics = WorklineDiagnosticRepository()
        self.dispatch_attempts = WorklineDispatchAttemptRepository()
        self.safety_incidents = WorklineSafetyIncidentRepository()
        self.outboxes = SystemOutboxRepository()

    async def __aenter__(self) -> WorklineUnitOfWork:
        if self._external_db is not None:
            self._db = self._external_db
            return self

        self._session_context = self._session_factory()
        self._db = await self._session_context.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        if exc_type is not None and self._db is not None:
            await self.rollback()
        if self._session_context is not None:
            _ = await self._session_context.__aexit__(exc_type, exc_val, exc_tb)
        return False

    @property
    def session(self) -> AsyncSession:
        if self._db is None:
            raise RuntimeError("WorklineUnitOfWork must be entered before accessing session")
        return self._db

    async def commit(self) -> None:
        try:
            await self.session.commit()
        except Exception:
            await self.rollback()
            raise

    async def rollback(self) -> None:
        await self.session.rollback()

    async def checkpoint(self) -> None:
        """提交当前检查点，供单消息/单阶段处理释放事务级锁。"""

        await self.commit()


__all__ = ["WorklineUnitOfWork"]
