"""SMT source-pick 最终写入的执行锚点与命令终态合同。"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.app.device.models.command import CommandResult, CommandStatus
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.models.smt_inbound_handoff import SmtInboundHandoffSourceItemStatus
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
