from types import SimpleNamespace

import pytest

from src.app.workline.models.runtime_hold import RuntimeHoldStatus, RuntimeHoldType
from src.app.workline.repositories.runtime_hold_repository import RuntimeHoldRepository
from src.app.workline.services.runtime_hold_creation_service import RuntimeHoldCreationService


@pytest.mark.asyncio
async def test_create_open_hold_is_idempotent_by_source_key(db_session) -> None:
    repo = RuntimeHoldRepository()

    first = await repo.create_open_hold(
        db_session,
        hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
        workline_id=45,
        source_kind="TIMER_TIMEOUT",
        source_reason="CALLBACK_DEADLINE_EXPIRED",
        source_idempotency_key="callback-timeout:1:10",
    )
    second = await repo.create_open_hold(
        db_session,
        hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
        workline_id=45,
        source_kind="TIMER_TIMEOUT",
        source_reason="CALLBACK_DEADLINE_EXPIRED",
        source_idempotency_key="callback-timeout:1:10",
    )

    assert first is second
    assert first.id is not None


@pytest.mark.asyncio
async def test_create_open_hold_returns_existing_after_unique_key_race(db_session, monkeypatch) -> None:
    repo = RuntimeHoldRepository()
    source_key = "callback-timeout:race:1"
    existing = await repo.create_open_hold(
        db_session,
        hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
        workline_id=45,
        source_kind="TIMER_TIMEOUT",
        source_reason="CALLBACK_DEADLINE_EXPIRED",
        source_idempotency_key=source_key,
    )

    original_get = repo.get_by_source_idempotency_key
    first_lookup = True

    async def stale_first_lookup(db, source_idempotency_key: str):
        nonlocal first_lookup
        if first_lookup:
            first_lookup = False
            return None
        return await original_get(db, source_idempotency_key)

    monkeypatch.setattr(repo, "get_by_source_idempotency_key", stale_first_lookup)

    raced = await repo.create_open_hold(
        db_session,
        hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
        workline_id=45,
        source_kind="TIMER_TIMEOUT",
        source_reason="CALLBACK_DEADLINE_EXPIRED",
        source_idempotency_key=source_key,
    )

    assert raced.id == existing.id


@pytest.mark.asyncio
async def test_get_active_blocking_by_workline_filters_terminal_and_nonblocking(db_session) -> None:
    repo = RuntimeHoldRepository()
    active = await repo.create_open_hold(
        db_session,
        hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
        workline_id=45,
        source_kind="TIMER_TIMEOUT",
        source_reason="CALLBACK_DEADLINE_EXPIRED",
        source_idempotency_key="callback-timeout:1:10",
    )
    resolved = await repo.create_open_hold(
        db_session,
        hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
        workline_id=45,
        source_kind="TIMER_TIMEOUT",
        source_reason="CALLBACK_DEADLINE_EXPIRED",
        source_idempotency_key="callback-timeout:2:20",
    )
    resolved.status = RuntimeHoldStatus.RESOLVED
    nonblocking = await repo.create_open_hold(
        db_session,
        hold_type=RuntimeHoldType.MANUAL_HOLD,
        workline_id=45,
        source_kind="MANUAL",
        source_reason="OPERATOR_NOTE",
        source_idempotency_key="manual:45:1",
        blocking=False,
    )
    await db_session.flush()

    holds = await repo.get_active_blocking_by_workline(db_session, 45)

    assert [hold.id for hold in holds] == [active.id]
    assert resolved.id not in [hold.id for hold in holds]
    assert nonblocking.id not in [hold.id for hold in holds]


@pytest.mark.asyncio
async def test_count_open_issues_by_device_groups_active_holds_only(db_session) -> None:
    repo = RuntimeHoldRepository()
    _ = await repo.create_open_hold(
        db_session,
        hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
        workline_id=45,
        source_kind="DISPATCH_ACK_EXHAUSTED",
        source_reason="COMMAND_ACK_EXHAUSTED",
        source_idempotency_key="dispatch-ack-exhausted:1:2",
        source_device_id=7,
    )
    resolved = await repo.create_open_hold(
        db_session,
        hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
        workline_id=45,
        source_kind="TIMER_TIMEOUT",
        source_reason="CALLBACK_DEADLINE_EXPIRED",
        source_idempotency_key="callback-timeout:2:20",
        source_device_id=7,
    )
    resolved.status = RuntimeHoldStatus.RESOLVED
    _ = await repo.create_open_hold(
        db_session,
        hold_type=RuntimeHoldType.SAFETY_ESTOP,
        workline_id=45,
        source_kind="SAFETY_ESTOP",
        source_reason="ESTOP_PRESSED",
        source_idempotency_key="safety-estop:11",
        source_device_id=8,
    )
    await db_session.flush()

    counts = await repo.count_open_issues_by_device(db_session, device_ids=[7, 8, 9])

    assert counts == {7: 1, 8: 1}


@pytest.mark.asyncio
async def test_creation_service_builds_callback_timeout_hold_key_and_snapshot(db_session) -> None:
    service = RuntimeHoldCreationService()
    session = SimpleNamespace(
        id=91,
        workline_id=45,
        trace_id="trace-001",
        plugin_key="smt_classifier",
        contract_version="1.0",
        reconciliation_deadline_at=None,
        reconciliation_wait_token="COMMAND_RESULT:CMD-1",
    )
    inbox = SimpleNamespace(id=10, payload_json={"kind": "TIMER_TIMEOUT"})
    command = SimpleNamespace(id=501, device_id=7, command_code="CMD-001")

    hold = await service.create_for_callback_deadline_expired(
        db_session,
        session=session,
        inbox=inbox,
        command=command,
    )

    assert hold.source_idempotency_key == "callback-timeout:91:10"
    assert hold.source_inbox_id == 10
    assert hold.source_command_id == 501
    assert hold.source_device_id == 7
    assert hold.evidence_snapshot_json["command_code"] == "CMD-001"


@pytest.mark.asyncio
async def test_creation_service_builds_dispatch_ack_exhausted_hold_key(db_session) -> None:
    service = RuntimeHoldCreationService()
    session = SimpleNamespace(
        id=91,
        workline_id=45,
        trace_id="trace-001",
        plugin_key="smt_classifier",
        contract_version="1.0",
    )
    outbox = SimpleNamespace(id=12, dispatch_key="device-command:CMD-001", payload_json={})
    command = SimpleNamespace(id=501, device_id=7, command_code="CMD-001")

    hold = await service.create_for_dispatch_ack_exhausted(
        db_session,
        session=session,
        outbox=outbox,
        command=command,
        source_reason="COMMAND_ACK_EXHAUSTED",
    )

    assert hold.source_idempotency_key == "dispatch-ack-exhausted:12:501"
    assert hold.source_outbox_id == 12
    assert hold.source_command_id == 501
    assert hold.source_device_id == 7


@pytest.mark.asyncio
async def test_creation_service_builds_safety_estop_hold_key(db_session) -> None:
    service = RuntimeHoldCreationService()
    incident = SimpleNamespace(
        id=31,
        workline_id=45,
        source_inbox_id=10,
        source_device_id=7,
        source_command_id=None,
        reason="ESTOP_PRESSED",
        event_type="ESTOP_PRESSED",
        evidence_json={"drain": "PENDING"},
    )

    hold = await service.create_for_safety_estop(db_session, incident=incident)

    assert hold.source_idempotency_key == "safety-estop:31"
    assert hold.hold_type == RuntimeHoldType.SAFETY_ESTOP
    assert hold.source_reason == "ESTOP_PRESSED"
    assert hold.evidence_snapshot_json["incident_id"] == 31
