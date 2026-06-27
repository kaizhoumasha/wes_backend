from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.workline.models.material_unit import MaterialUnitStatus
from src.app.workline.services import write_back_service as workline_effects
from src.app.workline.services.ng_return_item_service import ng_return_item_service
from src.workline_runtime.effect_result import WriteBackDisposition
from src.workline_runtime.orchestrator import OrchestratorResult
from src.workline_runtime.runtime_intent import BlockScope, Destination, RuntimeIntent
from src.workline_runtime.runtime_intent_effects import (
    RuntimeIntentEffectApplier,
)
from tests.workline_runtime.support.runtime_intent_effects import (
    MaterialUnitDb,
    RecordingBinCellReservationService,
    RecordingRackOperationStatusService,
    RecordingResourceProjectionService,
    _ctx,
    _session,
)


@pytest.mark.asyncio
async def test_resource_fact_syncs_waiting_rack_operation_status() -> None:
    session = _session(
        context_json={
            "waiting_rack_operation_key": "rack-operation:trace-runtime",
            "rack_operation": {"operation_key": "rack-operation:trace-runtime", "status": "PENDING"},
        }
    )
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    resource_projection = RecordingResourceProjectionService()
    rack_operation_service = RecordingRackOperationStatusService()

    await RuntimeIntentEffectApplier(
        resource_projection_service=resource_projection,
        rack_operation_service=rack_operation_service,
    ).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="RACK_ARRIVED",
                payload={"rack_code": "RACK-3CELL-001", "position_code": "SINGLE_LAYER_A"},
                idempotency_key="RACK_ARRIVED:rack-operation:trace-runtime",
            )
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "RACK_ARRIVED"
    assert rack_operation_service.calls == [
        {"db": ctx["db"], "operation_key": "rack-operation:trace-runtime"},
    ]


@pytest.mark.asyncio
async def test_reconciling_resource_fact_stops_following_intents(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(context_json={"pkg_id": "PKG-001"})
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    emit_timeline = AsyncMock()
    record_ng_flow = AsyncMock()
    resource_projection = RecordingResourceProjectionService(status="RECONCILING")

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", record_ng_flow)

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="RACK_ARRIVED",
                payload={"rack_code": "RACK-001", "workline_code": "WL-001", "position_code": "SINGLE_LAYER_A"},
                idempotency_key="RACK_ARRIVED:conflict",
            ),
            RuntimeIntent.update_context({"rack_operation": {"status": "ARRIVED"}}),
            RuntimeIntent.complete({"material_mounted": True}),
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "RACK_ARRIVED"
    assert session.context_json == {"pkg_id": "PKG-001"}
    assert session.status == "MANUAL_HOLD"
    assert session.failure_code == "RESOURCE_PROJECTION_RECONCILING"
    record_ng_flow.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_resource_fact_skips_following_material_unit_status_update() -> None:
    material_unit = SimpleNamespace(
        id=1001,
        status=MaterialUnitStatus.IN_TRANSIT,
        current_location="LATEST-BIN:1",
        current_session_id=801,
    )
    session = _session(id=902, current_material_unit_id=1001, context_json={})
    db = MaterialUnitDb(material_unit=material_unit)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    resource_projection = RecordingResourceProjectionService(status="DUPLICATE")

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="MATERIAL_MOUNTED",
                payload={
                    "bin_code": "OLD-BIN",
                    "bin_cell_index": "9",
                    "material_identity_key": "MAT:OLD",
                    "pkg_code": "PKG-OLD",
                },
                idempotency_key="MATERIAL_MOUNTED:old",
            ),
            RuntimeIntent.update_material_unit_status(
                material_unit_id=1001,
                status=MaterialUnitStatus.STORED.value,
                current_location="OLD-BIN:9",
            ),
            RuntimeIntent.update_context({"resource_fact_replayed": True}),
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "MATERIAL_MOUNTED"
    assert material_unit.status == MaterialUnitStatus.IN_TRANSIT
    assert material_unit.current_location == "LATEST-BIN:1"
    assert material_unit.current_session_id == 801
    assert session.context_json == {"resource_fact_replayed": True}


