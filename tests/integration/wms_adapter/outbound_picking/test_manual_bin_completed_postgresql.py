from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from src.app.execution.models import InboundEvidence, InboundEvidenceApplyStatus
from src.app.wms_adapter.outbound_picking.manual_bin_completed_wire import parse_manual_bin_completed_event
from src.app.wms_integration.outbound_picking.services.manual_bin_completed import ManualBinCompletedService
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.support.postgresql_heavy import migrated_database

pytestmark = pytest.mark.asyncio


async def test_manual_bin_completion_is_persisted_before_received_ack() -> None:
    operation_id = new_uuid7()
    operation = "outbound.manual_bin.work_completed@v1"
    event = parse_manual_bin_completed_event(
        {
            "operation_id": operation_id,
            "operation": operation,
            "timestamp": 1_788_390_000_000,
            "data": {
                "task_id": "PICK-001",
                "bin_code": "BIN-001",
                "result": "NORMAL",
                "completed_at": 1_788_389_999_000,
            },
        }
    )
    async with migrated_database() as (_url, sessions):
        service = ManualBinCompletedService(sessions)
        try:
            received = await service.record(event, received_at=timezone.now_for_db())
            duplicate = await service.record(event, received_at=timezone.now_for_db())
            async with sessions() as db:
                evidence = await db.scalar(
                    select(InboundEvidence).where(
                        InboundEvidence.operation == operation,
                        InboundEvidence.operation_id == operation_id,
                    )
                )

            assert received.code == "RECEIVED"
            assert duplicate.code == "DUPLICATE"
            assert evidence is not None
            assert evidence.apply_status == InboundEvidenceApplyStatus.PENDING
            assert evidence.normalized_payload["data"]["bin_code"] == "BIN-001"
        finally:
            async with sessions.begin() as db:
                await db.execute(
                    delete(InboundEvidence).where(
                        InboundEvidence.operation == operation,
                        InboundEvidence.operation_id == operation_id,
                    )
                )
