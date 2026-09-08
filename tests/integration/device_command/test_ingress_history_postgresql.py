"""CallbackLog 混合 Evidence 历史分页的真实 PostgreSQL owner。"""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from src.app.callback.models import CallbackLog
from src.app.device.contracts import DeviceIngressAttempt
from src.app.device.services.device_ingress_history_service import DeviceIngressHistoryService
from src.app.execution.models import InboundEvidence
from src.utils.timezone import timezone

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_history_pages_attempts_and_legacy_evidence_with_current_status(integration_session_factory):
    suffix = uuid4().hex
    device = "D" * 60 + suffix
    now = timezone.now_for_db()
    request_ids = [f"history-{suffix}-{i}" for i in range(3)]
    evidence_ids = []
    service = DeviceIngressHistoryService(session_context=integration_session_factory)
    try:
        async with integration_session_factory.begin() as db:
            for index in range(2):
                evidence = InboundEvidence(
                    kind="DEVICE_EVENT",
                    source_identity=f"history-{suffix}-event-{index}",
                    payload_digest=str(index) * 64,
                    normalized_payload={"event_type": "SCAN_COMPLETED", "device_code": device},
                    device_code=device,
                    received_at=now,
                    apply_status="PENDING",
                )
                db.add(evidence)
                await db.flush()
                evidence_ids.append(evidence.id)
        for index in range(2):
            await service.record_attempt(
                DeviceIngressAttempt(
                    request_id=request_ids[index],
                    kind="DEVICE_EVENT",
                    path="/api/v1/callback/event",
                    received_at=timezone.to_utc(now).isoformat(),
                    disposition="DUPLICATE" if index else "ACCEPTED",
                    status_code=200,
                    evidence_id=evidence_ids[0],
                    source_event_id=f"history-{suffix}-event-0",
                    device_code=device,
                    apply_status="PENDING",
                    observed_body_bytes=1,
                    raw_payload={"secret": "[REDACTED]"},
                )
            )
        async with integration_session_factory.begin() as db:
            logs = list(await db.scalars(select(CallbackLog).where(CallbackLog.request_id.in_(request_ids))))
            for log in logs:
                log.created_at = now
            evidence = await db.get(InboundEvidence, evidence_ids[0])
            evidence.apply_status = "IGNORED"
            evidence.processed_at = now + timedelta(seconds=1)
        collected = []
        cursor = None
        for _ in range(4):
            page = await service.list_history(device_code=device, limit=1, cursor=cursor)
            collected.extend(page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert len(collected) == 3
        assert len({item.row_key for item in collected}) == 3
        assert [item.latest_update.apply_status for item in collected] == ["IGNORED", "IGNORED", "PENDING"]
        assert collected[-1].attempt is None
        assert collected[-1].latest_update.evidence_id == evidence_ids[1]
        assert collected[-1].latest_update.processed_at is None
        assert {item.attempt.disposition for item in collected[:2]} == {"ACCEPTED", "DUPLICATE"}
        page = await service.list_history(device_code=device, apply_status="IGNORED")
        assert len(page.items) == 2
        assert all(item.attempt.apply_status == "PENDING" for item in page.items)
        page = await service.list_history(device_code=device, apply_status="PENDING")
        assert [item.latest_update.evidence_id for item in page.items] == [evidence_ids[1]]
        assert not (await service.list_history(device_code=device, command_code="unrelated")).items
        assert not (await service.list_history(device_code=device, kind="DEVICE_RESULT")).items
    finally:
        async with integration_session_factory.begin() as db:
            await db.execute(delete(CallbackLog).where(CallbackLog.request_id.in_(request_ids)))
            await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_(evidence_ids)))
