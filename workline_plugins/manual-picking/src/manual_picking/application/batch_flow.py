"""工作线锁内推进一个人工拣料 CTU 批次。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from .batch_policy import choose_next_batch

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession
    from wes_plugin_sdk import BinInboundBatchIntent, BinInboundBatchReady, BinReturnBatchIntent

    from .batch_repository import InboundFaceProgress


class BatchRepository(Protocol):
    async def has_unclosed_action(self, db: AsyncSession, workline_id: int) -> bool: ...

    async def return_retry_due(
        self, db: AsyncSession, workline_id: int, rack_id: str, rack_face: str, now: datetime
    ) -> bool: ...

    async def inbound_progress(
        self, db: AsyncSession, workline_id: int, task_id: str, rack_id: str, rack_face: str
    ) -> InboundFaceProgress | None: ...


class PassageRow(Protocol):
    bin_code: str | None


class PassageReader(Protocol):
    async def ready_return_prefix_for_update(
        self, db: AsyncSession, workline_id: int, *, limit: int = 4
    ) -> tuple[PassageRow, ...]: ...


class BatchScheduler(Protocol):
    async def create_in_session(
        self,
        db: AsyncSession,
        intent: BinInboundBatchIntent | BinReturnBatchIntent,
        *,
        workline_id: int,
        created_at: datetime,
    ) -> None: ...


class InboundCreator(Protocol):
    async def create_inbound_chunk(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        intent: BinInboundBatchIntent,
        ready: BinInboundBatchReady,
        evidence_id: int,
        offset: int,
        inlet_location: str,
    ) -> None: ...


class ManualPickingBatchFlow:
    def __init__(
        self,
        repository: BatchRepository,
        passages: PassageReader,
        scheduler: BatchScheduler,
        inbound: InboundCreator,
        *,
        uuid_factory: Callable[[], str],
    ) -> None:
        self._repository = repository
        self._passages = passages
        self._scheduler = scheduler
        self._inbound = inbound
        self._uuid_factory = uuid_factory

    async def advance_in_session(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        workline_code: str,
        task_id: str,
        rack_id: str,
        rack_face: str,
        return_location: str,
        inlet_location: str,
        now: datetime,
    ) -> bool:
        if await self._repository.has_unclosed_action(db, workline_id):
            return False
        rows = await self._passages.ready_return_prefix_for_update(db, workline_id)
        return_bins: list[str] = []
        for row in rows:
            if not row.bin_code:
                raise ValueError("ready return passage requires a known bin_code")
            return_bins.append(row.bin_code)
        progress = await self._repository.inbound_progress(db, workline_id, task_id, rack_id, rack_face)
        intent = choose_next_batch(
            operation_id=self._uuid_factory(),
            workline_code=workline_code,
            task_id=task_id,
            rack_id=rack_id,
            rack_face=rack_face,
            return_bins=tuple(return_bins),
            return_location=return_location,
            return_retry_due=await self._repository.return_retry_due(db, workline_id, rack_id, rack_face, now),
            allow_inbound=progress is None,
        )
        if intent is not None:
            await self._scheduler.create_in_session(db, intent, workline_id=workline_id, created_at=now)
            return True
        if progress is None or progress.next_offset is None:
            return False
        await self._inbound.create_inbound_chunk(
            db,
            workline_id=workline_id,
            intent=progress.intent,
            ready=progress.result,
            evidence_id=progress.evidence_id,
            offset=progress.next_offset,
            inlet_location=inlet_location,
        )
        return True


__all__ = ["ManualPickingBatchFlow"]
