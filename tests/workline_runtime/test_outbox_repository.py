from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from src.app.device.models import Device
from src.app.sys.models import SystemOutbox, SystemOutboxDispatchType, SystemOutboxStatus, SystemOutboxTargetType
from src.app.sys.repositories.outbox_repository import SystemOutboxRepository
from src.app.workline.models.session import RunMode, SessionStatus, WorklineSession
from src.utils.timezone import timezone

# db_session fixture 通过 SQLModel.metadata.create_all 建表；显式导入 Device 确保
# system_outbox.device_id 的外键目标表已注册到 metadata。
_DEVICE_TABLE_FOR_SYSTEM_OUTBOX_FK = Device


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class _FakeDb:
    def __init__(self, value: object) -> None:
        self.value = value
        self.flush = AsyncMock()

    async def execute(self, _statement: object) -> _ScalarResult:
        return _ScalarResult(self.value)


@pytest.mark.asyncio
async def test_mark_as_sent_clears_retry_error_projection() -> None:
    outbox = SimpleNamespace(
        status=SystemOutboxStatus.DISPATCHING,
        sent_at=None,
        next_retry_at=object(),
        last_error="Dispatch failed",
    )
    db = _FakeDb(outbox)

    updated = await SystemOutboxRepository().mark_as_sent(db, 1)  # type: ignore[arg-type]

    assert updated is outbox
    assert outbox.status == SystemOutboxStatus.SENT
    assert outbox.sent_at is not None
    assert outbox.next_retry_at is None
    assert outbox.last_error is None
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_as_sent_does_not_overwrite_cancelled_outbox() -> None:
    outbox = SimpleNamespace(
        status=SystemOutboxStatus.CANCELLED,
        sent_at=None,
        next_retry_at=None,
        last_error="CALLBACK_DEADLINE_EXPIRED",
    )
    db = _FakeDb(outbox)

    updated = await SystemOutboxRepository().mark_as_sent(db, 1)  # type: ignore[arg-type]

    assert updated is None
    assert outbox.status == SystemOutboxStatus.CANCELLED
    assert outbox.sent_at is None
    assert outbox.last_error == "CALLBACK_DEADLINE_EXPIRED"
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_as_sent_does_not_reopen_callback_finished_dispatching_outbox() -> None:
    outbox = SimpleNamespace(
        status=SystemOutboxStatus.DISPATCHING,
        sent_at=None,
        next_retry_at=object(),
        last_error=None,
        finished_at=timezone.now_for_db(),
    )
    db = _FakeDb(outbox)

    updated = await SystemOutboxRepository().mark_as_sent(db, 1)  # type: ignore[arg-type]

    assert updated is None
    assert outbox.status == SystemOutboxStatus.DISPATCHING
    assert outbox.sent_at is None
    assert outbox.next_retry_at is not None
    assert outbox.finished_at is not None
    db.flush.assert_not_awaited()


def test_repository_does_not_expose_device_busy_direct_sent_repair() -> None:
    assert not hasattr(SystemOutboxRepository(), "mark_blocked_device_busy_as_sent")


