from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import delete

from src.app.transport.debug_run_repository import TransportDebugRunRepository
from src.app.transport.debug_run_service import TransportDebugRunService
from src.app.transport.models import TransportDebugRun, TransportDebugRunStep
from src.utils.timezone import timezone

pytestmark = pytest.mark.asyncio


class _Publisher:
    async def publish_to(self, channel: str, event_type: str, payload: dict[str, object]) -> bool:
        del channel, event_type, payload
        return True


class _Audit:
    async def create_audit_log(self, db: object, **values: Any) -> object:
        del db, values
        return object()


async def test_abort_persists_release_without_deleting_step(
    integration_session_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = f"debug-abort-{uuid.uuid4().hex}"
    now = timezone.now_for_db()
    run = TransportDebugRun(
        run_id=run_id,
        status="NEEDS_ATTENTION",
        active_scope="GLOBAL",
        rack_id="510056",
        configuration_json={
            "rack_id": "510056",
            "face_groups": [{"face": "90", "bins": [{"bin_id": "A", "slot_id": "S"}]}],
        },
        current_group_index=0,
        current_phase="WAIT_SCAN12",
        current_step_ordinal=0,
        attention_code="EVIDENCE_RECONCILING",
        version=1,
        created_by_user_id=2**40,
        created_at=now,
        updated_at=now,
    )
    step = TransportDebugRunStep(
        run_id=run_id,
        ordinal=0,
        group_index=0,
        phase="WAIT_SCAN12",
        status="NEEDS_ATTENTION",
        evidence_high_watermark=100,
        evidence_not_before_ms=1_725_000_000_000,
        reason_code="EVIDENCE_RECONCILING",
        created_at=now,
        updated_at=now,
    )
    async with integration_session_factory.begin() as db:
        db.add(run)
        db.add(step)

    service = TransportDebugRunService(
        integration_session_factory,
        TransportDebugRunRepository(),
        SimpleNamespace(),
        event_publisher=_Publisher(),
    )
    monkeypatch.setattr("src.app.transport.debug_run_service.audit_log_service", _Audit())
    try:
        snapshot = await service.abort_run(
            run_id,
            assertion="PHYSICAL_STATE_VERIFIED",
            reason="现场确认全部机构静止",
            actor_id=2**40 + 1,
        )
        assert snapshot.status == "ABORTED"
        async with integration_session_factory() as db:
            stored = await db.get(TransportDebugRun, run.id)
            stored_step = await db.get(TransportDebugRunStep, step.id)
        assert stored is not None and stored.active_scope is None
        assert stored.created_by_user_id == 2**40
        assert stored.aborted_by_user_id == 2**40 + 1
        assert stored_step is not None and stored_step.reason_code == "EVIDENCE_RECONCILING"
    finally:
        async with integration_session_factory.begin() as db:
            await db.execute(delete(TransportDebugRunStep).where(TransportDebugRunStep.run_id == run_id))
            await db.execute(delete(TransportDebugRun).where(TransportDebugRun.run_id == run_id))