@pytest.mark.asyncio
async def test_duplicate_resource_fact_skips_following_material_unit_creation() -> None:
    existing = SimpleNamespace(
        id=1002,
        pkg_code="PKG-001",
        material_identity_key="latest-key",
        six_in_one={"PkgID": "PKG-001", "latest": True},
        status=MaterialUnitStatus.STORED,
        current_location="LATEST-BIN:1",
        current_session_id=800,
    )
    session = _session(id=903, current_material_unit_id=1002, context_json={})
    db = MaterialUnitDb(material_unit=existing)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    resource_projection = RecordingResourceProjectionService(status="DUPLICATE")

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="MATERIAL_UNMOUNTED",
                payload={
                    "bin_code": "OLD-BIN",
                    "bin_cell_index": "9",
                    "material_identity_key": "MAT:OLD",
                    "pkg_code": "PKG-001",
                },
                idempotency_key="MATERIAL_UNMOUNTED:old",
            ),
            RuntimeIntent.create_material_unit(
                pkg_code="PKG-001",
                material_identity_key="old-key",
                six_in_one={"PkgID": "PKG-001", "old": True},
                status=MaterialUnitStatus.IN_TRANSIT.value,
            ),
            RuntimeIntent.update_context({"source_pick_replayed": True}),
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "MATERIAL_UNMOUNTED"
    assert db.added == []
    assert existing.material_identity_key == "latest-key"
    assert existing.six_in_one == {"PkgID": "PKG-001", "latest": True}
    assert existing.status == MaterialUnitStatus.STORED
    assert existing.current_location == "LATEST-BIN:1"
    assert existing.current_session_id == 800
    assert session.context_json == {"source_pick_replayed": True}


@pytest.mark.asyncio
async def test_duplicate_resource_fact_skip_only_applies_to_immediate_material_unit_intent() -> None:
    material_unit = SimpleNamespace(
        id=1001,
        status=MaterialUnitStatus.IN_TRANSIT,
        current_location=None,
        current_session_id=None,
    )
    session = _session(id=902, current_material_unit_id=1001, context_json={})
    db = MaterialUnitDb(material_unit=material_unit)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    resource_projection = RecordingResourceProjectionService(status="DUPLICATE")

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="MATERIAL_MOUNTED",
                payload={
                    "bin_code": "OLD-BIN",
                    "bin_cell_index": "9",
                    "material_identity_key": "MAT:OLD",
                    "pkg_code": "PKG-OLD",
                },
                idempotency_key="MATERIAL_MOUNTED:old",
            ),
            RuntimeIntent.update_context({"resource_fact_replayed": True}),
            RuntimeIntent.update_material_unit_status(
                material_unit_id=1001,
                status=MaterialUnitStatus.STORED.value,
                current_location="NEW-BIN:1",
            ),
        ],
    )

    assert session.context_json == {"resource_fact_replayed": True}
    assert material_unit.status == MaterialUnitStatus.STORED
    assert material_unit.current_location == "NEW-BIN:1"
    assert material_unit.current_session_id == 902


@pytest.mark.asyncio
async def test_reconciling_resource_fact_moves_session_to_manual_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    material_unit = SimpleNamespace(
        id=1001,
        pkg_code="PKG-001",
        status=MaterialUnitStatus.STORED,
        reconciliation_from_state=None,
        current_location="BIN-001:1",
        current_session_id=123,
    )
    session = _session(context_json={"pkg_id": "PKG-001"}, current_material_unit_id=1001)
    db = MaterialUnitDb(material_unit=material_unit)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    emit_timeline = AsyncMock()
    persist_manual_hold = AsyncMock()
    resource_projection = RecordingResourceProjectionService(status="RECONCILING")

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(
        "src.app.workline.repositories.session_repository.WorklineSessionRepository.persist_manual_hold",
        persist_manual_hold,
    )

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="BIN_MOUNTED",
                payload={"rack_code": "RACK-3CELL-001", "bin_mounts": []},
                idempotency_key="BIN_MOUNTED:conflict",
            ),
            RuntimeIntent.complete({"material_mounted": True}),
        ],
    )

    assert session.status == "MANUAL_HOLD"
    assert session.current_wait_type is None
    assert session.failure_domain == "RESOURCE_RECONCILIATION"
    assert session.failure_code == "RESOURCE_PROJECTION_RECONCILING"
    assert session.failure_message == "资源事实投影进入调和状态，等待人工处理 RuntimeHold"
    assert material_unit.status == MaterialUnitStatus.RECONCILING
    assert material_unit.reconciliation_from_state == MaterialUnitStatus.STORED
    persist_manual_hold.assert_awaited_once_with(
        ctx["db"],
        session_id=session.id,
        occurred_at=ctx["now"],
        failure_domain="RESOURCE_RECONCILIATION",
        failure_code="RESOURCE_PROJECTION_RECONCILING",
        failure_message="资源事实投影进入调和状态，等待人工处理 RuntimeHold",
    )