@pytest.mark.asyncio
async def test_mark_as_blocked_by_workline_state_parks_without_retry() -> None:
    outbox = SimpleNamespace(
        status=SystemOutboxStatus.NEW,
        workline_id=45,
        finished_at=None,
        next_retry_at=object(),
        last_error="Dispatch failed",
        blocked_by_reconciliation_session_id=None,
        blocked_device_id=None,
        blocked_workline_id=None,
        blocked_reason=None,
    )
    db = _FakeDb(outbox)

    updated = await SystemOutboxRepository().mark_as_blocked_by_workline_state(  # type: ignore[arg-type]
        db,
        1,
        owner_session_id=91,
        reason="CALLBACK_DEADLINE_EXPIRED",
        blocked_device_id=7,
        blocked_workline_id=45,
    )

    assert updated is outbox
    assert outbox.status == SystemOutboxStatus.BLOCKED_RESOURCE
    assert outbox.finished_at is not None
    assert outbox.next_retry_at is None
    assert outbox.last_error == "CALLBACK_DEADLINE_EXPIRED"
    assert outbox.blocked_by_reconciliation_session_id == 91
    assert outbox.blocked_device_id == 7
    assert outbox.blocked_workline_id == 45
    assert outbox.blocked_reason == "CALLBACK_DEADLINE_EXPIRED"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_block_by_runtime_hold_parks_with_hold_owner() -> None:
    outbox = SimpleNamespace(
        status=SystemOutboxStatus.NEW,
        workline_id=45,
        finished_at=None,
        next_retry_at=object(),
        last_error="Dispatch failed",
        blocked_by_runtime_hold_id=None,
        blocked_by_reconciliation_session_id=None,
        blocked_device_id=None,
        blocked_workline_id=None,
        blocked_reason=None,
    )
    db = _FakeDb(outbox)

    updated = await SystemOutboxRepository().block_by_runtime_hold(  # type: ignore[arg-type]
        db,
        1,
        runtime_hold_id=9901,
        owner_session_id=91,
        reason="CALLBACK_DEADLINE_EXPIRED",
        blocked_device_id=7,
        blocked_workline_id=45,
    )

    assert updated is outbox
    assert outbox.status == SystemOutboxStatus.BLOCKED_RESOURCE
    assert outbox.finished_at is not None
    assert outbox.next_retry_at is None
    assert outbox.last_error == "CALLBACK_DEADLINE_EXPIRED"
    assert outbox.blocked_by_runtime_hold_id == 9901
    assert outbox.blocked_by_reconciliation_session_id == 91
    assert outbox.blocked_device_id == 7
    assert outbox.blocked_workline_id == 45
    assert outbox.blocked_reason == "CALLBACK_DEADLINE_EXPIRED"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_as_blocked_by_workline_estop_terminates_without_retry() -> None:
    outbox = SimpleNamespace(
        status=SystemOutboxStatus.NEW,
        finished_at=None,
        next_retry_at=object(),
        last_error=None,
    )
    db = _FakeDb(outbox)

    updated = await SystemOutboxRepository().mark_as_blocked_by_workline_estop(db, 1)  # type: ignore[arg-type]

    assert updated is outbox
    assert outbox.status == SystemOutboxStatus.FAILED
    assert outbox.finished_at is not None
    assert outbox.next_retry_at is None
    assert outbox.last_error == "BLOCKED_BY_WORKLINE_ESTOP"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_as_blocked_by_device_busy_parks_without_retry() -> None:
    outbox = SimpleNamespace(
        status=SystemOutboxStatus.DISPATCHING,
        workline_id=45,
        finished_at=None,
        next_retry_at=object(),
        last_error=None,
        blocked_by_reconciliation_session_id=91,
        blocked_device_id=None,
        blocked_workline_id=None,
        blocked_reason=None,
        blocked_at=None,
        last_blocked_check_at=None,
        blocked_check_count=0,
        blocked_detail_json={},
    )
    db = _FakeDb(outbox)

    updated = await SystemOutboxRepository().mark_as_blocked_by_device_busy(  # type: ignore[arg-type]
        db,
        1,
        blocked_device_id=7,
        blocked_workline_id=45,
        reason="DEVICE_BUSY",
        last_error="设备 ARM01 正在执行任务",
    )

    assert updated is outbox
    assert outbox.status == SystemOutboxStatus.BLOCKED_RESOURCE
    assert outbox.finished_at is not None
    assert outbox.next_retry_at is None
    assert outbox.last_error == "设备 ARM01 正在执行任务"
    assert outbox.blocked_by_reconciliation_session_id is None
    assert outbox.blocked_device_id == 7
    assert outbox.blocked_workline_id == 45
    assert outbox.blocked_reason == "DEVICE_BUSY"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_block_for_resource_wait_writes_minimal_diagnostics() -> None:
    outbox = SimpleNamespace(
        status=SystemOutboxStatus.DISPATCHING,
        workline_id=45,
        attempt_count=2,
        finished_at=None,
        next_retry_at=object(),
        last_error=None,
        blocked_at=None,
        last_blocked_check_at=None,
        blocked_check_count=0,
        blocked_detail_json={},
        blocked_by_reconciliation_session_id=91,
        blocked_device_id=None,
        blocked_workline_id=None,
        blocked_reason=None,
    )
    db = _FakeDb(outbox)

    updated = await SystemOutboxRepository().mark_as_blocked_by_device_busy(  # type: ignore[arg-type]
        db,
        1,
        blocked_device_id=7,
        blocked_workline_id=45,
        reason="DEVICE_BUSY",
        last_error="设备 ARM01 正在执行任务",
        detail={"device_code": "ARM01", "last_probe_result": "BUSY"},
    )

    assert updated is outbox
    assert outbox.status == SystemOutboxStatus.BLOCKED_RESOURCE
    assert outbox.attempt_count == 2
    assert outbox.blocked_at is not None
    assert outbox.last_blocked_check_at is not None
    assert outbox.blocked_check_count == 1
    assert outbox.blocked_detail_json == {"device_code": "ARM01", "last_probe_result": "BUSY"}
    assert outbox.last_error == "设备 ARM01 正在执行任务"
    assert outbox.blocked_device_id == 7
    assert outbox.blocked_workline_id == 45
    assert outbox.blocked_reason == "DEVICE_BUSY"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_block_for_resource_wait_preserves_blocked_at_and_increments_checks() -> None:
    first_blocked_at = timezone.now_for_db() - timedelta(seconds=10)
    first_check_at = timezone.now_for_db() - timedelta(seconds=5)
    outbox = SimpleNamespace(
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        workline_id=45,
        attempt_count=1,
        finished_at=None,
        next_retry_at=None,
        last_error="设备 ARM01 正在执行任务",
        blocked_at=first_blocked_at,
        last_blocked_check_at=first_check_at,
        blocked_check_count=1,
        blocked_detail_json={"device_code": "ARM01", "last_probe_result": "BUSY"},
        blocked_by_reconciliation_session_id=None,
        blocked_device_id=7,
        blocked_workline_id=45,
        blocked_reason="DEVICE_BUSY",
    )
    db = _FakeDb(outbox)

    updated = await SystemOutboxRepository().mark_as_blocked_by_device_busy(  # type: ignore[arg-type]
        db,
        1,
        blocked_device_id=7,
        blocked_workline_id=45,
        reason="DEVICE_BUSY",
        last_error="ECS status 仍为 BUSY",
        detail={"device_code": "ARM01", "last_probe_result": "BUSY_AGAIN"},
    )

    assert updated is outbox
    assert outbox.status == SystemOutboxStatus.BLOCKED_RESOURCE
    assert outbox.attempt_count == 1
    assert outbox.blocked_at == first_blocked_at
    assert outbox.last_blocked_check_at is not None
    assert outbox.last_blocked_check_at > first_check_at
    assert outbox.blocked_check_count == 2
    assert outbox.blocked_detail_json == {"device_code": "ARM01", "last_probe_result": "BUSY_AGAIN"}
    assert outbox.last_error == "ECS status 仍为 BUSY"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_block_for_resource_wait_skips_duplicate_probe_inside_interval() -> None:
    first_check_at = timezone.now_for_db()
    outbox = SimpleNamespace(
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        workline_id=45,
        attempt_count=1,
        finished_at=None,
        next_retry_at=None,
        last_error="设备 ARM01 正在执行任务",
        blocked_at=first_check_at - timedelta(seconds=10),
        last_blocked_check_at=first_check_at,
        blocked_check_count=3,
        blocked_detail_json={"device_code": "ARM01", "last_probe_result": "BUSY"},
        blocked_by_reconciliation_session_id=None,
        blocked_device_id=7,
        blocked_workline_id=45,
        blocked_reason="DEVICE_BUSY",
    )
    db = _FakeDb(outbox)

    updated = await SystemOutboxRepository().mark_as_blocked_by_device_busy(  # type: ignore[arg-type]
        db,
        1,
        blocked_device_id=7,
        blocked_workline_id=45,
        reason="DEVICE_BUSY",
        last_error="ECS status 仍为 BUSY",
        detail={"device_code": "ARM01", "last_probe_result": "BUSY_AGAIN"},
    )

    assert updated is None
    assert outbox.last_blocked_check_at == first_check_at
    assert outbox.blocked_check_count == 3
    assert outbox.blocked_detail_json == {"device_code": "ARM01", "last_probe_result": "BUSY"}
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_block_for_resource_wait_preserves_escalated_diagnostic_summary() -> None:
    first_blocked_at = timezone.now_for_db() - timedelta(seconds=180)
    first_check_at = timezone.now_for_db() - timedelta(seconds=5)
    outbox = SimpleNamespace(
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        workline_id=45,
        attempt_count=1,
        finished_at=None,
        next_retry_at=None,
        last_error="DEVICE_STATUS_PRECHECK_WAIT_TIMEOUT",
        blocked_at=first_blocked_at,
        last_blocked_check_at=first_check_at,
        blocked_check_count=31,
        blocked_detail_json={
            "device_code": "ARM01",
            "last_probe_result": "escalated",
            "escalated_at": "2026-06-04T00:00:00+00:00",
            "diagnostic_key": "outbox-resource-wait:1:DEVICE_STATUS_PRECHECK_WAIT",
        },
        blocked_by_reconciliation_session_id=None,
        blocked_device_id=7,
        blocked_workline_id=45,
        blocked_reason="DEVICE_STATUS_PRECHECK_WAIT",
    )
    db = _FakeDb(outbox)

    updated = await SystemOutboxRepository().mark_as_blocked_by_device_busy(  # type: ignore[arg-type]
        db,
        1,
        blocked_device_id=7,
        blocked_workline_id=45,
        reason="DEVICE_STATUS_PRECHECK_WAIT",
        last_error="设备 ARM01 实时状态查询仍不可用",
        detail={"device_code": "ARM01", "last_probe_result": "STATUS_WAIT", "error_kind": "http_status"},
    )

    assert updated is outbox
    assert outbox.blocked_check_count == 32
    assert outbox.blocked_detail_json == {
        "device_code": "ARM01",
        "error_kind": "http_status",
        "last_probe_result": "escalated",
        "escalated_at": "2026-06-04T00:00:00+00:00",
        "diagnostic_key": "outbox-resource-wait:1:DEVICE_STATUS_PRECHECK_WAIT",
    }
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_block_for_resource_wait_allows_device_resource_wait_reason_switch() -> None:
    """ECS busy/status wait 都是同一 admission 资源等待，reason 变化时仍要更新观测元数据。"""

    first_blocked_at = timezone.now_for_db() - timedelta(seconds=10)
    first_check_at = timezone.now_for_db() - timedelta(seconds=5)
    outbox = SimpleNamespace(
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        workline_id=45,
        attempt_count=1,
        finished_at=None,
        next_retry_at=None,
        last_error="设备 ARM01 正在执行任务",
        blocked_at=first_blocked_at,
        last_blocked_check_at=first_check_at,
        blocked_check_count=1,
        blocked_detail_json={"device_code": "ARM01", "last_probe_result": "BUSY"},
        blocked_by_reconciliation_session_id=None,
        blocked_device_id=7,
        blocked_workline_id=45,
        blocked_reason="DEVICE_BUSY",
    )
    db = _FakeDb(outbox)

    updated = await SystemOutboxRepository().mark_as_blocked_by_device_busy(  # type: ignore[arg-type]
        db,
        1,
        blocked_device_id=7,
        blocked_workline_id=45,
        reason="DEVICE_STATUS_PRECHECK_WAIT",
        last_error="设备 ARM01 实时状态查询超时，等待下次预检",
        detail={"device_code": "ARM01", "last_probe_result": "STATUS_WAIT", "error_kind": "timeout"},
    )

    assert updated is outbox
    assert outbox.blocked_reason == "DEVICE_STATUS_PRECHECK_WAIT"
    assert outbox.blocked_at == first_blocked_at
    assert outbox.last_blocked_check_at is not None
    assert outbox.last_blocked_check_at > first_check_at
    assert outbox.blocked_check_count == 2
    assert outbox.blocked_detail_json == {
        "device_code": "ARM01",
        "last_probe_result": "STATUS_WAIT",
        "error_kind": "timeout",
    }
    assert outbox.last_error == "设备 ARM01 实时状态查询超时，等待下次预检"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_dispatching_device_messages_returns_only_device_command_leases(db_session) -> None:
    session = WorklineSession(
        session_code="session-dispatching-device-candidates",
        workline_id=45,
        plugin_key="test_workline_plugin",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.WAITING_DEVICE_RESULT,
    )
    db_session.add(session)
    await db_session.flush()

    dispatching_device = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:dispatching-device",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM01",
        status=SystemOutboxStatus.DISPATCHING,
    )
    new_device = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:new-device",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM01",
        status=SystemOutboxStatus.NEW,
    )
    dispatching_external = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key="external:dispatching",
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        target_code="https://example.invalid/callback",
        status=SystemOutboxStatus.DISPATCHING,
    )
    db_session.add_all([dispatching_device, new_device, dispatching_external])
    await db_session.flush()

    result = await SystemOutboxRepository().get_dispatching_device_messages(db_session, limit=10)

    assert [item.id for item in result] == [dispatching_device.id]

    filtered = await SystemOutboxRepository().get_dispatching_device_messages(
        db_session, limit=10, operation_domains=("RACK",)
    )
    assert filtered == []


