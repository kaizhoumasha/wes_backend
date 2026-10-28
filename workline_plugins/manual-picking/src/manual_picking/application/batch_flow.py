"""工作线锁内推进一个人工拣料 CTU 批次。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from wes_plugin_sdk import BinInboundBatchIntent, BinReturnCandidate, wms_operations

from .batch_policy import choose_next_batch
from .drain_execution_repository import DrainExecutionRepository

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession
    from wes_plugin_sdk import BinInboundBatchReady, BinReturnBatchIntent

    from .batch_repository import InboundFaceProgress
    from .bin_line.return_model import BinLineReturn


class BatchRepository(Protocol):
    async def record_allocation(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        picking_task_id: int,
        plan_revision: int,
        intent: BinInboundBatchIntent,
        evidence_id: int | None,
    ) -> None: ...

    async def return_before_motion_state(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        rack_id: str,
        rack_face: str,
        arrived_at: datetime,
    ) -> str | None: ...
    async def has_unclosed_action_for_face(
        self,
        db: AsyncSession,
        workline_id: int,
        task_id: str | None,
        plan_revision: int | None,
        rack_id: str,
        rack_face: str,
    ) -> bool: ...

    async def has_unclosed_return_transport_for_face(
        self, db: AsyncSession, *, workline_id: int, rack_id: str, rack_face: str
    ) -> bool: ...

    async def return_retry_due(
        self,
        db: AsyncSession,
        workline_id: int,
        rack_id: str,
        rack_face: str,
        source_evidence_id: int,
        now: datetime,
        after: datetime,
    ) -> bool: ...

    async def inbound_progress(
        self,
        db: AsyncSession,
        workline_id: int,
        task_id: str,
        plan_revision: int,
        rack_id: str,
        rack_face: str,
        inlet_location: str,
    ) -> InboundFaceProgress | None: ...


class PassageReader(Protocol):
    async def ready_prefix_for_update(
        self, db: AsyncSession, workline_id: int, *, limit: int = 4
    ) -> tuple[BinLineReturn, ...]: ...


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
        picking_task_id: int,
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
        drain_executions: DrainExecutionRepository | None = None,
    ) -> None:
        self._drain_executions = drain_executions or DrainExecutionRepository()
        self._repository = repository
        self._passages = passages
        self._scheduler = scheduler
        self._inbound = inbound
        self._uuid_factory = uuid_factory

    async def return_before_motion(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        workline_code: str,
        rack_id: str,
        rack_face: str | None,
        return_location: str,
        arrived_at: datetime,
        now: datetime,
    ) -> int | None:
        """None 允许换面/离场；0 等待直接义务；1 创建当前到位面的回架请求。"""
        rows = await self._passages.ready_prefix_for_update(db, workline_id)
        rows = await self._drain_executions.eligible_return_prefix(db, workline_id=workline_id, rows=tuple(rows))
        if not rows:
            return None
        if rack_face is None:
            return None
        state = await self._repository.return_before_motion_state(
            db,
            workline_id=workline_id,
            rack_id=rack_id,
            rack_face=rack_face,
            arrived_at=arrived_at,
        )
        if state == "WAIT":
            return 0
        if state == "DONE":
            return None
        intent = wms_operations.outbound_bin_return_batch(
            operation_id=self._uuid_factory(),
            workline_code=workline_code,
            rack_id=rack_id,
            rack_face=rack_face,
            return_candidates=tuple(
                BinReturnCandidate(i, row.bin_code, return_location) for i, row in enumerate(rows, 1)
            ),
        )
        await self._scheduler.create_in_session(db, intent, workline_id=workline_id, created_at=now)
        return 1

    async def face_progress(
        self,
        db: AsyncSession,
        workline_id: int,
        task_id: str,
        plan_revision: int,
        rack_id: str,
        rack_face: str,
        inlet_location: str,
    ) -> InboundFaceProgress | None:
        return await self._repository.inbound_progress(
            db, workline_id, task_id, plan_revision, rack_id, rack_face, inlet_location
        )

    async def has_unclosed_action_for_face(
        self,
        db: AsyncSession,
        workline_id: int,
        task_id: str | None,
        plan_revision: int | None,
        rack_id: str,
        rack_face: str,
    ) -> bool:
        return await self._repository.has_unclosed_action_for_face(
            db, workline_id, task_id, plan_revision, rack_id, rack_face
        )

    async def advance_in_session(  # noqa: PLR0911
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        workline_code: str,
        picking_task_id: int,
        task_id: str,
        plan_revision: int,
        source_evidence_id: int,
        rack_id: str,
        rack_face: str,
        return_location: str,
        inlet_location: str,
        now: datetime,
        allow_inbound: bool = True,
    ) -> bool:
        if await self._repository.has_unclosed_action_for_face(
            db, workline_id, task_id, plan_revision, rack_id, rack_face
        ):
            return False
        progress = await self._repository.inbound_progress(
            db, workline_id, task_id, plan_revision, rack_id, rack_face, inlet_location
        )
        if progress is None:
            if not allow_inbound:
                if await self._repository.has_unclosed_return_transport_for_face(
                    db, workline_id=workline_id, rack_id=rack_id, rack_face=rack_face
                ):
                    return False
                rows = await self._passages.ready_prefix_for_update(db, workline_id)
                rows = await self._drain_executions.eligible_return_prefix(
                    db, workline_id=workline_id, rows=tuple(rows)
                )
                return_bins = tuple(row.bin_code for row in rows)
                if not return_bins or not await self._repository.return_retry_due(
                    db, workline_id, rack_id, rack_face, source_evidence_id, now, now
                ):
                    return False
                intent = choose_next_batch(
                    operation_id=self._uuid_factory(),
                    workline_code=workline_code,
                    task_id=task_id,
                    rack_id=rack_id,
                    rack_face=rack_face,
                    return_bins=return_bins,
                    return_location=return_location,
                    return_retry_due=True,
                    allow_inbound=False,
                )
                assert intent is not None
                await self._scheduler.create_in_session(db, intent, workline_id=workline_id, created_at=now)
                return True
            intent = choose_next_batch(
                operation_id=self._uuid_factory(),
                workline_code=workline_code,
                task_id=task_id,
                rack_id=rack_id,
                rack_face=rack_face,
                return_bins=(),
                return_location=return_location,
                return_retry_due=False,
                allow_inbound=True,
            )
            assert isinstance(intent, BinInboundBatchIntent)
            await self._repository.record_allocation(
                db,
                workline_id=workline_id,
                picking_task_id=picking_task_id,
                plan_revision=plan_revision,
                intent=intent,
                evidence_id=None,
            )
            await self._scheduler.create_in_session(db, intent, workline_id=workline_id, created_at=now)
            return True
        rows = await self._passages.ready_prefix_for_update(db, workline_id)
        rows = await self._drain_executions.eligible_return_prefix(db, workline_id=workline_id, rows=tuple(rows))
        return_bins: list[str] = []
        for row in rows:
            if not row.bin_code:
                raise ValueError("ready return passage requires a known bin_code")
            return_bins.append(row.bin_code)
        after = getattr(progress, "last_chunk_created_at", None) or now
        can_interleave_return = progress.feed_complete or progress.next_offset is not None
        if (
            can_interleave_return
            and return_bins
            and await self._repository.return_retry_due(
                db, workline_id, rack_id, rack_face, source_evidence_id, now, after
            )
            and not await self._repository.has_unclosed_return_transport_for_face(
                db, workline_id=workline_id, rack_id=rack_id, rack_face=rack_face
            )
        ):
            intent = choose_next_batch(
                operation_id=self._uuid_factory(),
                workline_code=workline_code,
                task_id=task_id,
                rack_id=rack_id,
                rack_face=rack_face,
                return_bins=tuple(return_bins),
                return_location=return_location,
                return_retry_due=True,
                allow_inbound=False,
            )
            assert intent is not None
            await self._scheduler.create_in_session(db, intent, workline_id=workline_id, created_at=now)
            return True
        if not progress.feed_complete:
            if progress.next_offset is None:
                return False
            await self._inbound.create_inbound_chunk(
                db,
                workline_id=workline_id,
                picking_task_id=picking_task_id,
                intent=progress.intent,
                ready=cast("BinInboundBatchReady", progress.result),
                evidence_id=progress.evidence_id,
                offset=progress.next_offset,
                inlet_location=inlet_location,
            )
            return True
        return False


__all__ = ["ManualPickingBatchFlow"]