@pytest.mark.asyncio
async def test_resource_reservation_intent_is_applied_before_command(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SimpleNamespace(id=1, device_code="CONV01", device_role="CONVEYOR")
    target = SimpleNamespace(id=2, device_code="OUT01", device_role="OUTPUT_ARM", upstream_device_id=1)
    created_payloads: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["source_device"] = source
    ctx["devices_by_role"] = {"CONVEYOR": [source], "OUTPUT_ARM": [target]}
    reservation_service = RecordingBinCellReservationService()

    async def fake_create(_repo, _db, payload):
        created_payloads.append(payload)
        return SimpleNamespace(
            id=88,
            command_code="CMD-OUTPUT",
            task_type="PICK_AND_PUT",
            priority=5,
            timeout_ms=30000,
            params={},
        )

    monkeypatch.setattr(workline_effects, "_enforce_device_command_governance", lambda *_, **__: None)
    monkeypatch.setattr("src.app.device.repositories.command_repository.DeviceCommandRepository.create", fake_create)
    monkeypatch.setattr(workline_effects, "_emit_timeline", AsyncMock())

    await RuntimeIntentEffectApplier(bin_cell_reservation_service=reservation_service).apply(
        ctx,
        [
            RuntimeIntent.resource_reservation(
                operation="CLAIM_BIN_CELL",
                payload={"pkg_code": "PKG-001", "bin_code": "BIN-001", "bin_cell_index": "4"},
                idempotency_key="CLAIM_BIN_CELL:123:BIN-001:4:PKG-001",
            ),
            RuntimeIntent.command(
                action="PICK_AND_PUT",
                payload={"barcode": "PKG-001", "bin_id": "BIN-001", "bin_cell_index": "4"},
                destination=Destination.role("OUTPUT_ARM"),
                timeout_seconds=300,
            ),
        ],
    )

    assert reservation_service.calls[0]["operation"] == "CLAIM_BIN_CELL"
    assert reservation_service.calls[0]["session"] is session
    assert created_payloads[0]["device_id"] == 2


@pytest.mark.asyncio
async def test_reconciling_resource_reservation_stops_following_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    material_unit = SimpleNamespace(
        id=1001,
        pkg_code="PKG-001",
        status=MaterialUnitStatus.IN_TRANSIT,
        reconciliation_from_state=None,
        current_location="SORTING:SCAN",
        current_session_id=123,
    )
    session = _session(context_json={"pkg_id": "PKG-001"}, current_material_unit_id=1001)
    db = MaterialUnitDb(material_unit=material_unit)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    emit_timeline = AsyncMock()
    record_ng_flow = AsyncMock()
    reservation_service = RecordingBinCellReservationService(status="RECONCILING")

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", record_ng_flow)

    await RuntimeIntentEffectApplier(bin_cell_reservation_service=reservation_service).apply(
        ctx,
        [
            RuntimeIntent.resource_reservation(
                operation="CONSUME_BIN_CELL",
                payload={"bin_code": "BIN-001", "bin_cell_index": "4"},
                idempotency_key="CONSUME_BIN_CELL:123:BIN-001:4",
            ),
            RuntimeIntent.complete({"material_mounted": True}),
        ],
    )

    assert reservation_service.calls[0]["operation"] == "CONSUME_BIN_CELL"
    assert session.context_json == {"pkg_id": "PKG-001"}
    assert session.status == "WAITING_DEVICE_RESULT"
    assert material_unit.status == MaterialUnitStatus.RECONCILING
    assert material_unit.reconciliation_from_state == MaterialUnitStatus.IN_TRANSIT
    record_ng_flow.assert_not_awaited()


@pytest.mark.asyncio
async def test_consume_bin_cell_owner_mismatch_creates_hold_and_runtime_does_not_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.workline.models.bin_cell_reservation import BinCellReservationStatus, WorklineBinCellReservation
    from src.app.workline.models.safety import WorkLineRuntimeStatus
    from src.app.workline.services.bin_cell_reservation_service import WorklineBinCellReservationService
    from src.app.workline.services.runtime_hold_creation_service import RuntimeHoldCreationService

    class ReservationRepo:
        def __init__(self) -> None:
            self.consumed: list[WorklineBinCellReservation] = []

        async def get_active_by_bin_cell(
            self,
            _db: object,
            *,
            bin_code: str,
            bin_cell_index: str,
        ) -> WorklineBinCellReservation:
            assert bin_code == "BIN-001"
            assert bin_cell_index == "4"
            return WorklineBinCellReservation(
                reservation_key="reserve:old",
                workline_id=1001,
                workline_code="SMT_SORTER_01",
                session_id=2001,
                trace_id="trace-old",
                pkg_code="PKG-OLD",
                bin_code="BIN-001",
                bin_cell_code="BIN-001-4",
                bin_cell_index="4",
                reservation_status=BinCellReservationStatus.PLANNED,
                reserved_at=datetime(2026, 1, 1, 0, 0, 0),
            )

        async def mark_consumed(
            self,
            _db: object,
            reservation: WorklineBinCellReservation,
            *,
            consumed_at: datetime,
        ) -> WorklineBinCellReservation:
            reservation.consumed_at = consumed_at
            self.consumed.append(reservation)
            return reservation

    class MaterialMountRepo:
        async def get_active_by_bin_cell(self, _db: object, *, bin_code: str, bin_cell_index: str) -> object | None:
            return None

    class RuntimeHoldRepo:
        def __init__(self) -> None:
            self.created: list[dict[str, Any]] = []

        async def create_open_hold(self, _db: object, **data: Any) -> SimpleNamespace:
            self.created.append(data)
            return SimpleNamespace(id=9901, **data)

    class WorkLineRepo:
        def __init__(self, workline: SimpleNamespace) -> None:
            self.workline = workline

        async def get_for_update(self, _db: object, workline_id: int) -> SimpleNamespace:
            assert workline_id == 1001
            return self.workline

    session = _session(id=2002, context_json={"pkg_id": "PKG-001"})
    workline = SimpleNamespace(
        id=1001,
        line_code="SMT_SORTER_01",
        plugin_key="demo_plugin",
        contract_version="1.0",
        runtime_status=WorkLineRuntimeStatus.READY,
        stopped_at=None,
        stopped_reason=None,
    )
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    ctx["workline"] = workline
    emit_timeline = AsyncMock()
    record_ng_flow = AsyncMock()
    reservation_repo = ReservationRepo()
    hold_repo = RuntimeHoldRepo()
    runtime_hold_creator = RuntimeHoldCreationService(
        repository=hold_repo,
        workline_repository=WorkLineRepo(workline),
    )
    reservation_service = WorklineBinCellReservationService(
        reservation_repository=reservation_repo,
        material_mount_repository=MaterialMountRepo(),
        runtime_hold_creator=runtime_hold_creator,
    )

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", record_ng_flow)

    await RuntimeIntentEffectApplier(bin_cell_reservation_service=reservation_service).apply(
        ctx,
        [
            RuntimeIntent.resource_reservation(
                operation="CONSUME_BIN_CELL",
                payload={"bin_code": "BIN-001", "bin_cell_index": "4"},
                idempotency_key="CONSUME_BIN_CELL:2002:CMD-OUTPUT-001:BIN-001:4",
            ),
            RuntimeIntent.complete({"material_mounted": True}),
        ],
    )

    assert hold_repo.created[0]["source_reason"] == "BIN_CELL_RESERVATION_OWNER_MISMATCH"
    assert workline.runtime_status == WorkLineRuntimeStatus.RECONCILING
    assert workline.stopped_reason == "BIN_CELL_RESERVATION_OWNER_MISMATCH"
    assert reservation_repo.consumed == []
    assert session.context_json == {"pkg_id": "PKG-001"}
    assert session.status == "WAITING_DEVICE_RESULT"
    assert getattr(ctx["orch_result"], "complete", False) is False
    record_ng_flow.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_ng_writes_business_decision_timeline(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        captured.append(kwargs)

    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=_session(status="RUNNING"))

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [RuntimeIntent.mark_ng(reason_code="SCAN_NG", message="扫码判定 NG", payload={"barcode": "BAD-001"})],
    )

    assert captured[0]["message"] == "扫码判定 NG"
    assert captured[0]["payload"]["reason_code"] == "SCAN_NG"
    assert captured[0]["payload"]["evidence"] == {"barcode": "BAD-001"}