@pytest.mark.asyncio
async def test_get_blocked_device_busy_messages_returns_only_device_busy_blocks(db_session) -> None:
    session = WorklineSession(
        session_code="session-blocked-device-busy-candidates",
        workline_id=45,
        plugin_key="test_workline_plugin",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.WAITING_DEVICE_RESULT,
    )
    db_session.add(session)
    await db_session.flush()

    device_busy = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:blocked-device-busy",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM03",
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        blocked_reason="DEVICE_BUSY",
    )
    workline_busy = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:blocked-workline",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM03",
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        blocked_reason="WORKLINE_RECONCILING",
    )
    external_busy = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key="external:blocked-device-busy",
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        target_code="https://example.invalid/callback",
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        blocked_reason="DEVICE_BUSY",
    )
    db_session.add_all([device_busy, workline_busy, external_busy])
    await db_session.flush()

    result = await SystemOutboxRepository().get_blocked_device_busy_messages(db_session, limit=10)

    assert [item.id for item in result] == [device_busy.id]

    filtered = await SystemOutboxRepository().get_blocked_device_busy_messages(
        db_session, limit=10, operation_domains=("RACK",)
    )
    assert filtered == []


@pytest.mark.asyncio
async def test_get_pending_messages_does_not_skip_earlier_device_retry(db_session) -> None:
    """同设备早到 outbox 仍在 backoff 时，晚到 outbox 不能越过它派发。"""

    now = timezone.now_for_db()
    session = WorklineSession(
        session_code="session-device-fifo-retry",
        workline_id=45,
        plugin_key="test_workline_plugin",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.WAITING_DEVICE_RESULT,
    )
    db_session.add(session)
    await db_session.flush()

    earlier_retry = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:earlier-retry",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM01",
        status=SystemOutboxStatus.NEW,
        next_retry_at=now + timedelta(seconds=30),
        created_at=now,
    )
    later_ready = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:later-ready",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM01",
        status=SystemOutboxStatus.NEW,
        created_at=now + timedelta(seconds=1),
    )
    other_device_ready = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:other-device-ready",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM02",
        status=SystemOutboxStatus.NEW,
        created_at=now + timedelta(seconds=2),
    )
    db_session.add_all([earlier_retry, later_ready, other_device_ready])
    await db_session.flush()

    result = await SystemOutboxRepository().get_pending_messages(db_session, limit=10)

    assert [item.dispatch_key for item in result] == ["device-command:other-device-ready"]


@pytest.mark.asyncio
async def test_get_pending_messages_returns_only_earliest_active_device_outbox(db_session) -> None:
    """同设备多个 ready outbox 同时存在时，每轮只领取队首。"""

    now = timezone.now_for_db()
    session = WorklineSession(
        session_code="session-device-fifo-ready",
        workline_id=45,
        plugin_key="test_workline_plugin",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.WAITING_DEVICE_RESULT,
    )
    db_session.add(session)
    await db_session.flush()

    first = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:first-ready",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM01",
        status=SystemOutboxStatus.NEW,
        created_at=now,
    )
    second = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:second-ready",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM01",
        status=SystemOutboxStatus.NEW,
        created_at=now + timedelta(seconds=1),
    )
    db_session.add_all([first, second])
    await db_session.flush()

    result = await SystemOutboxRepository().get_pending_messages(db_session, limit=10)
    assert [item.dispatch_key for item in result] == ["device-command:first-ready"]

    first.status = SystemOutboxStatus.SENT
    await db_session.flush()

    result_after_first_sent = await SystemOutboxRepository().get_pending_messages(db_session, limit=10)
    assert [item.dispatch_key for item in result_after_first_sent] == ["device-command:second-ready"]


