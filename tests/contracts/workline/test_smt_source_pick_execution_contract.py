"""SMT source-pick 最终写入的执行锚点与命令终态合同。"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.device.models.command import CommandResult, CommandStatus
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from tests.workline_runtime.test_smt_command_correlation_recovery import _command, _recover, _RecoveryService
from tests.workline_runtime.test_smt_generated_source_pick_lifecycle import _claim_and_process_source_pick


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("owner", "field", "corrupt_value"),
    [
        ("inbox", "trace_id", "trace-corrupt-inbox"),
        ("session", "trace_id", "trace-corrupt-session"),
        ("correlation", "trace_id", "trace-corrupt-correlation"),
        ("command", "trace_id", "trace-corrupt-command"),
        ("work_item", "manifest_version", "contract-corrupt-work-item"),
    ],
)
async def test_source_pick_success_rejects_each_corrupted_owned_anchor_field(
    db_session: object,
    owner: str,
    field: str,
    corrupt_value: str,
) -> None:
    service, source_item, source_inbox, command, _outbox = await _claim_and_process_source_pick(db_session)
    session = await db_session.get(WorklineSession, source_item.sorting_session_id)
    correlation = await db_session.scalar(
        select(ExecutionCorrelation).where(ExecutionCorrelation.correlation_id == source_inbox.correlation_id)
    )
    work_item = await db_session.scalar(
        select(ExecutionWorkItem).where(ExecutionWorkItem.execution_session_id == source_inbox.execution_session_id)
    )
    owned = {
        "inbox": source_inbox,
        "session": session,
        "correlation": correlation,
        "command": command,
        "work_item": work_item,
    }[owner]
    assert owned is not None
    setattr(owned, field, corrupt_value)
    command.status = CommandStatus.COMPLETED
    command.result = CommandResult.SUCCESS
    db_session.add_all([owned, command])
    await db_session.commit()

    result = await service.record_source_pick_success(
        db_session,
        handoff_demand_id=source_item.handoff_demand_id,
        source_item_id=source_item.id,
        claim_attempt_no=source_item.claim_attempt_no,
        source_pick_inbox_id=source_inbox.id,
        command_id=command.id,
    )

    assert result.outcome == "manual_hold"
    assert result.advanced is False
    assert source_item.status == SmtInboundHandoffSourceItemStatus.MANUAL_HOLD


@pytest.mark.asyncio
async def test_unique_completed_failed_candidate_records_correlation_then_enters_manual_hold() -> None:
    service = _RecoveryService([_command(status="COMPLETED", result="FAILED")])

    outcome, item = await _recover(service)

    assert outcome == "manual_hold"
    assert len(service.correlations) == 1
    assert service.successes == []
    assert len(service.manual_holds) == 1
    assert "失败终态" in str(service.manual_holds[0]["message"])
    assert item.status == SmtInboundHandoffSourceItemStatus.MANUAL_HOLD


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "result", "task_type", "expected"),
    [
        (CommandStatus.FAILED, CommandResult.FAILED, "SORTING_SOURCE_PICK", "manual_hold"),
        (CommandStatus.TIMEOUT, CommandResult.FAILED, "SORTING_SOURCE_PICK", "manual_hold"),
        (CommandStatus.CANCELLED, CommandResult.FAILED, "SORTING_SOURCE_PICK", "manual_hold"),
        (CommandStatus.PENDING, None, "SORTING_SOURCE_PICK", "retryable"),
        (CommandStatus.COMPLETED, CommandResult.SUCCESS, "PICK", "manual_hold"),
    ],
)
async def test_source_pick_success_verifies_command_terminal_evidence(
    db_session: object,
    status: CommandStatus,
    result: CommandResult | None,
    task_type: str,
    expected: str,
) -> None:
    service, source_item, source_inbox, command, _outbox = await _claim_and_process_source_pick(db_session)
    command.status = status
    command.result = result
    command.task_type = task_type
    db_session.add(command)
    await db_session.commit()

    kwargs = {
        "handoff_demand_id": source_item.handoff_demand_id,
        "source_item_id": source_item.id,
        "claim_attempt_no": source_item.claim_attempt_no,
        "source_pick_inbox_id": source_inbox.id,
        "command_id": command.id,
    }
    if expected == "retryable":
        with pytest.raises(ValueError, match="尚未成功完成"):
            await service.record_source_pick_success(db_session, **kwargs)
        assert source_item.status == SmtInboundHandoffSourceItemStatus.CLAIMED_BY_SORTING
    else:
        outcome = await service.record_source_pick_success(db_session, **kwargs)
        assert outcome.outcome == "manual_hold"
        assert source_item.status == SmtInboundHandoffSourceItemStatus.MANUAL_HOLD


@pytest.mark.asyncio
async def test_source_pick_success_persists_manual_hold_for_completed_failed_command(db_session: object) -> None:
    service, source_item, source_inbox, command, _outbox = await _claim_and_process_source_pick(db_session)
    source_item_id = source_item.id
    demand_id = source_item.handoff_demand_id
    command.status = CommandStatus.COMPLETED
    command.result = CommandResult.FAILED
    db_session.add(command)
    await db_session.commit()

    outcome = await service.record_source_pick_success(
        db_session,
        handoff_demand_id=source_item.handoff_demand_id,
        source_item_id=source_item.id,
        claim_attempt_no=source_item.claim_attempt_no,
        source_pick_inbox_id=source_inbox.id,
        command_id=command.id,
    )
    await db_session.commit()

    assert outcome.outcome == "manual_hold"
    assert outcome.advanced is False
    reopened_session_factory = async_sessionmaker(db_session.bind, class_=AsyncSession, expire_on_commit=False)
    async with reopened_session_factory() as reopened_db:
        reopened_item = await reopened_db.get(SmtInboundHandoffSourceItem, source_item_id)
        reopened_demand = await reopened_db.get(SmtInboundHandoffDemand, demand_id)
        assert reopened_item is not None and reopened_demand is not None
        assert reopened_item.status == SmtInboundHandoffSourceItemStatus.MANUAL_HOLD
        assert reopened_item.failure_code == "SOURCE_PICK_COMMAND_NOT_CREATED"
        assert reopened_demand.status == SmtInboundHandoffDemandStatus.MANUAL_HOLD
        assert reopened_demand.failure_code == reopened_item.failure_code


@pytest.mark.asyncio
async def test_associated_recovery_persists_manual_hold_for_completed_failed_command(db_session: object) -> None:
    service, source_item, source_inbox, command, _outbox = await _claim_and_process_source_pick(db_session)
    command.status = CommandStatus.COMPLETED
    command.result = CommandResult.FAILED
    db_session.add(command)
    await db_session.commit()
    await db_session.refresh(command)
    await db_session.refresh(source_inbox)
    assert command.status == CommandStatus.COMPLETED
    assert command.result == CommandResult.FAILED
    assert source_inbox.status == "PROCESSED"
    locked_item = await service.repository.get_source_item_for_update(db_session, source_item.id)
    assert locked_item is not None
    outcome = await service._recover_stuck_source_item(db_session, locked_item, now=source_item.updated_at)
    await db_session.commit()

    await db_session.refresh(source_item)
    assert outcome == "manual_hold"
    assert source_item.status == SmtInboundHandoffSourceItemStatus.MANUAL_HOLD