@pytest.mark.asyncio
async def test_block_intent_holds_session_without_command_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    session = _session()
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        captured.append(kwargs)

    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.block(
                scope=BlockScope.MATERIAL,
                reason_code="MATERIAL_BLOCKED",
                message="物料需要人工处理",
                suggested_action="检查标签",
                payload={"evidence_key": "EVD-1234"},
            )
        ],
    )

    assert session.status == "MANUAL_HOLD"
    assert session.current_wait_type is None
    assert session.awaiting_device_command_code is None
    assert session.failure_domain == "MATERIAL"
    assert session.failure_code == "MATERIAL_BLOCKED"
    assert db.add.call_count == 0
    db.execute.assert_awaited_once()
    assert captured[0]["payload"]["suggested_action"] == "检查标签"
    assert captured[0]["payload"]["evidence"] == {"evidence_key": "EVD-1234"}


@pytest.mark.asyncio
async def test_apply_resource_wait_sets_waiting_external_and_returns_resource_retry_disposition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(
        status="RUNNING",
        current_wait_type=None,
        awaiting_device_command_code=None,
        context_json={},
    )
    db = SimpleNamespace(execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["workline"].plugin_key = "SMT_SORTING_INBOUND"
    emit_timeline = AsyncMock()
    persist_external_wait = AsyncMock()
    record_resource_wait = AsyncMock()

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(
        "src.app.workline.repositories.session_repository.WorklineSessionRepository.persist_external_wait",
        persist_external_wait,
    )
    monkeypatch.setattr(
        "src.app.workline.services.diagnostic_service.workline_diagnostic_service.record_resource_wait",
        record_resource_wait,
    )

    result = await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.resource_wait(
                subject_type="TARGET_STATION",
                subject_key="station:TARGET_STATION",
                projection_type="ACTIVE_TARGET_BIN_RACK",
                reason_code="STATION_BUSY",
                message="目标 Station 忙",
                payload={"active_session_id": 456},
            )
        ],
    )

    assert result.disposition == WriteBackDisposition.RESOURCE_RETRY
    assert session.status == "WAITING_EXTERNAL"
    assert session.current_wait_type == "RESOURCE_WAIT"
    assert session.context_json["resource_wait"]["inbox_id"] == 10
    assert session.context_json["resource_wait"]["subject_type"] == "TARGET_STATION"
    assert session.context_json["resource_wait"]["subject_key"] == "station:TARGET_STATION"
    assert session.context_json["resource_wait"]["projection_type"] == "ACTIVE_TARGET_BIN_RACK"
    assert session.context_json["resource_wait"]["wait_count"] == 1
    persist_external_wait.assert_awaited_once()
    record_resource_wait.assert_awaited_once()
    emit_timeline.assert_awaited_once()


