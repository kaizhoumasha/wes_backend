from datetime import datetime
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest

from src.app.execution.models import InboundEvidenceKind, WmsConfirmationStatus
from src.utils.canonical_json import canonical_json_digest
from tests.contracts.wms_adapter.return_buffer_drain.test_contract import (
    OPERATION,
    OPERATION_ID,
    intent,
    request,
    response,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["valid", "missing", "inactive", "id", "line"])
async def test_owner_locks_exact_workline(case):
    from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainOwnerService

    line = SimpleNamespace(id=7, line_code="LINE-1", is_active=True, plugin_key="manual-picking")
    if case == "missing":
        line = None
    elif case == "inactive":
        line.is_active = False
    elif case == "id":
        line.id = 8
    elif case == "line":
        line.line_code = "OTHER"
    worklines = AsyncMock()
    worklines.get_for_update.return_value = line
    db = object()
    owner = ReturnBufferDrainOwnerService(worklines)
    assert await owner.validate_owner(db, workline_id=7, request_payload=request()) is (case == "valid")
    worklines.get_for_update.assert_awaited_once_with(db, 7, populate_existing=True)


@pytest.mark.asyncio
async def test_owner_returns_false_for_malformed_request_payload():
    from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainOwnerService

    worklines = AsyncMock()
    db = object()
    owner = ReturnBufferDrainOwnerService(worklines)
    assert await owner.validate_owner(db, workline_id=7, request_payload={"bad": "payload"}) is False
    worklines.get_for_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_deployment_composes_drain_owner_even_without_plugins(monkeypatch):
    from deployment import plugin_composition

    owner = AsyncMock()
    owner.validate_owner.return_value = True
    monkeypatch.setattr(plugin_composition, "ReturnBufferDrainOwnerService", lambda: owner)
    monkeypatch.setattr(plugin_composition, "build_execution_runtime", lambda **kwargs: SimpleNamespace(**kwargs))
    transport = SimpleNamespace(service=object(), client=AsyncMock(), position_projection_service=object())
    runtime = plugin_composition.build_deployment_runtime(
        session_factory=object(),
        transport_runtime=transport,
        device_command_service=object(),
        enabled_plugin_keys=(),
    )
    assert runtime.plugins == ()
    assert await runtime.execution.workline_owner.validate_owner(object(), workline_id=7, request_payload=request())
    owner.validate_owner.assert_awaited_once()


@pytest.mark.asyncio
async def test_scheduler_delegates_frozen_workline_obligation_to_lifecycle():
    from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainScheduler

    lifecycle = AsyncMock()
    scheduler = ReturnBufferDrainScheduler(lifecycle)
    now = datetime(2026, 9, 15)
    db = object()
    await scheduler.create_in_session(db, intent(), workline_id=7, created_at=now)
    lifecycle.create_or_get.assert_awaited_once()
    kwargs = lifecycle.create_or_get.await_args.kwargs
    assert kwargs["operation"] == OPERATION
    assert kwargs["operation_id"] == OPERATION_ID
    assert kwargs["workline_id"] == 7
    assert kwargs["request_payload"]["data"] == request()["data"]
    assert "picking_task_id" not in kwargs
    assert kwargs["deadline_at"] > now


@pytest.mark.asyncio
async def test_scheduler_uses_lifecycle_idempotency_and_preserves_original_on_drift():
    from src.app.execution.services.wms_confirmation_service import (
        WmsConfirmationIdentityConflictError,
        WmsConfirmationLifecycleService,
    )
    from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainScheduler
    from tests.runtime.execution.test_wms_confirmation_service import FakeWmsConfirmationRepository

    repository = FakeWmsConfirmationRepository()
    owner = AsyncMock()
    owner.validate_owner.return_value = True
    lifecycle = WmsConfirmationLifecycleService(repository=repository, workline_owner=owner)
    scheduler = ReturnBufferDrainScheduler(lifecycle)
    now = datetime(2026, 9, 15)
    await scheduler.create_in_session(object(), intent(), workline_id=7, created_at=now)
    await scheduler.create_in_session(object(), intent(), workline_id=7, created_at=now)
    assert len(repository.confirmations) == 1
    owner.validate_owner.assert_awaited_once()
    with pytest.raises(WmsConfirmationIdentityConflictError):
        await scheduler.create_in_session(object(), intent(required_slot_count=2), workline_id=7, created_at=now)
    original = repository.confirmations[(OPERATION, OPERATION_ID)]
    assert original.request_payload["data"]["required_slot_count"] == 3
    assert original.status == WmsConfirmationStatus.RECONCILING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "status",
        "owner",
        "evidence_owner",
        "evidence_id",
        "kind",
        "operation",
        "identity",
        "request_identity",
        "request_digest",
        "response_identity",
        "response_digest",
    ],
)
async def test_result_reader_requires_completed_correlated_confirmation_and_evidence(case):
    from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainResultReader

    payload = request()
    answer = response()
    confirmation = SimpleNamespace(
        operation=OPERATION,
        operation_id=OPERATION_ID,
        workline_id=7,
        status=WmsConfirmationStatus.COMPLETED,
        response_evidence_id=9,
        request_payload=payload,
        request_digest=canonical_json_digest(payload),
        response_result="READY",
    )
    evidence = SimpleNamespace(
        id=9,
        operation=OPERATION,
        operation_id=OPERATION_ID,
        workline_id=7,
        kind=InboundEvidenceKind.WMS_RESULT,
        normalized_payload=answer,
        payload_digest=canonical_json_digest(answer),
    )
    if case == "status":
        confirmation.status = WmsConfirmationStatus.PENDING
    elif case == "owner":
        confirmation.workline_id = 8
    elif case == "evidence_owner":
        evidence.workline_id = 8
    elif case == "evidence_id":
        evidence.id = 10
    elif case == "kind":
        evidence.kind = InboundEvidenceKind.WMS_EVENT
    elif case == "operation":
        evidence.operation = "other"
    elif case == "identity":
        confirmation.operation_id = "other"
    elif case == "request_identity":
        payload["operation_id"] = "019f3406-2200-7b03-8b01-000000000001"
    elif case == "request_digest":
        confirmation.request_digest = "0" * 64
    elif case == "response_identity":
        answer["operation_id"] = "019f3406-2200-7b03-8b01-000000000001"
    elif case == "response_digest":
        evidence.payload_digest = "0" * 64
    repository = AsyncMock()
    repository.get_by_identity.return_value = confirmation
    repository.get_by_identity_for_update.side_effect = AssertionError(
        "WorkLine-held reader must not lock Confirmation"
    )
    reader = ReturnBufferDrainResultReader(repository)
    if case == "valid":
        original, outcome = await reader.read(object(), evidence, workline_id=7)
        assert original == intent()
        assert outcome.result.racks[0].rack_id == "RACK-2"
        repository.get_by_identity.assert_awaited_once_with(ANY, OPERATION, OPERATION_ID)
        repository.get_by_identity_for_update.assert_not_awaited()
    else:
        with pytest.raises(ValueError):
            await reader.read(object(), evidence, workline_id=7)


