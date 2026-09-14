"""PickingTask 完成确认复用唯一可靠义务，读取时保持原响应身份。"""

from datetime import datetime
from types import SimpleNamespace

import pytest
import wes_plugin_sdk as sdk
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.app.execution.models import InboundEvidence, WmsConfirmation
from src.app.wms_integration.outbound_picking.services.picking_task_completion import (
    PickingTaskCompletionResultReader,
    PickingTaskCompletionScheduler,
)


@pytest.mark.asyncio
async def test_scheduler_freezes_typed_request_under_original_picking_task_owner() -> None:
    class Confirmations:
        def __init__(self) -> None:
            self.kwargs = None

        async def create_or_get(self, _db, **kwargs):  # type: ignore[no-untyped-def]
            self.kwargs = kwargs
            return SimpleNamespace(duplicate=False)

    confirmations = Confirmations()
    scheduler = PickingTaskCompletionScheduler(confirmations)
    now = datetime(2026, 9, 14, 4)
    intent = sdk.wms_operations.outbound_picking_task_completion_confirm(
        operation_id="019f0000-0000-7000-8000-000000000001", task_id="PICK-1", last_applied_plan_revision=2
    )
    await scheduler.create_in_session(object(), intent, picking_task_id=11, created_at=now)
    assert confirmations.kwargs["picking_task_id"] == 11
    assert confirmations.kwargs["request_payload"]["data"] == {"task_id": "PICK-1", "last_applied_plan_revision": 2}
    assert confirmations.kwargs["operation"] == "outbound.picking_task.completion_confirm@v1"


@pytest.mark.asyncio
async def test_result_reader_accepts_only_matched_persisted_completion_evidence() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def attach_schema(connection, _record):  # type: ignore[no-untyped-def]
        connection.execute("ATTACH DATABASE ':memory:' AS wes_biz")

    async with engine.begin() as connection:
        await connection.run_sync(InboundEvidence.__table__.create)
        await connection.run_sync(WmsConfirmation.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 9, 14, 4)
    operation = "outbound.picking_task.completion_confirm@v1"
    operation_id = "019f0000-0000-7000-8000-000000000001"
    try:
        async with sessions.begin() as db:
            evidence = InboundEvidence(
                kind="WMS_RESULT",
                source_identity=f"{operation}:{operation_id}",
                payload_digest="a" * 64,
                normalized_payload={
                    "operation_id": operation_id,
                    "code": "DECIDED",
                    "timestamp": 1789360000000,
                    "data": {"result": "COMPLETED"},
                },
                received_at=now,
                operation=operation,
                operation_id=operation_id,
                apply_status="APPLIED",
            )
            db.add(evidence)
            await db.flush()
            confirmation = WmsConfirmation(
                operation=operation,
                operation_id=operation_id,
                picking_task_id=11,
                request_digest="b" * 64,
                request_payload={
                    "operation": operation,
                    "operation_id": operation_id,
                    "timestamp": 1789360000000,
                    "data": {"task_id": "PICK-1", "last_applied_plan_revision": 2},
                },
                deadline_at=now,
                status="COMPLETED",
                completed_at=now,
                response_result="COMPLETED",
                response_evidence_id=evidence.id,
            )
            db.add(confirmation)
            await db.flush()
            snapshot = await PickingTaskCompletionResultReader().latest(db, 11)
            assert type(snapshot.outcome.result) is sdk.PickingTaskCompleted
            assert snapshot.plan_revision == 2
            confirmation.response_result = "BUSINESS_IN_PROGRESS"
            await db.flush()
            with pytest.raises(ValueError, match="result"):
                await PickingTaskCompletionResultReader().latest(db, 11)
            confirmation.response_result = "COMPLETED"
            evidence.operation_id = "different"
            await db.flush()
            with pytest.raises(ValueError, match="identity"):
                await PickingTaskCompletionResultReader().latest(db, 11)
    finally:
        await engine.dispose()