@pytest.mark.asyncio
async def test_resource_wait_must_be_final_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    emit_timeline = AsyncMock()
    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)

    with pytest.raises(ValueError, match="terminal RuntimeIntent must be final intent"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.resource_wait(
                    subject_type="TARGET_STATION",
                    subject_key="station:TARGET_STATION",
                    projection_type="ACTIVE_TARGET_BIN_RACK",
                    reason_code="STATION_BUSY",
                    message="目标 Station 忙",
                ),
                RuntimeIntent.update_context({"late": True}),
            ],
        )

    emit_timeline.assert_not_awaited()


@pytest.mark.asyncio
async def test_resource_wait_cannot_follow_command_producing_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    emit_timeline = AsyncMock()
    create_command = AsyncMock()
    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr("src.app.device.repositories.command_repository.DeviceCommandRepository.create", create_command)

    with pytest.raises(ValueError, match="terminal RuntimeIntent cannot follow command-producing RuntimeIntent"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.command(action="MOVE_FORWARD", payload={}, destination=Destination.current()),
                RuntimeIntent.resource_wait(
                    subject_type="TARGET_STATION",
                    subject_key="station:TARGET_STATION",
                    projection_type="ACTIVE_TARGET_BIN_RACK",
                    reason_code="STATION_BUSY",
                    message="目标 Station 忙",
                ),
            ],
        )

    create_command.assert_not_awaited()
    emit_timeline.assert_not_awaited()
