from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.execution.models import PositionProjection
from src.app.transport.contracts import (
    BinExchangePair,
    BinMove,
    RackBinSlot,
    TransportCaller,
    TransportHandle,
    TransportOutcome,
    TransportSubmitCode,
    TransportSubmitResult,
)
from src.app.transport.models import (
    TransportCallbackReceipt,
    TransportEvidence,
    TransportMember,
    TransportResourceBinding,
    TransportTask,
)
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from tests.support.sqlmodel_metadata import register_required_sqlmodel_metadata
from tests.support.transport_projections import confirm_rack_faces

register_required_sqlmodel_metadata()


class FakeProvider:
    async def submit(
        self,
        *,
        operation_id: str,
        transport_task_id: str,
        request_body: bytes,
        request_body_digest: str,
    ) -> TransportSubmitResult:
        return TransportSubmitResult(TransportSubmitCode.RECEIVED, transport_task_id)


class FakePublisher:
    def __init__(self) -> None:
        self.outcomes: list[TransportOutcome] = []

    async def publish(self, outcome: TransportOutcome) -> None:
        self.outcomes.append(outcome)


class OutcomeTransportService(TransportService):
    """为结果收敛测试建立已确认工作面前置事实。"""

    def __init__(self, db_engine: object, sessions: async_sessionmaker[AsyncSession]) -> None:
        super().__init__(sessions, TransportRepository(), FakeProvider())
        self._db_engine = db_engine

    async def move_bins(
        self,
        client_request_id: str,
        caller: TransportCaller,
        moves: tuple[BinMove, ...],
    ) -> TransportHandle:
        rack_faces = {
            position.rack_id: position.rack_face
            for move in moves
            for position in (move.source, move.target)
            if isinstance(position, RackBinSlot)
        }
        await confirm_rack_faces(self._db_engine, rack_faces)
        return await super().move_bins(client_request_id, caller, moves)

    async def exchange_bins(
        self,
        client_request_id: str,
        caller: TransportCaller,
        exchange_pairs: tuple[BinExchangePair, ...],
    ) -> TransportHandle:
        rack_faces = {
            position.rack_id: position.rack_face
            for pair in exchange_pairs
            for position in (pair.left_location, pair.right_location)
        }
        await confirm_rack_faces(self._db_engine, rack_faces)
        return await super().exchange_bins(client_request_id, caller, exchange_pairs)


@pytest_asyncio.fixture
async def outcome_service(db_engine: object) -> TransportService:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        for model in (
            TransportEvidence,
            TransportCallbackReceipt,
            TransportResourceBinding,
            TransportMember,
            PositionProjection,
            TransportTask,
        ):
            await db.execute(delete(model))
    return OutcomeTransportService(db_engine, sessions)


@pytest.fixture
def outcome_publisher() -> FakePublisher:
    return FakePublisher()
