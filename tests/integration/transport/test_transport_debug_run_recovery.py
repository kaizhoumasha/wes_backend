from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import delete

from src.app.transport.debug_run_repository import TransportDebugRunRepository
from src.app.transport.debug_run_service import TransportDebugRunService
from src.app.transport.models import TransportDebugRun, TransportDebugRunStep, TransportMember, TransportTask
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

pytestmark = pytest.mark.asyncio


class _Publisher:
    async def publish_to(self, channel: str, event_type: str, payload: dict[str, object]) -> bool:
        del channel, event_type, payload
        return True


async def test_recovery_reuses_same_task_and_creates_only_next_step(integration_session_factory: Any) -> None:
    suffix = uuid.uuid4().hex
    run_id = f"debug-recovery-{suffix}"
    task_id = f"transport-recovery-{suffix}"
    client_request_id = new_uuid7()
    now = timezone.now_for_db()
    run = TransportDebugRun(
        run_id=run_id,
        status="NEEDS_ATTENTION",
        active_scope="GLOBAL",
        rack_id="510056",
        configuration_json={
            "rack_id": "510056",
            "face_groups": [{"face": "90", "bins": [{"bin_id": "A", "slot_id": "S"}]}],
            "storage_zone": "WH01",
            "workstation": "KT16",
            "infeed_position": "CNV0301",
            "outfeed_position": "CNV0302",
            "rack_out_template": "CTU01",
            "rack_rotate_template": "CTU02",
            "rack_return_template": "CTU03",
            "rack_return_face": "90",
        },
        current_group_index=0,
        current_phase="RACK_TO_STATION",
        current_step_ordinal=0,
        attention_code="TRANSPORT_DELIVERY_UNKNOWN",
        version=1,
        created_by_user_id=7,
        created_at=now,
        updated_at=now,
    )
    step = TransportDebugRunStep(
        run_id=run_id,
        ordinal=0,
        group_index=0,
        phase="RACK_TO_STATION",
        status="NEEDS_ATTENTION",
        client_request_id=client_request_id,
        transport_task_id=task_id,
        reason_code="TRANSPORT_DELIVERY_UNKNOWN",
        created_at=now,
        updated_at=now,
    )
    task = TransportTask(
        transport_task_id=task_id,
        client_request_id=client_request_id,
        request_digest="0" * 64,
        kind="RACK_MOVE",
        caller_json={"workline_id": "TRANSPORT_DEBUG", "station_id": "TRANSPORT_DEBUG_AUTO"},
        request_json={},
        submit_operation_id=new_uuid7(),
        submit_timestamp_ms=1,
        submit_request_body="{}",
        submit_request_body_digest="1" * 64,
        status="SUCCEEDED",
        created_at=now,
        updated_at=now,
    )
    member = TransportMember(
        transport_task_id=task_id,
        ordinal=0,
        object_type="RACK",
        object_id="510056",
        source_json={"kind": "RACK", "location_code": "510056"},
        target_json={"kind": "RACK_POSITION", "location_code": "KT16"},
        status="SUCCEEDED",
        final_position_json={"kind": "RACK_POSITION", "location_code": "KT16"},
        arrival_face="90",
        updated_at=now,
    )
    async with integration_session_factory.begin() as db:
        db.add(task)
        await db.flush()
        db.add(run)
        await db.flush()
        db.add(step)
        db.add(member)

    repository = TransportDebugRunRepository()
    service = TransportDebugRunService(
        integration_session_factory,
        repository,
        SimpleNamespace(),
        event_publisher=_Publisher(),
    )
    try:
        assert await service.advance_run(run_id) is True
        async with integration_session_factory() as db:
            stored_run = await repository.get_run(db, run_id)
            steps = await repository.list_steps(db, run_id)
        assert stored_run is not None and stored_run.status == "RUNNING"
        assert [(item.ordinal, item.phase) for item in steps] == [
            (0, "RACK_TO_STATION"),
            (1, "BINS_TO_INFEED"),
        ]
        assert steps[0].client_request_id == client_request_id
        assert steps[0].transport_task_id == task_id
    finally:
        async with integration_session_factory.begin() as db:
            await db.execute(delete(TransportMember).where(TransportMember.transport_task_id == task_id))
            await db.execute(delete(TransportDebugRunStep).where(TransportDebugRunStep.run_id == run_id))
            await db.execute(delete(TransportDebugRun).where(TransportDebugRun.run_id == run_id))
            await db.execute(delete(TransportTask).where(TransportTask.transport_task_id == task_id))
