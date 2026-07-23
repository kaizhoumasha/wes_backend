"""WorklineDispatchAttempt 与 SystemOutbox owner lease 双围栏。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from src.app.device.models.command import DeviceCommand
from src.app.effect_ledger_status import DispatchAttemptStatus
from src.app.runtime.orchestration.services.inbox import dispatch_attempt_service as attempt_service_module
from src.app.runtime.orchestration.services.inbox.dispatch_attempt_service import WorklineDispatchAttemptService
from src.app.sys.models.outbox import (
    SystemOutbox,
    SystemOutboxDispatchType,
    SystemOutboxStatus,
    SystemOutboxTargetType,
)
from src.utils.timezone import timezone


def _outbox(*, owner: str, expires_in_seconds: int = 60) -> SystemOutbox:
    return SystemOutbox(
        provider_profile_identity="ecs.device-command.v1",
        operation_identity="device.command",
        operation_domain="DEVICE",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key=f"device-command:{owner}",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ROBOT-1",
        payload_json={"command_code": owner},
        status=SystemOutboxStatus.DISPATCHING,
        lease_owner_token=owner,
        lease_expires_at=timezone.now_for_db() + timedelta(seconds=expires_in_seconds),
    )


@pytest.mark.asyncio
async def test_attempt_mirrors_current_outbox_owner_and_expiry(db_session: Any) -> None:
    outbox = _outbox(owner="worker-a:lease-1")
    db_session.add(outbox)
    await db_session.flush()

    attempt = await WorklineDispatchAttemptService().create_attempt(db_session, outbox=outbox, auto_commit=False)

    assert attempt.lease_token == outbox.lease_owner_token
    assert attempt.lease_expires_at == outbox.lease_expires_at
    assert getattr(attempt.status, "value", attempt.status) == DispatchAttemptStatus.DISPATCHING.value


@pytest.mark.asyncio
async def test_attempt_finalize_rejects_wrong_or_expired_owner_without_mutation(db_session: Any) -> None:
    lease_lost_type = getattr(attempt_service_module, "OutboxLeaseLost", None)
    assert isinstance(lease_lost_type, type) and issubclass(lease_lost_type, RuntimeError)
    outbox = _outbox(owner="worker-a:lease-2")
    db_session.add(outbox)
    await db_session.flush()
    service = WorklineDispatchAttemptService()
    attempt = await service.create_attempt(db_session, outbox=outbox, auto_commit=False)

    with pytest.raises(lease_lost_type, match="OUTBOX_LEASE_LOST"):
        await service.finalize_attempt_record(
            db_session,
            attempt=attempt,
            lease_owner_token="worker-old:lease-lost",
            success=True,
            auto_commit=False,
        )
    assert getattr(attempt.status, "value", attempt.status) == DispatchAttemptStatus.DISPATCHING.value

    attempt.lease_expires_at = timezone.now_for_db() - timedelta(seconds=1)
    with pytest.raises(lease_lost_type, match="OUTBOX_LEASE_LOST"):
        await service.finalize_attempt_record(
            db_session,
            attempt=attempt,
            lease_owner_token="worker-a:lease-2",
            success=True,
            auto_commit=False,
        )
    assert getattr(attempt.status, "value", attempt.status) == DispatchAttemptStatus.DISPATCHING.value


@pytest.mark.asyncio
async def test_new_owner_attempt_cancels_expired_previous_attempt(db_session: Any) -> None:
    outbox = _outbox(owner="worker-old:lease", expires_in_seconds=60)
    db_session.add(outbox)
    await db_session.flush()
    service = WorklineDispatchAttemptService()
    old_attempt = await service.create_attempt(db_session, outbox=outbox, auto_commit=False)
    old_attempt.lease_expires_at = timezone.now_for_db() - timedelta(seconds=1)
    outbox.lease_owner_token = "worker-new:lease"
    outbox.lease_expires_at = timezone.now_for_db() + timedelta(seconds=60)

    new_attempt = await service.create_attempt(db_session, outbox=outbox, auto_commit=False)

    assert getattr(old_attempt.status, "value", old_attempt.status) == DispatchAttemptStatus.CANCELLED.value
    assert old_attempt.error_message == "OUTBOX_LEASE_REPLACED"
    assert old_attempt.response_json == {"lease_loss": True, "replacement_owner": "worker-new:lease"}
    assert new_attempt.lease_token == "worker-new:lease"
    assert new_attempt.attempt_no == old_attempt.attempt_no + 1
    assert getattr(new_attempt.status, "value", new_attempt.status) == DispatchAttemptStatus.DISPATCHING.value