@pytest.mark.asyncio
async def test_get_probeable_blocked_device_heads_returns_oldest_blocked_per_device(db_session) -> None:
    """blocked 设备队首可被重新探测，且 device_id/target_code 混合写入仍保持物理设备 FIFO。"""

    now = timezone.now_for_db()
    session = WorklineSession(
        session_code="session-probeable-blocked-device-head",
        workline_id=45,
        plugin_key="test_workline_plugin",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.WAITING_DEVICE_RESULT,
    )
    device = Device(
        device_code="ARM-MIXED-FIFO-01",
        device_name="ARM mixed fifo 01",
        device_role="ROBOT_ARM",
        role_index=1,
    )
    other_device = Device(
        device_code="ARM-MIXED-FIFO-02",
        device_name="ARM mixed fifo 02",
        device_role="ROBOT_ARM",
        role_index=2,
    )
    db_session.add_all([session, device, other_device])
    await db_session.flush()

    earlier_blocked = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        device_id=device.id,
        operation_domain="WORKLINE",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:mixed-earlier-blocked",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="legacy-arm-alias",
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        blocked_reason="DEVICE_BUSY",
        blocked_device_id=device.id,
        blocked_at=now - timedelta(seconds=20),
        last_blocked_check_at=now - timedelta(seconds=10),
        created_at=now,
    )
    later_new_same_physical_by_code = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        operation_domain="WORKLINE",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:mixed-later-new-by-code",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code=device.device_code,
        status=SystemOutboxStatus.NEW,
        created_at=now + timedelta(seconds=1),
    )
    later_blocked_same_physical = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        operation_domain="RACK",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:mixed-later-blocked-by-code",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code=device.device_code,
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        blocked_reason="DEVICE_STATUS_PRECHECK_WAIT",
        blocked_at=now - timedelta(seconds=12),
        last_blocked_check_at=now - timedelta(seconds=8),
        created_at=now + timedelta(seconds=2),
    )
    other_device_ready = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        device_id=other_device.id,
        operation_domain="WORKLINE",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:other-device-ready",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code=other_device.device_code,
        status=SystemOutboxStatus.NEW,
        created_at=now + timedelta(seconds=3),
    )
    other_domain_blocked = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        operation_domain="HANDLING",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:other-domain-blocked",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="HANDLING-ARM-01",
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        blocked_reason="DEVICE_BUSY",
        blocked_at=now - timedelta(seconds=20),
        last_blocked_check_at=now - timedelta(seconds=10),
        created_at=now + timedelta(seconds=4),
    )
    db_session.add_all(
        [
            earlier_blocked,
            later_new_same_physical_by_code,
            later_blocked_same_physical,
            other_device_ready,
            other_domain_blocked,
        ]
    )
    await db_session.flush()

    probeable = await SystemOutboxRepository().get_probeable_blocked_device_heads(
        db_session,
        limit=10,
        min_probe_interval_seconds=2,
        operation_domains=("WORKLINE", "RACK"),
    )
    pending = await SystemOutboxRepository().get_pending_messages(
        db_session,
        limit=10,
        operation_domains=("WORKLINE", "RACK"),
    )

    assert [item.dispatch_key for item in probeable] == ["device-command:mixed-earlier-blocked"]
    assert "device-command:mixed-later-blocked-by-code" not in {item.dispatch_key for item in probeable}
    assert "device-command:other-domain-blocked" not in {item.dispatch_key for item in probeable}
    assert "device-command:mixed-later-new-by-code" not in {item.dispatch_key for item in pending}
    assert "device-command:other-device-ready" in {item.dispatch_key for item in pending}


@pytest.mark.asyncio
async def test_device_fifo_ignores_soft_deleted_device_code_aliases(db_session) -> None:
    """device_code 可在软删除后复用，FIFO 解析只能使用 active device。"""

    now = timezone.now_for_db()
    session = WorklineSession(
        session_code="session-soft-deleted-device-alias",
        workline_id=45,
        plugin_key="test_workline_plugin",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.WAITING_DEVICE_RESULT,
    )
    active_device = Device(
        device_code="ARM-SOFT-DELETE-01",
        device_name="active arm",
        device_role="ROBOT_ARM",
        role_index=2,
    )
    db_session.add_all([session, active_device])
    await db_session.flush()
    # PostgreSQL 生产库使用部分唯一索引，软删除设备允许复用 device_code。
    # SQLite 单测库当前是普通唯一索引；这里用 SQL 直接构造同 code 旧行，
    # 验证 join 必须过滤 is_deleted。
    await db_session.execute(text("DROP INDEX IF EXISTS wes_biz.ux_devices_device_code_deleted"))
    await db_session.execute(
        text(
            """
            INSERT INTO wes_biz.devices (
                device_code, device_name, device_role, role_index, is_deleted, is_active,
                sort_order, protocol, timeout, device_status, maintenance_mode,
                max_concurrent_tasks, idempotency_ttl, version, created_at
            )
            VALUES (
                :device_code, 'deleted arm', 'ROBOT_ARM', 1, 1, 1,
                0, 'HTTP', 10000, 'IDLE', 0,
                1, 3600, 0, :created_at
            )
            """
        ).bindparams(device_code=active_device.device_code, created_at=now)
    )

    earlier_blocked = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        device_id=active_device.id,
        operation_domain="WORKLINE",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:active-device-blocked",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="legacy-active-device-alias",
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        blocked_reason="DEVICE_BUSY",
        blocked_at=now - timedelta(seconds=20),
        last_blocked_check_at=now - timedelta(seconds=10),
        created_at=now,
    )
    later_new_by_code = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        operation_domain="WORKLINE",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:later-active-device-code",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code=active_device.device_code,
        status=SystemOutboxStatus.NEW,
        created_at=now + timedelta(seconds=1),
    )
    db_session.add_all([earlier_blocked, later_new_by_code])
    await db_session.flush()

    pending = await SystemOutboxRepository().get_pending_messages(
        db_session,
        limit=10,
        operation_domains=("WORKLINE", "RACK"),
    )

    assert "device-command:later-active-device-code" not in {item.dispatch_key for item in pending}


