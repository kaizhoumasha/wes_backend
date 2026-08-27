from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.dialects import postgresql

from src.app.device.models import (
    CommandStatus,
    DeviceEventCommandBlock,
    DeviceEventCommandBlockStatus,
)
from src.app.device.repositories import DeviceEventCommandBlockRepository


class _ScalarResult:
    def __init__(self, value: DeviceEventCommandBlock | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> DeviceEventCommandBlock | None:
        return self._value


class _RecordingDb:
    def __init__(self, value: DeviceEventCommandBlock | None = None) -> None:
        self.value = value
        self.added: list[DeviceEventCommandBlock] = []
        self.statements: list[object] = []
        self.flush_count = 0

    def add(self, block: DeviceEventCommandBlock) -> None:
        self.added.append(block)

    async def execute(self, statement: object) -> _ScalarResult:
        self.statements.append(statement)
        return _ScalarResult(self.value)

    async def flush(self) -> None:
        self.flush_count += 1

    async def commit(self) -> None:
        raise AssertionError("Repository 不得提交事务")


def _block(*, status: DeviceEventCommandBlockStatus = DeviceEventCommandBlockStatus.BLOCKED) -> DeviceEventCommandBlock:
    return DeviceEventCommandBlock(
        id=71,
        evidence_id=2_147_483_701,
        source_event_id="EVENT:" + "a" * 64,
        device_code="STATION_SCAN10",
        blocking_command_id=2_147_483_702,
        blocking_command_code="CMD-OLD-001",
        blocking_command_status=CommandStatus.RECONCILING,
        blocking_reconciliation_reason="DELIVERY_UNKNOWN",
        reason_code="DEVICE_HAS_ACTIVE_COMMAND",
        status=status,
        blocked_at=datetime(2026, 8, 27),
    )


@pytest.mark.asyncio
async def test_add_block_flushes_without_committing() -> None:
    block = _block()
    db = _RecordingDb()

    added = await DeviceEventCommandBlockRepository().add_block(db, block)  # type: ignore[arg-type]

    assert added is block
    assert db.added == [block]
    assert db.flush_count == 1
    assert block.requeued_at is None


@pytest.mark.asyncio
async def test_get_by_id_for_update_locks_exact_block_and_evidence_owner() -> None:
    block = _block()
    db = _RecordingDb(block)

    selected = await DeviceEventCommandBlockRepository().get_by_id_for_update(
        db,  # type: ignore[arg-type]
        block_id=71,
        evidence_id=2_147_483_701,
    )

    assert selected is block
    sql = str(
        db.statements[0].compile(  # type: ignore[union-attr]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "device_event_command_blocks.id = 71" in sql
    assert "device_event_command_blocks.evidence_id = 2147483701" in sql
    assert sql.endswith("FOR UPDATE")


@pytest.mark.asyncio
async def test_get_latest_for_evidence_returns_latest_history_regardless_of_status() -> None:
    block = _block(status=DeviceEventCommandBlockStatus.REQUEUED)
    db = _RecordingDb(block)

    selected = await DeviceEventCommandBlockRepository().get_latest_for_evidence(
        db,  # type: ignore[arg-type]
        evidence_id=2_147_483_701,
    )

    assert selected is block
    sql = str(
        db.statements[0].compile(  # type: ignore[union-attr]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "device_event_command_blocks.evidence_id = 2147483701" in sql
    assert "ORDER BY wes_biz.device_event_command_blocks.blocked_at DESC" in sql
    assert "wes_biz.device_event_command_blocks.id DESC" in sql
    assert "LIMIT 1" in sql


@pytest.mark.asyncio
async def test_mark_requeued_sets_terminal_history_timestamp_without_committing() -> None:
    block = _block()
    db = _RecordingDb()
    requeued_at = datetime(2026, 8, 27, 1, 2, 3)

    await DeviceEventCommandBlockRepository().mark_requeued(
        db,  # type: ignore[arg-type]
        block,
        requeued_at=requeued_at,
    )

    assert block.status == DeviceEventCommandBlockStatus.REQUEUED
    assert block.requeued_at == requeued_at
    assert db.flush_count == 1