@pytest.mark.asyncio
async def test_confirmation_snapshot_identity_read_does_not_acquire_row_lock():
    from sqlalchemy.dialects.postgresql import dialect

    from src.app.execution.repositories.wms_confirmation_repository import WmsConfirmationRepository

    confirmation = object()

    class WorkLineLockedSession:
        async def execute(self, statement):
            sql = str(statement.compile(dialect=dialect()))
            assert "FOR UPDATE" not in sql
            assert "SKIP LOCKED" not in sql
            assert statement.compile().params == {"operation_1": OPERATION, "operation_id_1": OPERATION_ID}
            return SimpleNamespace(scalar_one_or_none=lambda: confirmation)

    assert (
        await WmsConfirmationRepository().get_by_identity(WorkLineLockedSession(), OPERATION, OPERATION_ID)
        is confirmation
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", [False, True])
async def test_history_joins_completed_evidence_once_and_validates_before_typed_record(drift):
    from dataclasses import FrozenInstanceError

    from sqlalchemy.dialects.postgresql import dialect

    from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainRecord, ReturnBufferDrainResultReader

    payload, answer = request(), response()
    confirmation = SimpleNamespace(
        id=1,
        operation=OPERATION,
        operation_id=OPERATION_ID,
        workline_id=7,
        status="COMPLETED",
        created_at=datetime(2026, 9, 15),
        completed_at=datetime(2026, 9, 15),
        request_payload=payload,
        request_digest=canonical_json_digest(payload),
        response_evidence_id=9,
        response_result="READY",
    )
    evidence = SimpleNamespace(
        id=9,
        operation=OPERATION,
        operation_id=OPERATION_ID,
        workline_id=7,
        kind=InboundEvidenceKind.WMS_RESULT,
        normalized_payload=answer,
        payload_digest=canonical_json_digest(answer),
        published_at=datetime(2026, 9, 15),
    )
    if drift:
        evidence.payload_digest = "f" * 64

    class Db:
        calls = 0

        async def execute(self, statement):
            self.calls += 1
            sql = str(statement.compile(dialect=dialect()))
            assert "JOIN wes_biz.inbound_evidences" in sql
            assert "FOR UPDATE" not in sql
            return SimpleNamespace(all=lambda: [(confirmation, evidence)])

    db = Db()
    if drift:
        with pytest.raises(ValueError):
            await ReturnBufferDrainResultReader().history(db, workline_id=7)
    else:
        records = await ReturnBufferDrainResultReader().history(db, workline_id=7)
        assert type(records) is tuple and type(records[0]) is ReturnBufferDrainRecord
        assert records[0].intent == intent()
        assert records[0].result.racks[0].rack_id == "RACK-2"
        assert not hasattr(records[0], "request_payload")
        with pytest.raises(FrozenInstanceError):
            records[0].evidence_id = 20
    assert db.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("checkpoint", [False, True])
async def test_history_uses_operation_identity_index_for_tail_and_exact_checkpoint(checkpoint):
    from sqlalchemy.dialects.postgresql import dialect

    from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainResultReader

    class Db:
        async def execute(self, statement):
            sql = str(statement.compile(dialect=dialect(), compile_kwargs={"literal_binds": True}))
            assert "ORDER BY wes_biz.wms_confirmations.operation_id DESC" in sql
            assert "LIMIT 2" in sql
            assert "ORDER BY wes_biz.wms_confirmations.created_at" not in sql
            if checkpoint:
                assert f"wms_confirmations.operation_id > '{OPERATION_ID}'" in sql
                assert f"wms_confirmations.operation_id = '{OPERATION_ID}'" in sql
                assert "wms_confirmations.response_evidence_id =" not in sql
            return SimpleNamespace(all=list)

    if checkpoint:
        with pytest.raises(ValueError, match="checkpoint"):
            await ReturnBufferDrainResultReader().history(Db(), workline_id=7, after_operation_id=OPERATION_ID)
    else:
        assert await ReturnBufferDrainResultReader().history(Db(), workline_id=7) == ()