@pytest.mark.asyncio
async def test_claim_blocked_resource_wait_for_dispatch_requires_same_status_and_reason() -> None:
    outbox = SimpleNamespace(
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        attempt_count=3,
        next_retry_at=None,
        last_error="设备 ARM01 正在执行任务",
        finished_at=object(),
        blocked_by_runtime_hold_id=None,
        blocked_by_reconciliation_session_id=None,
        blocked_device_id=7,
        blocked_workline_id=45,
        blocked_reason="DEVICE_BUSY",
        blocked_at=object(),
        last_blocked_check_at=object(),
        blocked_check_count=4,
        blocked_detail_json={"device_code": "ARM01", "last_probe_result": "BUSY"},
    )
    db = _FakeDb(outbox)
    repo = SystemOutboxRepository()

    claimed = await repo.claim_blocked_resource_wait_for_dispatch(  # type: ignore[arg-type]
        db,
        1,
        expected_reason="DEVICE_BUSY",
    )

    assert claimed is outbox
    assert outbox.status == SystemOutboxStatus.DISPATCHING
    assert outbox.next_retry_at is not None
    assert outbox.next_retry_at > timezone.now_for_db()
    assert outbox.last_error is None
    assert outbox.finished_at is None
    assert outbox.blocked_device_id is None
    assert outbox.blocked_workline_id is None
    assert outbox.blocked_reason is None
    assert outbox.blocked_at is None
    assert outbox.last_blocked_check_at is None
    assert outbox.blocked_check_count == 0
    assert outbox.blocked_detail_json == {}
    assert outbox.attempt_count == 3
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_claim_blocked_resource_wait_for_dispatch_revalidates_head_and_domain(db_session) -> None:
    now = timezone.now_for_db()
    session = WorklineSession(
        session_code="session-claim-blocked-resource-head",
        workline_id=45,
        plugin_key="test_workline_plugin",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.WAITING_DEVICE_RESULT,
    )
    device = Device(
        device_code="ARM-CLAIM-HEAD-01",
        device_name="claim head arm",
        device_role="ROBOT_ARM",
        role_index=1,
    )
    db_session.add_all([session, device])
    await db_session.flush()

    earlier_blocked = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        device_id=device.id,
        operation_domain="WORKLINE",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:claim-earlier-blocked",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code=device.device_code,
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        attempt_count=3,
        last_error="设备 ARM-CLAIM-HEAD-01 正在执行任务",
        blocked_device_id=device.id,
        blocked_workline_id=45,
        blocked_reason="DEVICE_BUSY",
        blocked_at=now - timedelta(seconds=20),
        last_blocked_check_at=now - timedelta(seconds=10),
        blocked_check_count=4,
        blocked_detail_json={"device_code": device.device_code, "last_probe_result": "BUSY"},
        created_at=now,
    )
    later_blocked = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        operation_domain="RACK",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:claim-later-blocked",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code=device.device_code,
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        blocked_reason="DEVICE_BUSY",
        blocked_at=now - timedelta(seconds=20),
        last_blocked_check_at=now - timedelta(seconds=10),
        created_at=now + timedelta(seconds=1),
    )
    other_domain_blocked = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        operation_domain="HANDLING",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:claim-other-domain",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="HANDLING-CLAIM-01",
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        blocked_reason="DEVICE_BUSY",
        blocked_at=now - timedelta(seconds=20),
        last_blocked_check_at=now - timedelta(seconds=10),
        created_at=now,
    )
    db_session.add_all([earlier_blocked, later_blocked, other_domain_blocked])
    await db_session.flush()

    repo = SystemOutboxRepository()

    wrong_reason = await repo.claim_blocked_resource_wait_for_dispatch(
        db_session,
        earlier_blocked.id,
        expected_reason="DEVICE_STATUS_PRECHECK_WAIT",
        operation_domains=("WORKLINE", "RACK"),
    )
    not_head = await repo.claim_blocked_resource_wait_for_dispatch(
        db_session,
        later_blocked.id,
        expected_reason="DEVICE_BUSY",
        operation_domains=("WORKLINE", "RACK"),
    )
    wrong_domain = await repo.claim_blocked_resource_wait_for_dispatch(
        db_session,
        other_domain_blocked.id,
        expected_reason="DEVICE_BUSY",
        operation_domains=("WORKLINE", "RACK"),
    )
    claimed = await repo.claim_blocked_resource_wait_for_dispatch(
        db_session,
        earlier_blocked.id,
        expected_reason="DEVICE_BUSY",
        operation_domains=("WORKLINE", "RACK"),
    )

    assert wrong_reason is None
    assert not_head is None
    assert wrong_domain is None
    assert claimed is earlier_blocked
    assert earlier_blocked.status == SystemOutboxStatus.DISPATCHING
    assert earlier_blocked.next_retry_at is not None
    assert earlier_blocked.next_retry_at > timezone.now_for_db()
    assert earlier_blocked.attempt_count == 3
    assert earlier_blocked.blocked_reason is None
    assert earlier_blocked.blocked_device_id is None
    assert earlier_blocked.blocked_workline_id is None
    assert earlier_blocked.blocked_at is None
    assert earlier_blocked.last_blocked_check_at is None
    assert earlier_blocked.blocked_check_count == 0
    assert earlier_blocked.blocked_detail_json == {}
    assert later_blocked.status == SystemOutboxStatus.BLOCKED_RESOURCE
    assert other_domain_blocked.status == SystemOutboxStatus.BLOCKED_RESOURCE


@pytest.mark.asyncio
async def test_mark_as_failed_uses_three_retry_backoff_then_exhausts() -> None:
    outbox = SimpleNamespace(
        status=SystemOutboxStatus.DISPATCHING,
        attempt_count=0,
        next_retry_at=None,
        last_error=None,
        finished_at=None,
    )
    db = _FakeDb(outbox)
    repo = SystemOutboxRepository()

    first = await repo.mark_as_failed(db, 1, "OUTBOX_ACK_TIMEOUT")
    first_retry = first.next_retry_at
    assert first.status == SystemOutboxStatus.NEW
    assert first.attempt_count == 1
    assert first_retry is not None

    second = await repo.mark_as_failed(db, 1, "OUTBOX_ACK_TIMEOUT")
    second_retry = second.next_retry_at
    assert second.status == SystemOutboxStatus.NEW
    assert second.attempt_count == 2
    assert second_retry is not None
    assert second_retry > first_retry

    third = await repo.mark_as_failed(db, 1, "OUTBOX_ACK_TIMEOUT")
    third_retry = third.next_retry_at
    assert third.status == SystemOutboxStatus.NEW
    assert third.attempt_count == 3
    assert third_retry is not None
    assert third_retry > second_retry

    exhausted = await repo.mark_as_failed(db, 1, "OUTBOX_ACK_TIMEOUT")
    assert exhausted.status == SystemOutboxStatus.FAILED
    assert exhausted.attempt_count == 4
    assert exhausted.next_retry_at is None
    assert exhausted.finished_at is not None


@pytest.mark.asyncio
async def test_mark_as_failed_does_not_overwrite_blocked_outbox() -> None:
    outbox = SimpleNamespace(
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        attempt_count=0,
        next_retry_at=None,
        last_error="CALLBACK_DEADLINE_EXPIRED",
        finished_at=None,
    )
    db = _FakeDb(outbox)

    updated = await SystemOutboxRepository().mark_as_failed(db, 1, "Dispatch failed")  # type: ignore[arg-type]

    assert updated is None
    assert outbox.status == SystemOutboxStatus.BLOCKED_RESOURCE
    assert outbox.attempt_count == 0
    assert outbox.last_error == "CALLBACK_DEADLINE_EXPIRED"
    assert outbox.finished_at is None
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_sandbox_pending_messages_excludes_terminal_sessions_and_keeps_sent_waiting_outbox(
    db_session,
) -> None:
    failed_session = WorklineSession(
        session_code="session-failed",
        workline_id=45,
        plugin_key="test_workline_plugin",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.FAILED,
        failure_domain="ORCHESTRATION",
        failure_code="DEVICE_BUSY",
        failure_message="设备正在执行任务",
    )
    waiting_session = WorklineSession(
        session_code="session-waiting",
        workline_id=45,
        plugin_key="test_workline_plugin",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.WAITING_DEVICE_RESULT,
    )
    db_session.add(failed_session)
    db_session.add(waiting_session)
    await db_session.flush()

    failed_outbox = SystemOutbox(
        session_id=failed_session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:failed",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM01",
        status=SystemOutboxStatus.SENT,
    )
    waiting_outbox = SystemOutbox(
        session_id=waiting_session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:waiting",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM02",
        status=SystemOutboxStatus.SENT,
    )
    blocked_outbox = SystemOutbox(
        session_id=waiting_session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:blocked-device",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM03",
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        blocked_device_id=7,
        blocked_reason="DEVICE_BUSY",
    )
    db_session.add(failed_outbox)
    db_session.add(waiting_outbox)
    db_session.add(blocked_outbox)
    await db_session.flush()

    pending = await SystemOutboxRepository().get_sandbox_pending_messages(db_session, workline_id=45)

    assert [item.dispatch_key for item in pending] == [
        "device-command:waiting",
        "device-command:blocked-device",
    ]


