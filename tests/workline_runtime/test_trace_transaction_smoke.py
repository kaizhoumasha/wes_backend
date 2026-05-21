"""TRACE 事务 smoke test。"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type: Any, _compiler: Any, **_kw: Any) -> str:
    return "JSON"


# 预加载相关模型，确保 tests/conftest.py 创建表时元数据已注册。
from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService
from src.app.device.models.command import CommandCallbackResult, CommandResult, CommandStatus, DeviceCommand, TaskType
from src.app.device.models.device import Device, DeviceProtocol, DeviceStatus
from src.app.device.services.device_command_service import DeviceCommandService
from src.app.device.services.device_service import device_service as runtime_device_service
from src.app.workline.models.inbox import InboxKind, WorklineInbox
from src.app.workline.models.outbox import DispatchType, OutboxStatus, TargetType, WorklineOutbox
from src.app.workline.models.session import SessionStatus, WorklineSession
from src.app.workline.models.timeline import TimelineActionType, TimelineStage, WorklineTimeline
from src.app.workline.models.workline import LineType, WorkLine
from src.app.workline.services.inbox_service import WorklineInboxService
from src.app.workline.services.trace_query_service import TraceQueryService


@dataclass(slots=True)
class SmokeFixture:
    workline: WorkLine
    device: Device
    session: WorklineSession
    command: DeviceCommand


def _sa_col(value: Any) -> Any:
    """SQLModel 字段实例类型与类级 SQL 表达式类型不一致，测试查询里集中收敛。"""

    return value


def _build_result_callback(fixture: SmokeFixture) -> CommandCallbackResult:
    return CommandCallbackResult(
        command_code=fixture.command.command_code,
        device_code=fixture.device.device_code,
        result=CommandResult.SUCCESS,
        finish_time=1702627250000,
        data={"task_type": "PICK_AND_PUT"},
    )


async def _load_inboxes_by_trace(db_session: AsyncSession, trace_id: str) -> list[WorklineInbox]:
    return list(
        (await db_session.execute(select(WorklineInbox).where(_sa_col(WorklineInbox.trace_id) == trace_id)))
        .scalars()
        .all()
    )


async def _load_outboxes_by_trace(db_session: AsyncSession, trace_id: str) -> list[WorklineOutbox]:
    return list(
        (
            await db_session.execute(
                select(WorklineOutbox).where(
                    _sa_col(WorklineOutbox.dispatch_key) == "device-command:CMD-SMOKE-001",
                    _sa_col(WorklineOutbox.workline_id)
                    == select(_sa_col(WorklineSession.workline_id))
                    .where(_sa_col(WorklineSession.trace_id) == trace_id)
                    .scalar_subquery(),
                )
            )
        )
        .scalars()
        .all()
    )


async def _load_timelines_by_trace(db_session: AsyncSession, trace_id: str) -> list[WorklineTimeline]:
    return list(
        (await db_session.execute(select(WorklineTimeline).where(_sa_col(WorklineTimeline.trace_id) == trace_id)))
        .scalars()
        .all()
    )


async def _seed_trace_graph(db_session: AsyncSession) -> SmokeFixture:
    workline = WorkLine(
        line_code="WL-SMOKE-001",
        line_name="Smoke Line",
        line_type=LineType.AUTO,
        plugin_key="smt_classifier",
        contract_version="1.0",
        config={},
        runtime_config_json={},
    )
    db_session.add(workline)
    await db_session.flush()
    workline_id = workline.id
    assert workline_id is not None

    device = Device(
        device_code="DEV-SMOKE-001",
        device_name="Smoke Device",
        work_line_id=workline_id,
        device_role="SCANNER",
        protocol=DeviceProtocol.HTTP,
        device_status=DeviceStatus.IDLE,
    )
    db_session.add(device)
    await db_session.flush()
    device_id = device.id
    assert device_id is not None

    session = WorklineSession(
        session_code="SES-SMOKE-001",
        workline_id=workline_id,
        plugin_key="smt_classifier",
        status=SessionStatus.RUNNING,
        trace_id="trace-smoke-001",
        context_json={},
    )
    db_session.add(session)
    await db_session.flush()
    session_id = session.id
    assert session_id is not None

    command = DeviceCommand(
        command_code="CMD-SMOKE-001",
        device_id=device_id,
        task_type=TaskType.PICK_AND_PUT,
        priority=5,
        timeout_ms=30000,
        params={"task_type": "PICK_AND_PUT"},
        trace_id="trace-smoke-001",
        session_id=session.session_code,
        session_id_int=session_id,
        workline_id=workline_id,
        plugin_key=workline.plugin_key,
        contract_version=workline.contract_version,
        status=CommandStatus.PENDING,
    )
    db_session.add(command)
    await db_session.flush()

    # 模拟设备命令已下发并 ACK，此时 WES 侧设备投影应处于占用态。
    device.device_status = DeviceStatus.RUNNING
    device.current_command_id = command.id

    outbox = WorklineOutbox(
        session_id=session_id,
        workline_id=workline_id,
        dispatch_type=DispatchType.DEVICE_COMMAND,
        dispatch_key=f"device-command:{command.command_code}",
        target_type=TargetType.DEVICE,
        target_code=device.device_code,
        payload_json={"command_code": command.command_code, "device_code": device.device_code},
        status=OutboxStatus.SENT,
    )
    db_session.add(outbox)
    await db_session.commit()
    await db_session.refresh(workline)
    await db_session.refresh(device)
    await db_session.refresh(session)
    await db_session.refresh(command)
    return SmokeFixture(workline=workline, device=device, session=session, command=command)


class TestTraceTransactionSmoke:
    @pytest.mark.asyncio
    async def test_callback_result_rolls_back_command_and_inbox_together(self, db_session: AsyncSession) -> None:
        """命令更新与 Inbox 写入必须同生同灭，不能只落一边。"""
        fixture = await _seed_trace_graph(db_session)
        command_id = fixture.command.id
        session_id = fixture.session.id
        device_id = fixture.device.id
        session_trace_id = fixture.session.trace_id
        assert command_id is not None
        assert session_id is not None
        assert device_id is not None
        assert session_trace_id is not None

        callback = _build_result_callback(fixture)
        existing_command = await db_session.get(DeviceCommand, command_id)
        assert existing_command is not None

        orchestration = CallbackOrchestrationService()
        cast("Any", orchestration)._commit_and_enqueue_workline_processing = AsyncMock(side_effect=RuntimeError("boom"))
        command_service = DeviceCommandService()
        cast("Any", command_service)._invalidate_command_cache = AsyncMock()

        with pytest.raises(RuntimeError, match="boom"):
            await orchestration.process_result(
                db_session,
                callback=callback,
                existing_command=existing_command,
                request_id="req-smoke-rollback",
                resolved_contract_version=fixture.workline.contract_version,
                command_service=command_service,
                device_service=runtime_device_service,
                inbox_service=WorklineInboxService(),
                enqueue_processing=lambda: None,
            )

        await db_session.rollback()
        db_session.expire_all()

        command_after = await db_session.get(DeviceCommand, command_id)
        assert command_after is not None
        assert command_after.status == CommandStatus.PENDING
        assert command_after.result is None

        device_after = await db_session.get(Device, device_id)
        assert device_after is not None
        assert device_after.device_status == DeviceStatus.RUNNING
        assert device_after.current_command_id == command_id
        assert device_after.error_code is None

        inbox_rows = await _load_inboxes_by_trace(db_session, session_trace_id)
        assert inbox_rows == []

        outbox_rows = await _load_outboxes_by_trace(db_session, session_trace_id)
        assert len(outbox_rows) == 1
        assert outbox_rows[0].status == OutboxStatus.SENT
        assert outbox_rows[0].finished_at is None

        timeline_rows = await _load_timelines_by_trace(db_session, session_trace_id)
        assert timeline_rows == []

        trace_query = TraceQueryService()
        trace_result = await trace_query.by_trace_id(db_session, session_trace_id)
        assert trace_result.session is not None
        assert trace_result.session.id == session_id
        assert trace_result.summary["commands"] == 1
        assert trace_result.commands[0].status == CommandStatus.PENDING

    @pytest.mark.asyncio
    async def test_callback_result_success_commits_command_and_inbox(self, db_session: AsyncSession) -> None:
        """成功路径应同时提交命令更新与 Inbox 写入，并可被 TraceQueryService 聚合。"""
        fixture = await _seed_trace_graph(db_session)
        command_id = fixture.command.id
        session_id = fixture.session.id
        device_id = fixture.device.id
        session_trace_id = fixture.session.trace_id
        assert command_id is not None
        assert session_id is not None
        assert device_id is not None
        assert session_trace_id is not None

        callback = _build_result_callback(fixture)
        existing_command = await db_session.get(DeviceCommand, command_id)
        assert existing_command is not None

        orchestration = CallbackOrchestrationService()
        command_service = DeviceCommandService()
        cast("Any", command_service)._invalidate_command_cache = AsyncMock()
        outcome = await orchestration.process_result(
            db_session,
            callback=callback,
            existing_command=existing_command,
            request_id="req-smoke-success",
            resolved_contract_version=fixture.workline.contract_version,
            command_service=command_service,
            device_service=runtime_device_service,
            inbox_service=WorklineInboxService(),
            enqueue_processing=lambda: None,
        )

        assert outcome.trace_id == fixture.session.trace_id
        assert outcome.is_duplicate is False

        command_after = await db_session.get(DeviceCommand, command_id)
        assert command_after is not None
        assert command_after.status == CommandStatus.COMPLETED
        assert command_after.result == CommandResult.SUCCESS

        device_after = await db_session.get(Device, device_id)
        assert device_after is not None
        assert device_after.device_status == DeviceStatus.IDLE
        assert device_after.current_command_id is None
        assert device_after.error_code is None

        inbox_rows = await _load_inboxes_by_trace(db_session, session_trace_id)
        assert len(inbox_rows) == 1
        assert inbox_rows[0].kind == InboxKind.COMMAND_RESULT

        outbox_rows = await _load_outboxes_by_trace(db_session, session_trace_id)
        assert len(outbox_rows) == 1
        assert outbox_rows[0].status == OutboxStatus.SENT
        assert outbox_rows[0].finished_at is None

        timeline_rows = await _load_timelines_by_trace(db_session, session_trace_id)
        assert len(timeline_rows) == 1
        assert timeline_rows[0].stage == TimelineStage.CALLBACK
        assert timeline_rows[0].action_type == TimelineActionType.COMMAND_ACKED
        assert timeline_rows[0].related_command_id == command_id

        trace_query = TraceQueryService()
        trace_result = await trace_query.by_trace_id(db_session, session_trace_id)
        assert trace_result.session is not None
        assert trace_result.session.id == session_id
        assert trace_result.summary["commands"] == 1
        assert trace_result.commands[0].command_code == fixture.command.command_code
        assert trace_result.commands[0].status == CommandStatus.COMPLETED
        assert trace_result.trace.trace_id == session_trace_id

    @pytest.mark.asyncio
    async def test_late_callback_race_records_evidence_without_clearing_device(self, db_session: AsyncSession) -> None:
        """第一次 late guard 后进入对账时，command service 的 outcome 必须阻止清设备运行态。"""
        from src.app.device.services.device_command_service import DeviceCallbackResultOutcome

        fixture = await _seed_trace_graph(db_session)
        command_id = fixture.command.id
        device_id = fixture.device.id
        assert command_id is not None
        assert device_id is not None

        callback = _build_result_callback(fixture)
        existing_command = await db_session.get(DeviceCommand, command_id)
        assert existing_command is not None
        orchestration = CallbackOrchestrationService()
        command_service = SimpleNamespace(
            handle_callback_result=AsyncMock(
                return_value=DeviceCallbackResultOutcome(
                    command=existing_command,
                    late_callback_recorded=True,
                )
            )
        )
        device_service = SimpleNamespace(mark_command_finished=AsyncMock())

        with patch(
            "src.app.workline.services.runtime_reconciliation_service."
            "workline_runtime_reconciliation_service.record_late_callback_if_pending",
            new=AsyncMock(return_value=False),
        ):
            outcome = await orchestration.process_result(
                db_session,
                callback=callback,
                existing_command=existing_command,
                request_id="req-late-race",
                resolved_contract_version=fixture.workline.contract_version,
                command_service=command_service,
                device_service=device_service,
                inbox_service=WorklineInboxService(),
                enqueue_processing=lambda: None,
            )

        assert outcome.is_duplicate is False
        device_service.mark_command_finished.assert_not_awaited()
        command_after = await db_session.get(DeviceCommand, command_id)
        assert command_after is not None
        assert command_after.status == CommandStatus.PENDING
        device_after = await db_session.get(Device, device_id)
        assert device_after is not None
        assert device_after.device_status == DeviceStatus.RUNNING