@pytest.mark.asyncio
async def test_get_sandbox_pending_messages_keeps_failed_outbox_history_for_open_session(db_session) -> None:
    session = WorklineSession(
        session_code="session-manual-hold",
        workline_id=45,
        plugin_key="test_workline_plugin",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.MANUAL_HOLD,
    )
    db_session.add(session)
    await db_session.flush()

    failed_outbox = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:failed-open-session",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM02",
        status=SystemOutboxStatus.FAILED,
        last_error="COMMAND_ACK_TIMEOUT",
    )
    db_session.add(failed_outbox)
    await db_session.flush()

    pending = await SystemOutboxRepository().get_sandbox_pending_messages(db_session, workline_id=45)

    assert "device-command:failed-open-session" in {item.dispatch_key for item in pending}


@pytest.mark.asyncio
async def test_cancel_active_by_session_closes_stale_sandbox_actions(db_session) -> None:
    session = WorklineSession(
        session_code="session-timeout",
        workline_id=45,
        plugin_key="test_workline_plugin",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.FAILED,
        failure_domain="ORCHESTRATION",
        failure_code="DEVICE_BUSY",
        failure_message="设备正在执行任务",
    )
    db_session.add(session)
    await db_session.flush()

    active_outbox = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:active",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM01",
        status=SystemOutboxStatus.SENT,
    )
    station_lease_outbox = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        operation_domain="RACK",
        operation_key="op-station-lease",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key="rack-operation:op-station-lease:1:ALLOCATE_AND_MOVE_RACK",
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        target_code="WMS_RCS_RACK_OPERATION",
        payload_json={
            "station": {"position_code": "SINGLE_LAYER_A"},
            "target_position_code": "SINGLE_LAYER_A",
        },
        status=SystemOutboxStatus.NEW,
    )
    terminal_outbox = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:terminal",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM02",
        status=SystemOutboxStatus.FAILED,
    )
    ownerless_station_lease = SystemOutbox(
        session_id=None,
        workline_id=45,
        operation_domain="RACK",
        operation_key="op-ownerless-station-lease",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key="rack-operation:op-ownerless-station-lease:1:ALLOCATE_AND_MOVE_RACK",
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        target_code="WMS_RCS_RACK_OPERATION",
        payload_json={
            "station": {"position_code": "SINGLE_LAYER_A"},
            "target_position_code": "SINGLE_LAYER_A",
        },
        status=SystemOutboxStatus.NEW,
    )
    db_session.add_all([active_outbox, station_lease_outbox, terminal_outbox, ownerless_station_lease])
    await db_session.flush()

    closed = await SystemOutboxRepository().cancel_active_by_session(
        db_session,
        session_id=session.id,
        reason="DEVICE_TIMEOUT",
    )

    assert closed == 2
    assert active_outbox.status == SystemOutboxStatus.CANCELLED
    assert active_outbox.last_error == "DEVICE_TIMEOUT"
    assert active_outbox.finished_at is not None
    assert station_lease_outbox.status == SystemOutboxStatus.CANCELLED
    assert station_lease_outbox.last_error == "DEVICE_TIMEOUT"
    assert station_lease_outbox.finished_at is not None
    assert terminal_outbox.status == SystemOutboxStatus.FAILED
    assert ownerless_station_lease.status == SystemOutboxStatus.NEW


@pytest.mark.asyncio
async def test_get_active_external_station_dispatch_keeps_finished_blocked_resource_lease(db_session) -> None:
    blocked_station_lease = SystemOutbox(
        session_id=601,
        workline_id=45,
        operation_domain="RACK",
        operation_key="op-blocked-station-lease",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key="rack-operation:op-blocked-station-lease:1:ALLOCATE_AND_MOVE_RACK",
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        target_code="WMS_RCS_RACK_OPERATION",
        payload_json={
            "station": {"position_code": "SINGLE_LAYER_A"},
            "target_position_code": "SINGLE_LAYER_A",
        },
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        blocked_reason="DEVICE_BUSY",
        finished_at=timezone.now_for_db(),
    )
    db_session.add(blocked_station_lease)
    await db_session.flush()

    active = await SystemOutboxRepository().get_active_external_station_dispatch(
        db_session,
        workline_id=45,
        position_code="SINGLE_LAYER_A",
    )

    assert active is not None
    assert active.dispatch_key == blocked_station_lease.dispatch_key


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [SystemOutboxStatus.FAILED, SystemOutboxStatus.CANCELLED])
async def test_get_active_external_station_dispatch_ignores_terminal_unfinished_lease(
    db_session,
    status: SystemOutboxStatus,
) -> None:
    terminal_station_lease = SystemOutbox(
        session_id=601,
        workline_id=45,
        operation_domain="RACK",
        operation_key=f"op-terminal-station-lease-{status.value.lower()}",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key=f"rack-operation:op-terminal-station-lease:1:{status.value}",
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        target_code="WMS_RCS_RACK_OPERATION",
        payload_json={
            "station": {"position_code": "SINGLE_LAYER_A"},
            "target_position_code": "SINGLE_LAYER_A",
        },
        status=status,
        finished_at=None,
    )
    db_session.add(terminal_station_lease)
    await db_session.flush()

    active = await SystemOutboxRepository().get_active_external_station_dispatch(
        db_session,
        workline_id=45,
        position_code="SINGLE_LAYER_A",
    )

    assert active is None


@pytest.mark.asyncio
async def test_get_active_external_station_dispatch_detects_move_out_source_station(db_session) -> None:
    move_out_station_lease = SystemOutbox(
        session_id=601,
        workline_id=45,
        operation_domain="RACK",
        operation_key="op-move-out-source-station",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key="rack-operation:op-move-out-source-station:1:MOVE_RACK",
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        target_code="WMS_RCS_RACK_OPERATION",
        payload_json={
            "task_type": "MOVE_RACK",
            "source_position_code": "SINGLE_LAYER_A",
            "source": {"position_code": "SINGLE_LAYER_A"},
            "target_position_code": None,
            "target": {"position_code": None, "position_role": "SMT_EMPTY_RACK_AREA"},
        },
        status=SystemOutboxStatus.NEW,
    )
    db_session.add(move_out_station_lease)
    await db_session.flush()

    active = await SystemOutboxRepository().get_active_external_station_dispatch(
        db_session,
        workline_id=45,
        position_code="SINGLE_LAYER_A",
    )

    assert active is not None
    assert active.dispatch_key == move_out_station_lease.dispatch_key


@pytest.mark.asyncio
async def test_finish_sent_external_by_dispatch_key_releases_station_dispatch_lease(db_session) -> None:
    sent_station_lease = SystemOutbox(
        session_id=601,
        workline_id=45,
        operation_domain="RACK",
        operation_key="op-sent-station-lease",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key="rack-operation:op-sent-station-lease:1:ALLOCATE_AND_MOVE_RACK",
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        target_code="WMS_RCS_RACK_OPERATION",
        payload_json={
            "station": {"position_code": "SINGLE_LAYER_A"},
            "target_position_code": "SINGLE_LAYER_A",
        },
        status=SystemOutboxStatus.SENT,
        finished_at=None,
    )
    db_session.add(sent_station_lease)
    await db_session.flush()

    active_before = await SystemOutboxRepository().get_active_external_station_dispatch(
        db_session,
        workline_id=45,
        position_code="SINGLE_LAYER_A",
    )
    finished = await SystemOutboxRepository().finish_sent_external_by_dispatch_key(
        db_session,
        sent_station_lease.dispatch_key,
    )
    active_after = await SystemOutboxRepository().get_active_external_station_dispatch(
        db_session,
        workline_id=45,
        position_code="SINGLE_LAYER_A",
    )

    assert active_before is not None
    assert active_before.dispatch_key == sent_station_lease.dispatch_key
    assert finished is sent_station_lease
    assert sent_station_lease.status == SystemOutboxStatus.SENT
    assert sent_station_lease.finished_at is not None
    assert active_after is None


@pytest.mark.asyncio
async def test_finish_external_by_dispatch_key_handles_callback_before_sent(db_session) -> None:
    early_callback_station_lease = SystemOutbox(
        session_id=601,
        workline_id=45,
        operation_domain="RACK",
        operation_key="op-early-callback-station-lease",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key="rack-operation:op-early-callback-station-lease:1:ALLOCATE_AND_MOVE_RACK",
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        target_code="WMS_RCS_RACK_OPERATION",
        payload_json={
            "station": {"position_code": "SINGLE_LAYER_A"},
            "target_position_code": "SINGLE_LAYER_A",
        },
        status=SystemOutboxStatus.DISPATCHING,
        finished_at=None,
    )
    db_session.add(early_callback_station_lease)
    await db_session.flush()

    finished = await SystemOutboxRepository().finish_sent_external_by_dispatch_key(
        db_session,
        early_callback_station_lease.dispatch_key,
    )
    sent_after_callback = await SystemOutboxRepository().mark_as_sent(
        db_session,
        early_callback_station_lease.id,
    )
    active_after = await SystemOutboxRepository().get_active_external_station_dispatch(
        db_session,
        workline_id=45,
        position_code="SINGLE_LAYER_A",
    )

    assert finished is early_callback_station_lease
    assert early_callback_station_lease.status == SystemOutboxStatus.SENT
    assert early_callback_station_lease.finished_at is not None
    assert sent_after_callback is None
    assert active_after is None


@pytest.mark.asyncio
async def test_release_blocked_by_reconciliation_session_requeues_only_owner_blocked_outbox(db_session) -> None:
    owner_session = WorklineSession(
        session_code="session-owner-reconcile",
        workline_id=45,
        plugin_key="test_workline_plugin",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.MANUAL_HOLD,
    )
    other_session = WorklineSession(
        session_code="session-other-reconcile",
        workline_id=45,
        plugin_key="test_workline_plugin",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.MANUAL_HOLD,
    )
    db_session.add_all([owner_session, other_session])
    await db_session.flush()

    owner_blocked = SystemOutbox(
        session_id=owner_session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:owner-blocked",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM01",
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        attempt_count=2,
        last_error="CALLBACK_DEADLINE_EXPIRED",
        blocked_by_reconciliation_session_id=owner_session.id,
        blocked_device_id=7,
        blocked_workline_id=45,
        blocked_reason="CALLBACK_DEADLINE_EXPIRED",
        blocked_at=timezone.now_for_db() - timedelta(seconds=20),
        last_blocked_check_at=timezone.now_for_db() - timedelta(seconds=10),
        blocked_check_count=2,
        blocked_detail_json={"device_code": "ARM01", "last_probe_result": "BLOCKED"},
    )
    other_blocked = SystemOutbox(
        session_id=other_session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:other-blocked",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM02",
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        blocked_by_reconciliation_session_id=other_session.id,
        blocked_reason="CALLBACK_DEADLINE_EXPIRED",
    )
    db_session.add_all([owner_blocked, other_blocked])
    await db_session.flush()

    released = await SystemOutboxRepository().release_blocked_by_reconciliation_session(
        db_session,
        owner_session.id,
    )

    assert released == 1
    assert owner_blocked.status == SystemOutboxStatus.NEW
    assert owner_blocked.attempt_count == 0
    assert owner_blocked.last_error is None
    assert owner_blocked.blocked_by_reconciliation_session_id is None
    assert owner_blocked.blocked_device_id is None
    assert owner_blocked.blocked_workline_id is None
    assert owner_blocked.blocked_reason is None
    assert owner_blocked.blocked_at is None
    assert owner_blocked.last_blocked_check_at is None
    assert owner_blocked.blocked_check_count == 0
    assert owner_blocked.blocked_detail_json == {}
    assert other_blocked.status == SystemOutboxStatus.BLOCKED_RESOURCE


@pytest.mark.asyncio
async def test_release_blocked_by_workline_keeps_device_resource_wait_outbox(db_session) -> None:
    session = WorklineSession(
        session_code="session-workline-release-resource-wait",
        workline_id=45,
        plugin_key="test_workline_plugin",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.MANUAL_HOLD,
    )
    db_session.add(session)
    await db_session.flush()
    now = timezone.now_for_db()

    device_busy = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:device-busy-workline-release",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM01",
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        attempt_count=3,
        last_error="设备 ARM01 正在执行任务",
        blocked_device_id=7,
        blocked_workline_id=45,
        blocked_reason="DEVICE_BUSY",
        blocked_at=now - timedelta(seconds=20),
        last_blocked_check_at=now - timedelta(seconds=10),
        blocked_check_count=2,
        blocked_detail_json={"device_code": "ARM01", "last_probe_result": "BUSY"},
    )
    status_wait = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:status-wait-workline-release",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM02",
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        attempt_count=4,
        last_error="设备 ARM02 实时状态查询返回 HTTP 503，等待下次预检",
        blocked_device_id=8,
        blocked_workline_id=45,
        blocked_reason="DEVICE_STATUS_PRECHECK_WAIT",
        blocked_at=now - timedelta(seconds=30),
        last_blocked_check_at=now - timedelta(seconds=5),
        blocked_check_count=5,
        blocked_detail_json={"device_code": "ARM02", "error_kind": "http_status"},
    )
    workline_blocked = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:workline-blocked-release",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM03",
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        attempt_count=2,
        last_error="WORKLINE_STOPPED_WAITING_START",
        blocked_workline_id=45,
        blocked_reason="WORKLINE_STOPPED_WAITING_START",
        blocked_at=now - timedelta(seconds=40),
        last_blocked_check_at=now - timedelta(seconds=15),
        blocked_check_count=6,
        blocked_detail_json={"reason": "workline stopped"},
    )
    db_session.add_all([device_busy, status_wait, workline_blocked])
    await db_session.flush()

    released = await SystemOutboxRepository().release_blocked_by_workline(db_session, 45)

    assert released == 1
    assert workline_blocked.status == SystemOutboxStatus.NEW
    assert workline_blocked.attempt_count == 0
    assert device_busy.status == SystemOutboxStatus.BLOCKED_RESOURCE
    assert device_busy.attempt_count == 3
    assert device_busy.last_error == "设备 ARM01 正在执行任务"
    assert device_busy.blocked_reason == "DEVICE_BUSY"
    assert device_busy.blocked_at == now - timedelta(seconds=20)
    assert device_busy.last_blocked_check_at == now - timedelta(seconds=10)
    assert device_busy.blocked_check_count == 2
    assert device_busy.blocked_detail_json == {"device_code": "ARM01", "last_probe_result": "BUSY"}
    assert status_wait.status == SystemOutboxStatus.BLOCKED_RESOURCE
    assert status_wait.attempt_count == 4
    assert status_wait.last_error == "设备 ARM02 实时状态查询返回 HTTP 503，等待下次预检"
    assert status_wait.blocked_reason == "DEVICE_STATUS_PRECHECK_WAIT"
    assert status_wait.blocked_at == now - timedelta(seconds=30)
    assert status_wait.last_blocked_check_at == now - timedelta(seconds=5)
    assert status_wait.blocked_check_count == 5
    assert status_wait.blocked_detail_json == {"device_code": "ARM02", "error_kind": "http_status"}


@pytest.mark.asyncio
async def test_release_parked_after_workline_start_keeps_device_resource_wait_outbox(db_session) -> None:
    session = WorklineSession(
        session_code="session-start-release-resource-wait",
        workline_id=45,
        plugin_key="test_workline_plugin",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.MANUAL_HOLD,
    )
    db_session.add(session)
    await db_session.flush()
    now = timezone.now_for_db()

    status_wait = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:status-wait-start-release",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM02",
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        attempt_count=4,
        last_error="设备 ARM02 实时状态查询返回 HTTP 503，等待下次预检",
        blocked_device_id=8,
        blocked_workline_id=45,
        blocked_reason="DEVICE_STATUS_PRECHECK_WAIT",
        blocked_at=now - timedelta(seconds=30),
        last_blocked_check_at=now - timedelta(seconds=5),
        blocked_check_count=5,
        blocked_detail_json={"device_code": "ARM02", "error_kind": "http_status"},
    )
    workline_blocked = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:workline-blocked-start-release",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM03",
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        attempt_count=2,
        last_error="WORKLINE_STOPPED_WAITING_START",
        blocked_workline_id=45,
        blocked_reason="WORKLINE_STOPPED_WAITING_START",
        blocked_at=now - timedelta(seconds=40),
        last_blocked_check_at=now - timedelta(seconds=15),
        blocked_check_count=6,
        blocked_detail_json={"reason": "workline stopped"},
    )
    db_session.add_all([status_wait, workline_blocked])
    await db_session.flush()

    released = await SystemOutboxRepository().release_parked_after_workline_start(db_session, 45)

    assert released == 1
    assert workline_blocked.status == SystemOutboxStatus.NEW
    assert workline_blocked.attempt_count == 0
    assert status_wait.status == SystemOutboxStatus.BLOCKED_RESOURCE
    assert status_wait.attempt_count == 4
    assert status_wait.last_error == "设备 ARM02 实时状态查询返回 HTTP 503，等待下次预检"
    assert status_wait.blocked_reason == "DEVICE_STATUS_PRECHECK_WAIT"
    assert status_wait.blocked_at == now - timedelta(seconds=30)
    assert status_wait.last_blocked_check_at == now - timedelta(seconds=5)
    assert status_wait.blocked_check_count == 5
    assert status_wait.blocked_detail_json == {"device_code": "ARM02", "error_kind": "http_status"}


@pytest.mark.asyncio
async def test_release_blocked_by_device_does_not_requeue_resource_wait_outbox(db_session) -> None:
    session = WorklineSession(
        session_code="session-device-busy-release",
        workline_id=45,
        plugin_key="test_workline_plugin",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.WAITING_DEVICE_RESULT,
    )
    db_session.add(session)
    await db_session.flush()

    device_blocked = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:device-busy",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM01",
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        attempt_count=1,
        last_error="设备 ARM01 正在执行任务",
        blocked_device_id=7,
        blocked_workline_id=45,
        blocked_reason="DEVICE_BUSY",
        blocked_at=timezone.now_for_db() - timedelta(seconds=20),
        last_blocked_check_at=timezone.now_for_db() - timedelta(seconds=10),
        blocked_check_count=2,
        blocked_detail_json={"device_code": "ARM01", "last_probe_result": "BUSY"},
    )
    reconciliation_blocked = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:reconcile",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM01",
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        blocked_device_id=7,
        blocked_workline_id=45,
        blocked_reason="CALLBACK_DEADLINE_EXPIRED",
    )
    db_session.add_all([device_blocked, reconciliation_blocked])
    await db_session.flush()

    released = await SystemOutboxRepository().release_blocked_by_device(db_session, device_id=7, workline_id=45)

    assert released == 0
    assert device_blocked.status == SystemOutboxStatus.BLOCKED_RESOURCE
    assert device_blocked.attempt_count == 1
    assert device_blocked.last_error == "设备 ARM01 正在执行任务"
    assert device_blocked.blocked_device_id == 7
    assert device_blocked.blocked_workline_id == 45
    assert device_blocked.blocked_reason == "DEVICE_BUSY"
    assert device_blocked.blocked_check_count == 2
    assert device_blocked.blocked_detail_json == {"device_code": "ARM01", "last_probe_result": "BUSY"}
    assert reconciliation_blocked.status == SystemOutboxStatus.BLOCKED_RESOURCE
    assert reconciliation_blocked.blocked_device_id == 7
    assert reconciliation_blocked.blocked_workline_id == 45
    assert reconciliation_blocked.blocked_reason == "CALLBACK_DEADLINE_EXPIRED"


@pytest.mark.asyncio
async def test_get_sandbox_completed_messages_includes_cancelled_terminal_outbox(db_session) -> None:
    session = WorklineSession(
        session_code="session-cancelled-outbox",
        workline_id=45,
        plugin_key="test_workline_plugin",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.FAILED,
        failure_domain="ORCHESTRATION",
        failure_code="DEVICE_BUSY",
        failure_message="设备正在执行任务",
    )
    db_session.add(session)
    await db_session.flush()

    outbox = SystemOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:cancelled",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM01",
        status=SystemOutboxStatus.CANCELLED,
        last_error="DEVICE_BUSY",
    )
    db_session.add(outbox)
    await db_session.flush()

    completed = await SystemOutboxRepository().get_sandbox_completed_messages(db_session, workline_id=45)

    assert completed[0]["session"]["id"] == session.id
    assert completed[0]["session"]["failure_domain"] == "ORCHESTRATION"
    assert completed[0]["session"]["failure_code"] == "DEVICE_BUSY"
    assert completed[0]["session"]["failure_message"] == "设备正在执行任务"
    assert completed[0]["history_group_key"] == f"session:{session.id}"
    assert completed[0]["outbox_items"][0]["dispatch_key"] == "device-command:cancelled"
    assert completed[0]["outbox_items"][0]["last_error"] == "DEVICE_BUSY"
    assert completed[0]["outbox_items"][0]["is_actionable"] is False
    assert completed[0]["outbox_items"][0]["history_group_key"] == f"session:{session.id}"
    assert completed[0]["outbox_items"][0]["failure_summary"] == {
        "code": "DEVICE_BUSY",
        "message": "设备正在执行任务",
        "runtime_hold_id": None,
    }
