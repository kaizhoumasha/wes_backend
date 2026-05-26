from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.workline.services.ng_return_item_service import ng_return_item_service
from src.celery_app.tasks import workline as workline_effects
from src.workline_runtime.orchestrator import OrchestratorResult
from src.workline_runtime.runtime_intent import BlockScope, Destination, RuntimeIntent, RuntimeIntentKind
from src.workline_runtime.runtime_intent_effects import RuntimeIntentEffectApplier
from src.workline_runtime.trace_context import TraceContext


def _session(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": 123,
        "workline_id": 1,
        "status": "WAITING_DEVICE_RESULT",
        "context_json": {},
        "trace_id": None,
        "last_inbox_id": None,
        "plugin_key": None,
        "contract_version": None,
        "current_wait_type": "COMMAND_RESULT",
        "waiting_since": datetime(2026, 1, 1, 0, 0, 0),
        "deadline_at": datetime(2026, 1, 1, 0, 1, 0),
        "current_wait_timeout_seconds": 60,
        "awaiting_command_id": 99,
        "ended_at": None,
        "failure_domain": None,
        "failure_code": None,
        "failure_message": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _ctx(orch_result: OrchestratorResult, *, session: Any | None = None, db: Any | None = None) -> dict[str, Any]:
    resolved_session = session or _session()
    return {
        "db": db or SimpleNamespace(add=MagicMock()),
        "session": resolved_session,
        "workline": SimpleNamespace(id=1, plugin_key="demo_plugin", contract_version="1.0"),
        "inbox": SimpleNamespace(id=10, trace_id="trace-runtime", payload_json={"canonical_event_type": "SCAN"}),
        "devices_by_role": {},
        "source_device": None,
        "orch_result": orch_result,
        "current_status": "WAITING_DEVICE_RESULT",
        "trace_id": "trace-runtime",
        "trace": TraceContext.from_runtime(session=resolved_session, trace_id="trace-runtime"),
        "session_ctx": dict(getattr(resolved_session, "context_json", {}) or {}),
        "now": datetime(2026, 1, 1, 0, 2, 0),
        "awaiting_command_id": None,
        "awaiting_command_code": None,
        "next_timeline_seq_no": None,
    }


class RecordingResourceProjectionService:
    def __init__(self, *, status: str = "PROJECTED") -> None:
        self.calls: list[dict[str, Any]] = []
        self.status = status

    async def record_resource_fact(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(status=self.status)


class RecordingBinCellReservationService:
    def __init__(self, *, status: str = "CLAIMED") -> None:
        self.calls: list[dict[str, Any]] = []
        self.status = status

    async def apply_runtime_reservation(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(status=self.status)


class RecordingHandlingOperationService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def request_bin_operation(
        self,
        db: Any,
        *,
        operation_type: str,
        operation_key: str,
        moves: list[dict[str, Any]],
        trace_id: str,
        workline_id: int | None = None,
        workline_code: str | None = None,
        material_session_id: int | None = None,
        carrier_type: str = "CTU",
        carrier_code: str | None = None,
        timeout_seconds: int | None = None,
    ) -> SimpleNamespace:
        self.calls.append(
            {
                "db": db,
                "operation_type": operation_type,
                "operation_key": operation_key,
                "moves": moves,
                "trace_id": trace_id,
                "workline_id": workline_id,
                "workline_code": workline_code,
                "material_session_id": material_session_id,
                "carrier_type": carrier_type,
                "carrier_code": carrier_code,
                "timeout_seconds": timeout_seconds,
            }
        )
        return SimpleNamespace(
            id=701,
            operation_key=operation_key,
            operation_type=operation_type,
            operation_status="REQUESTED",
        )


@pytest.mark.asyncio
async def test_empty_intents_complete_new_event_session_as_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    session = _session(
        status="NEW",
        current_wait_type=None,
        awaiting_command_id=None,
        waiting_since=None,
        deadline_at=None,
        current_wait_timeout_seconds=None,
    )
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    ctx["current_status"] = "NEW"
    ctx["inbox"].payload_json = {
        "message_type": "DEVICE_EVENT",
        "device_code": "PIPELINE01",
        "canonical_event_type": "MATERIAL_ARRIVED",
    }

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        captured.append(kwargs)

    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier().apply(ctx, [])

    assert session.status == "COMPLETED"
    assert session.ended_at == ctx["now"]
    assert session.current_wait_type is None
    assert session.awaiting_command_id is None
    assert captured[0]["from_status"] == "NEW"
    assert captured[0]["to_status"] == "COMPLETED"
    assert captured[0]["payload"]["completion_reason"] == "NO_RUNTIME_INTENT"


@pytest.mark.asyncio
async def test_update_context_and_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(context_json={"pkg_id": "PKG-001"})
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    emit_timeline = AsyncMock()
    record_ng_flow = AsyncMock()

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", record_ng_flow)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.update_context({"scan_ok": True}),
            RuntimeIntent.complete({"bin_code": "BIN-001"}),
        ],
    )

    assert session.context_json == {"pkg_id": "PKG-001", "scan_ok": True, "bin_code": "BIN-001"}
    assert session.status == "COMPLETED"
    assert session.current_wait_type is None
    assert session.awaiting_command_id is None
    assert session.ended_at == ctx["now"]
    record_ng_flow.assert_awaited_once()
    emit_timeline.assert_awaited_once()


@pytest.mark.asyncio
async def test_resource_fact_intent_is_applied_before_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(context_json={"pkg_id": "PKG-001"})
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    emit_timeline = AsyncMock()
    record_ng_flow = AsyncMock()
    resource_projection = RecordingResourceProjectionService()

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", record_ng_flow)

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="MATERIAL_MOUNTED",
                payload={"pkg_code": "PKG-001", "bin_code": "BIN-001", "bin_cell_index": "4"},
                idempotency_key="MATERIAL_MOUNTED:CMD-001:PKG-001:BIN-001:4",
            ),
            RuntimeIntent.complete({"material_mounted": True}),
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "MATERIAL_MOUNTED"
    assert resource_projection.calls[0]["db"] is ctx["db"]
    assert resource_projection.calls[0]["session"] is session
    assert resource_projection.calls[0]["idempotency_key"] == "MATERIAL_MOUNTED:CMD-001:PKG-001:BIN-001:4"
    assert session.status == "COMPLETED"
    assert session.context_json["material_mounted"] is True
    record_ng_flow.assert_awaited_once()


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
    assert session.status == "WAITING_DEVICE_RESULT"
    record_ng_flow.assert_not_awaited()


@pytest.mark.asyncio
async def test_resource_reservation_intent_is_applied_before_command(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SimpleNamespace(id=1, device_code="CONV01", device_role="CONVEYOR")
    target = SimpleNamespace(id=2, device_code="OUT01", device_role="OUTPUT_ARM", upstream_device_id=1)
    created_payloads: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
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
    session = _session(context_json={"pkg_id": "PKG-001"})
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
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
    db = SimpleNamespace(add=MagicMock())
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
            )
        ],
    )

    assert session.status == "MANUAL_HOLD"
    assert session.current_wait_type is None
    assert session.awaiting_command_id is None
    assert session.failure_domain == "MATERIAL"
    assert session.failure_code == "MATERIAL_BLOCKED"
    assert db.add.call_count == 0
    assert captured[0]["payload"]["suggested_action"] == "检查标签"


@pytest.mark.asyncio
async def test_command_intent_creates_command_outbox_and_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SimpleNamespace(id=1, device_code="ARM01", device_role="INPUT_ARM")
    target = SimpleNamespace(id=2, device_code="CONV01", device_role="CONVEYOR", upstream_device_id=1)
    created_command = SimpleNamespace(
        id=88,
        command_code="CMD-TEST-001",
        task_type="MOVE_FORWARD",
        priority=5,
        timeout_ms=30000,
        params={"pkg_id": "PKG-001"},
    )
    created_payloads: list[dict[str, Any]] = []
    timelines: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["source_device"] = source
    ctx["devices_by_role"] = {"INPUT_ARM": [source], "CONVEYOR": [target]}

    async def fake_create(_repo, _db, payload):
        created_payloads.append(payload)
        return created_command

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        timelines.append(kwargs)

    monkeypatch.setattr(workline_effects, "_enforce_device_command_governance", lambda *_, **__: None)
    monkeypatch.setattr(
        "src.app.device.repositories.command_repository.DeviceCommandRepository.create",
        fake_create,
    )
    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.command(
                action="MOVE_FORWARD",
                payload={"pkg_id": "PKG-001"},
                destination=Destination.role("CONVEYOR"),
                timeout_seconds=300,
            )
        ],
    )

    assert created_payloads[0]["device_id"] == 2
    assert created_payloads[0]["task_type"] == "MOVE_FORWARD"
    assert session.status == "WAITING_DEVICE_RESULT"
    assert session.awaiting_command_id == 88
    assert db.add.call_count == 1
    assert [timeline["related_command_id"] for timeline in timelines] == [88, 88]


@pytest.mark.asyncio
async def test_external_request_intent_creates_external_outbox_and_immediate_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timelines: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        timelines.append(kwargs)

    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.external_request(
                dispatch_key="external:smt:release-001:FULL_BIN_EXCHANGE",
                target_code="WMS_RCS_FULL_BOX_EXCHANGE",
                payload={"rack_release_id": "release-001"},
                timeout_seconds=1800,
                source_system="WMS_RCS",
            )
        ],
    )

    outbox = db.add.call_args.args[0]
    assert outbox.dispatch_type == "EXTERNAL_HTTP"
    assert outbox.target_type == "HTTP_ENDPOINT"
    assert outbox.dispatch_key == "external:smt:release-001:FULL_BIN_EXCHANGE"
    assert outbox.target_code == "WMS_RCS_FULL_BOX_EXCHANGE"
    assert outbox.payload_json == {"rack_release_id": "release-001"}
    assert session.status == "WAITING_EXTERNAL"
    assert session.current_wait_type == "EXTERNAL_HTTP"
    assert session.awaiting_command_id is None
    assert session.current_wait_timeout_seconds == 1800
    assert session.deadline_at == ctx["now"] + timedelta(seconds=1800)
    assert [timeline["action_type"].value for timeline in timelines] == ["EXTERNAL_CALL_STARTED", "WAIT_STARTED"]
    assert timelines[1]["payload"]["wait_token"] == "external:smt:release-001:FULL_BIN_EXCHANGE"


@pytest.mark.asyncio
async def test_rack_operation_request_creates_operation_tasks_and_waits_by_operation_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timelines: list[dict[str, Any]] = []
    operation_calls: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["workline"].line_code = "WL-SMT-01"

    class RecordingRackOperationService:
        async def request_operation_tasks(self, db: Any, **kwargs: Any) -> list[SimpleNamespace]:
            operation_calls.append({"db": db, **kwargs})
            return [
                SimpleNamespace(
                    id=901,
                    operation_key=kwargs["operation_key"],
                    sequence_no=1,
                    task_type="MOVE_RACK",
                    dispatch_key="rack-operation:rack-operation:trace-runtime:1:MOVE_RACK",
                    actions_json={"required": True},
                    rack_code="RACK-OLD",
                    source_position_code="SINGLE_LAYER_A",
                    target_position_code=None,
                ),
                SimpleNamespace(
                    id=902,
                    operation_key=kwargs["operation_key"],
                    sequence_no=2,
                    task_type="ALLOCATE_AND_MOVE_RACK",
                    dispatch_key="rack-operation:rack-operation:trace-runtime:2:ALLOCATE_AND_MOVE_RACK",
                    actions_json={"required": True},
                    rack_code=None,
                ),
            ]

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        timelines.append(kwargs)

    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier(rack_operation_service=RecordingRackOperationService()).apply(
        ctx,
        [
            RuntimeIntent.rack_operation_request(
                operation_type="REPLACE_CLASSIFIER_WORK_RACK",
                operation_key="rack-operation:trace-runtime",
                target_code="WMS_RCS_RACK_OPERATION",
                payload={
                    "work_position_code": "SINGLE_LAYER_A",
                    "new_rack_kind": "SINGLE_LAYER",
                    "move_out_target_position_role": "SMT_EMPTY_RACK_AREA",
                    "rack_tasks": [
                        {
                            "sequence_no": 1,
                            "task_type": "MOVE_RACK",
                            "rack_code": "RACK-OLD",
                            "rack_kind": "SINGLE_LAYER",
                            "source_position_code": "SINGLE_LAYER_A",
                            "target_position_role": "SMT_EMPTY_RACK_AREA",
                        },
                        {
                            "sequence_no": 2,
                            "task_type": "ALLOCATE_AND_MOVE_RACK",
                            "rack_kind": "SINGLE_LAYER",
                            "target_position_code": "SINGLE_LAYER_A",
                            "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                        },
                    ],
                    "trace_id": "trace-from-payload",
                },
                timeout_seconds=1800,
            )
        ],
    )

    assert db.add.call_count == 0
    assert len(operation_calls) == 1
    assert operation_calls[0]["db"] is db
    assert operation_calls[0]["session"] is session
    assert operation_calls[0]["workline"] is ctx["workline"]
    assert operation_calls[0]["operation_key"] == "rack-operation:trace-runtime"
    assert operation_calls[0]["operation_type"] == "REPLACE_CLASSIFIER_WORK_RACK"
    assert operation_calls[0]["target_code"] == "WMS_RCS_RACK_OPERATION"
    assert operation_calls[0]["task_specs"][0]["task_type"] == "MOVE_RACK"
    assert operation_calls[0]["task_specs"][1]["target_position_code"] == "SINGLE_LAYER_A"
    assert operation_calls[0]["trace_id"] == "trace-from-payload"
    assert session.status == "WAITING_EXTERNAL"
    assert session.current_wait_type == "RACK_OPERATION"
    assert session.awaiting_command_id is None
    assert session.current_wait_timeout_seconds == 1800
    assert session.deadline_at == ctx["now"] + timedelta(seconds=1800)
    assert session.context_json["waiting_rack_operation_key"] == "rack-operation:trace-runtime"
    assert session.context_json["rack_operation"]["operation_key"] == "rack-operation:trace-runtime"
    assert session.context_json["rack_operation"]["status"] == "PENDING"
    assert session.context_json["rack_operation"]["task_sequences"] == [1, 2]
    assert session.context_json["rack_operation"]["released_rack_codes"] == ["RACK-OLD"]
    assert [timeline["action_type"].value for timeline in timelines] == ["WAIT_STARTED"]
    assert timelines[0]["payload"]["wait_token"] == "rack-operation:trace-runtime"


@pytest.mark.asyncio
async def test_rack_operation_request_stores_operation_wait_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_calls: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock())
    session = _session(
        status="RUNNING",
        current_wait_type=None,
        awaiting_command_id=None,
        context_json={"rack_operation": {"status": "OLD"}},
    )
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)

    class RecordingRackOperationService:
        async def request_operation_tasks(self, db: Any, **kwargs: Any) -> list[SimpleNamespace]:
            operation_calls.append({"db": db, **kwargs})
            return [
                SimpleNamespace(
                    id=901,
                    operation_key=kwargs["operation_key"],
                    sequence_no=2,
                    task_type="ALLOCATE_AND_MOVE_RACK",
                    dispatch_key="rack-operation:rack-operation:trace-runtime:2:ALLOCATE_AND_MOVE_RACK",
                    actions_json={"required": True},
                    rack_code=None,
                )
            ]

    async def capture_timeline(_ctx: dict[str, Any], **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier(rack_operation_service=RecordingRackOperationService()).apply(
        ctx,
        [
            RuntimeIntent.rack_operation_request(
                operation_type="REPLACE_CLASSIFIER_WORK_RACK",
                operation_key="rack-operation:trace-runtime",
                target_code="WMS_RCS_RACK_OPERATION",
                payload={
                    "work_position_code": "SINGLE_LAYER_A",
                    "new_rack_kind": "SINGLE_LAYER",
                    "move_out_target_position_role": "SMT_EMPTY_RACK_AREA",
                    "rack_tasks": [
                        {
                            "sequence_no": 2,
                            "task_type": "ALLOCATE_AND_MOVE_RACK",
                            "rack_kind": "SINGLE_LAYER",
                            "target_position_code": "SINGLE_LAYER_A",
                            "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                        }
                    ],
                },
                timeout_seconds=1800,
            )
        ],
    )

    assert operation_calls[0]["trace_id"] == "trace-runtime"
    assert session.context_json["waiting_rack_operation_key"] == "rack-operation:trace-runtime"
    assert session.context_json["rack_operation"]["status"] == "PENDING"


@pytest.mark.asyncio
async def test_rack_operation_request_preserves_operation_metadata_written_by_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = SimpleNamespace(add=MagicMock())
    operation_key = "rack-operation:trace-runtime"
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)

    class RecordingRackOperationService:
        async def request_operation_tasks(self, _db: Any, **kwargs: Any) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(
                    id=901,
                    operation_key=kwargs["operation_key"],
                    sequence_no=1,
                    task_type="MOVE_RACK",
                    dispatch_key=f"rack-operation:{kwargs['operation_key']}:1:MOVE_RACK",
                    actions_json={"required": True},
                    rack_code="RACK-OLD",
                    source_position_code="SINGLE_LAYER_A",
                    target_position_code=None,
                ),
                SimpleNamespace(
                    id=902,
                    operation_key=kwargs["operation_key"],
                    sequence_no=2,
                    task_type="ALLOCATE_AND_MOVE_RACK",
                    dispatch_key=f"rack-operation:{kwargs['operation_key']}:2:ALLOCATE_AND_MOVE_RACK",
                    actions_json={"required": True},
                    rack_code=None,
                ),
            ]

    async def capture_timeline(_ctx: dict[str, Any], **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier(rack_operation_service=RecordingRackOperationService()).apply(
        ctx,
        [
            RuntimeIntent.update_context(
                {
                    "rack_operation": {
                        "operation_key": operation_key,
                        "status": "REQUESTED",
                        "pkg_id": "PKG-001",
                    },
                    "waiting_rack_operation_key": operation_key,
                }
            ),
            RuntimeIntent.rack_operation_request(
                operation_type="REPLACE_CLASSIFIER_WORK_RACK",
                operation_key=operation_key,
                target_code="WMS_RCS_RACK_OPERATION",
                payload={
                    "work_position_code": "SINGLE_LAYER_A",
                    "new_rack_kind": "SINGLE_LAYER",
                    "move_out_target_position_role": "SMT_EMPTY_RACK_AREA",
                    "rack_tasks": [
                        {
                            "sequence_no": 1,
                            "task_type": "MOVE_RACK",
                            "rack_code": "RACK-OLD",
                            "rack_kind": "SINGLE_LAYER",
                            "source_position_code": "SINGLE_LAYER_A",
                            "target_position_role": "SMT_EMPTY_RACK_AREA",
                        },
                        {
                            "sequence_no": 2,
                            "task_type": "ALLOCATE_AND_MOVE_RACK",
                            "rack_kind": "SINGLE_LAYER",
                            "target_position_code": "SINGLE_LAYER_A",
                            "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                        },
                    ],
                },
                timeout_seconds=1800,
            ),
        ],
    )

    rack_operation = session.context_json["rack_operation"]
    assert rack_operation["status"] == "PENDING"
    assert rack_operation["pkg_id"] == "PKG-001"
    assert rack_operation["task_sequences"] == [1, 2]
    assert rack_operation["released_rack_codes"] == ["RACK-OLD"]


@pytest.mark.asyncio
async def test_bin_operation_request_calls_handling_service_and_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timelines: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    service = RecordingHandlingOperationService()

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        timelines.append(kwargs)

    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier(handling_operation_service=service).apply(
        ctx,
        [
            RuntimeIntent.bin_operation_request(
                operation_type="SORTER_FEED_BIN",
                operation_key="bin-operation:trace-runtime",
                moves=[
                    {
                        "sequence_no": 1,
                        "bin_code": "BIN-001",
                        "source_type": "RACK_SLOT",
                        "source_code": "SINGLE_LAYER_A:01",
                        "target_type": "SORTER_STATION",
                        "target_code": "SORTER-01",
                    }
                ],
                carrier_type="CTU",
                carrier_code="CTU-01",
                timeout_seconds=1800,
            )
        ],
    )

    assert len(service.calls) == 1
    assert service.calls[0]["db"] is db
    assert service.calls[0]["workline_id"] == 1
    assert service.calls[0]["workline_code"] is None
    assert service.calls[0]["material_session_id"] == session.id
    assert service.calls[0]["operation_key"] == "bin-operation:trace-runtime"
    assert service.calls[0]["operation_type"] == "SORTER_FEED_BIN"
    assert service.calls[0]["moves"][0]["bin_code"] == "BIN-001"
    assert service.calls[0]["carrier_type"] == "CTU"
    assert service.calls[0]["carrier_code"] == "CTU-01"
    assert service.calls[0]["trace_id"] == "trace-runtime"
    assert session.status == "WAITING_EXTERNAL"
    assert session.current_wait_type == "HANDLING_OPERATION"
    assert session.awaiting_command_id is None
    assert session.current_wait_timeout_seconds == 1800
    assert session.deadline_at == ctx["now"] + timedelta(seconds=1800)
    assert session.context_json["waiting_handling_operation_key"] == "bin-operation:trace-runtime"
    assert session.context_json["handling_operation"]["operation_key"] == "bin-operation:trace-runtime"
    assert session.context_json["handling_operation"]["operation_type"] == "SORTER_FEED_BIN"
    assert session.context_json["handling_operation"]["status"] == "PENDING"
    assert session.context_json["handling_operation"]["move_sequences"] == [1]
    assert timelines[0]["payload"]["wait_type"] == "HANDLING_OPERATION"
    assert timelines[0]["payload"]["wait_token"] == "bin-operation:trace-runtime"


@pytest.mark.asyncio
async def test_rack_bin_exchange_request_uses_same_handling_wait_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = SimpleNamespace(add=MagicMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    service = RecordingHandlingOperationService()

    monkeypatch.setattr(workline_effects, "_emit_timeline", AsyncMock())

    await RuntimeIntentEffectApplier(handling_operation_service=service).apply(
        ctx,
        [
            RuntimeIntent.rack_bin_exchange_request(
                operation_type="SINGLE_LAYER_FULL_BIN_EXCHANGE",
                operation_key="rack-bin-exchange:release-001",
                moves=[
                    {
                        "sequence_no": 1,
                        "bin_code": "BIN-FULL",
                        "source_type": "RACK_SLOT",
                        "source_code": "SINGLE_LAYER_A:01",
                        "target_type": "BUFFER",
                        "target_code": "FULL_BIN_BUFFER",
                    },
                    {
                        "sequence_no": 2,
                        "placeholder_key": "EMPTY_BIN_FOR:SINGLE_LAYER_A:01",
                        "source_type": "BUFFER",
                        "source_code": "EMPTY_BIN_BUFFER",
                        "target_type": "RACK_SLOT",
                        "target_code": "SINGLE_LAYER_A:01",
                    },
                ],
                rack_code="RACK-SINGLE-01",
                carrier_type="CTU",
                timeout_seconds=1800,
            )
        ],
    )

    assert service.calls[0]["operation_key"] == "rack-bin-exchange:release-001"
    assert service.calls[0]["operation_type"] == "SINGLE_LAYER_FULL_BIN_EXCHANGE"
    assert service.calls[0]["moves"][1]["placeholder_key"] == "EMPTY_BIN_FOR:SINGLE_LAYER_A:01"
    assert session.current_wait_type == "HANDLING_OPERATION"
    assert session.context_json["handling_operation"]["rack_code"] == "RACK-SINGLE-01"
    assert session.context_json["handling_operation"]["move_sequences"] == [1, 2]


def test_rack_operation_wait_released_rack_codes_include_only_move_out_tasks() -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)

    RuntimeIntentEffectApplier()._mark_session_waiting_for_rack_operation(
        ctx,
        operation_key="rack-operation:move-in",
        operation_type="RACK_TRANSPORT",
        tasks=[
            SimpleNamespace(
                sequence_no=1,
                task_type="MOVE_RACK",
                dispatch_key="rack-operation:move-in:1:MOVE_RACK",
                actions_json={"required": True},
                rack_code="RACK-INBOUND-1",
                source_position_code="SOURCE-A",
                target_position_code="WORK-POSITION",
            ),
            SimpleNamespace(
                sequence_no=2,
                task_type="MOVE_RACK",
                dispatch_key="rack-operation:move-in:2:MOVE_RACK",
                actions_json={"required": True},
                rack_code="RACK-INBOUND-2",
                source_position_code="SOURCE-B",
                target_position_code="WORK-POSITION",
            ),
            SimpleNamespace(
                sequence_no=3,
                task_type="MOVE_RACK",
                dispatch_key="rack-operation:move-in:3:MOVE_RACK",
                actions_json={"required": True},
                rack_code="RACK-OLD",
                source_position_code="WORK-POSITION",
                target_position_code=None,
            ),
            SimpleNamespace(
                sequence_no=4,
                task_type="MOVE_RACK",
                dispatch_key="rack-operation:move-in:4:MOVE_RACK",
                actions_json={"required": False},
                rack_code="RACK-OPTIONAL",
                source_position_code="WORK-POSITION",
                target_position_code=None,
            ),
        ],
        timeout_seconds=1800,
    )

    rack_operation = session.context_json["rack_operation"]
    assert rack_operation["released_rack_codes"] == ["RACK-OLD"]


def test_rack_operation_wait_infers_target_position_from_returned_tasks() -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)

    RuntimeIntentEffectApplier()._mark_session_waiting_for_rack_operation(
        ctx,
        operation_key="rack-operation:custom-target",
        operation_type="RACK_TRANSPORT",
        tasks=[
            SimpleNamespace(
                sequence_no=1,
                task_type="MOVE_RACK",
                dispatch_key="rack-operation:custom-target:1:MOVE_RACK",
                actions_json={"required": True},
                rack_code="RACK-INBOUND",
                source_position_code="BUFFER-A",
                target_position_code="CUSTOM-WORK-POSITION",
            ),
            SimpleNamespace(
                sequence_no=2,
                task_type="ALLOCATE_AND_MOVE_RACK",
                dispatch_key="rack-operation:custom-target:2:ALLOCATE_AND_MOVE_RACK",
                actions_json={"required": True},
                rack_code=None,
                source_position_code=None,
                target_position_code="CUSTOM-WORK-POSITION",
            ),
        ],
        timeout_seconds=1800,
    )

    rack_operation = session.context_json["rack_operation"]
    assert rack_operation["target_position_code"] == "CUSTOM-WORK-POSITION"
    assert rack_operation["work_position_code"] == "CUSTOM-WORK-POSITION"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intent_kwargs", "message"),
    [
        (
            {
                "operation_type": "ANY_PLUGIN_OPERATION",
                "payload": {"trace_id": "trace-runtime"},
            },
            "RACK_OPERATION_REQUEST intent requires payload.rack_tasks",
        ),
    ],
)
async def test_rack_operation_request_rejects_invalid_operation_contract(
    intent_kwargs: dict[str, Any],
    message: str,
) -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)

    with pytest.raises(ValueError, match=message):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.rack_operation_request(
                    operation_key="rack-operation:trace-runtime",
                    target_code="WMS_RCS_RACK_OPERATION",
                    timeout_seconds=1800,
                    **intent_kwargs,
                )
            ],
        )

    assert session.context_json == {}
    assert session.status == "RUNNING"
    assert ctx["db"].add.call_count == 0


@pytest.mark.asyncio
async def test_rack_operation_request_requires_trace_id() -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None, trace_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    ctx["trace"] = SimpleNamespace(trace_id=None)
    ctx["trace_id"] = None

    with pytest.raises(ValueError, match="RACK_OPERATION_REQUEST intent requires trace_id"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.rack_operation_request(
                    operation_type="REPLACE_CLASSIFIER_WORK_RACK",
                    operation_key="rack-operation:trace-runtime",
                    target_code="WMS_RCS_RACK_OPERATION",
                    payload={
                        "rack_tasks": [
                            {
                                "sequence_no": 2,
                                "task_type": "ALLOCATE_AND_MOVE_RACK",
                                "rack_kind": "SINGLE_LAYER",
                                "target_position_code": "SINGLE_LAYER_A",
                                "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                            }
                        ],
                    },
                    timeout_seconds=1800,
                )
            ],
        )

    assert session.context_json == {}
    assert session.status == "RUNNING"
    assert ctx["db"].add.call_count == 0


@pytest.mark.asyncio
async def test_device_event_intent_creates_device_event_inbox_without_waiting_current_session() -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)

    class RecordingInboxService:
        def __init__(self) -> None:
            self.created: dict[str, Any] = {}

        async def create_device_event_inbox(self, **kwargs: Any) -> object:
            self.created = kwargs
            return SimpleNamespace(id=456)

    recording_inbox_service = RecordingInboxService()
    intent = RuntimeIntent.device_event(
        device_code="SMT-RACK-RELEASE",
        event_type="SINGLE_LAYER_RACK_RELEASED",
        timestamp=1770000000000,
        data={"rack_release_id": "release-001"},
        event_id="smt-release:release-001",
        causation_id="scan:event-001",
        canonical_event_type="SINGLE_LAYER_RACK_RELEASED",
    )

    await RuntimeIntentEffectApplier(inbox_service=recording_inbox_service).apply(ctx, [intent])

    assert intent.kind == RuntimeIntentKind.DEVICE_EVENT
    assert recording_inbox_service.created["db"] is ctx["db"]
    assert recording_inbox_service.created["device_code"] == "SMT-RACK-RELEASE"
    assert recording_inbox_service.created["event_type"] == "SINGLE_LAYER_RACK_RELEASED"
    assert recording_inbox_service.created["timestamp"] == 1770000000000
    assert recording_inbox_service.created["data"] == {"rack_release_id": "release-001"}
    assert recording_inbox_service.created["trace_id"] == "trace-runtime"
    assert recording_inbox_service.created["event_id"] == "smt-release:release-001"
    assert recording_inbox_service.created["causation_id"] == "scan:event-001"
    assert recording_inbox_service.created["canonical_event_type"] == "SINGLE_LAYER_RACK_RELEASED"
    assert recording_inbox_service.created["auto_commit"] is False
    assert session.status == "RUNNING"
    assert session.current_wait_type is None
    assert session.awaiting_command_id is None


@pytest.mark.asyncio
async def test_command_destination_current_targets_source_device(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SimpleNamespace(id=1, device_code="ARM01", device_role="INPUT_ARM")
    created_payloads: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["source_device"] = source
    ctx["devices_by_role"] = {"INPUT_ARM": [source]}

    async def fake_create(_repo, _db, payload):
        created_payloads.append(payload)
        return SimpleNamespace(
            id=88,
            command_code="CMD-CURRENT",
            task_type="SCAN",
            priority=5,
            timeout_ms=30000,
            params={},
        )

    monkeypatch.setattr(workline_effects, "_enforce_device_command_governance", lambda *_, **__: None)
    monkeypatch.setattr("src.app.device.repositories.command_repository.DeviceCommandRepository.create", fake_create)
    monkeypatch.setattr(workline_effects, "_emit_timeline", AsyncMock())

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [RuntimeIntent.command(action="SCAN", payload={}, destination=Destination.current(), timeout_seconds=30)],
    )

    assert created_payloads[0]["device_id"] == 1


@pytest.mark.asyncio
async def test_command_destination_next_targets_topology_downstream(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SimpleNamespace(id=1, device_code="ARM01", device_role="INPUT_ARM")
    target = SimpleNamespace(id=2, device_code="CONV01", device_role="CONVEYOR", upstream_device_id=1)
    created_payloads: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["source_device"] = source
    ctx["devices_by_role"] = {"INPUT_ARM": [source], "CONVEYOR": [target]}

    async def fake_create(_repo, _db, payload):
        created_payloads.append(payload)
        return SimpleNamespace(
            id=88,
            command_code="CMD-NEXT",
            task_type="MOVE_FORWARD",
            priority=5,
            timeout_ms=30000,
            params={},
        )

    monkeypatch.setattr(workline_effects, "_enforce_device_command_governance", lambda *_, **__: None)
    monkeypatch.setattr("src.app.device.repositories.command_repository.DeviceCommandRepository.create", fake_create)
    monkeypatch.setattr(workline_effects, "_emit_timeline", AsyncMock())

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [RuntimeIntent.command(action="MOVE_FORWARD", payload={}, destination=Destination.next(), timeout_seconds=30)],
    )

    assert created_payloads[0]["device_id"] == 2


@pytest.mark.asyncio
async def test_command_destination_device_outside_topology_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SimpleNamespace(id=1, device_code="ARM01", device_role="INPUT_ARM")
    timelines: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["source_device"] = source
    ctx["devices_by_role"] = {"INPUT_ARM": [source]}

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        timelines.append(kwargs)

    create_command = AsyncMock()
    monkeypatch.setattr("src.app.device.repositories.command_repository.DeviceCommandRepository.create", create_command)
    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.command(
                action="MOVE_FORWARD", payload={}, destination=Destination.device(99), timeout_seconds=30
            )
        ],
    )

    create_command.assert_not_awaited()
    assert session.status == "MANUAL_HOLD"
    assert session.failure_code == "DESTINATION_UNREACHABLE"
    assert "No destination matched" in timelines[0]["payload"]["message"]


@pytest.mark.asyncio
async def test_command_destination_ng_route_uses_configured_route_role(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SimpleNamespace(id=1, device_code="ARM01", device_role="INPUT_ARM")
    ng_target = SimpleNamespace(id=3, device_code="NG01", device_role="NG_BUFFER", upstream_device_id=1)
    created_payloads: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["workline"].config = {"route_roles": {"NG_ROUTE": "NG_BUFFER"}}
    ctx["source_device"] = source
    ctx["devices_by_role"] = {"INPUT_ARM": [source], "NG_BUFFER": [ng_target]}

    async def fake_create(_repo, _db, payload):
        created_payloads.append(payload)
        return SimpleNamespace(
            id=88,
            command_code="CMD-NG",
            task_type="PICK_AND_PUT",
            priority=5,
            timeout_ms=30000,
            params={},
        )

    monkeypatch.setattr(workline_effects, "_enforce_device_command_governance", lambda *_, **__: None)
    monkeypatch.setattr("src.app.device.repositories.command_repository.DeviceCommandRepository.create", fake_create)
    monkeypatch.setattr(workline_effects, "_emit_timeline", AsyncMock())

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.command(
                action="PICK_AND_PUT", payload={}, destination=Destination.ng_route(), timeout_seconds=30
            )
        ],
    )

    assert created_payloads[0]["device_id"] == 3


@pytest.mark.asyncio
async def test_invalid_combinations_are_rejected_before_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    emit_timeline = AsyncMock()

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)

    with pytest.raises(ValueError, match="terminal RuntimeIntent must be final intent"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.complete({"done": True}),
                RuntimeIntent.update_context({"late": True}),
            ],
        )

    assert session.status == "RUNNING"
    assert session.context_json == {}
    emit_timeline.assert_not_awaited()


@pytest.mark.asyncio
async def test_command_before_terminal_intent_is_rejected_before_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
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
                RuntimeIntent.complete({"done": True}),
            ],
        )

    assert session.status == "RUNNING"
    assert session.ended_at is None
    assert ctx["db"].add.call_count == 0
    create_command.assert_not_awaited()
    emit_timeline.assert_not_awaited()


@pytest.mark.asyncio
async def test_external_request_before_terminal_intent_is_rejected_before_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    emit_timeline = AsyncMock()

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)

    with pytest.raises(ValueError, match="terminal RuntimeIntent cannot follow command-producing RuntimeIntent"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.external_request(
                    dispatch_key="external:smt:release-001:FULL_BIN_EXCHANGE",
                    target_code="WMS_RCS_FULL_BOX_EXCHANGE",
                    payload={"rack_release_id": "release-001"},
                    timeout_seconds=1800,
                ),
                RuntimeIntent.complete({"done": True}),
            ],
        )

    assert session.status == "RUNNING"
    assert ctx["db"].add.call_count == 0
    emit_timeline.assert_not_awaited()


def test_result_requires_outbox_dispatch_for_external_request() -> None:
    result = OrchestratorResult(
        success=True,
        intents=[
            RuntimeIntent.external_request(
                dispatch_key="external:smt:release-001:FULL_BIN_EXCHANGE",
                target_code="WMS_RCS_FULL_BOX_EXCHANGE",
                payload={"rack_release_id": "release-001"},
                timeout_seconds=1800,
            )
        ],
    )

    assert workline_effects._result_requires_outbox_dispatch(result) is True


def test_result_requires_outbox_dispatch_for_rack_operation_request() -> None:
    result = OrchestratorResult(
        success=True,
        intents=[
            RuntimeIntent.rack_operation_request(
                operation_type="REPLACE_CLASSIFIER_WORK_RACK",
                operation_key="rack-operation:trace-runtime",
                target_code="WMS_RCS_RACK_OPERATION",
                payload={
                    "work_position_code": "SINGLE_LAYER_A",
                    "new_rack_kind": "SINGLE_LAYER",
                    "move_out_target_position_role": "SMT_EMPTY_RACK_AREA",
                },
                timeout_seconds=1800,
            )
        ],
    )

    assert workline_effects._result_requires_outbox_dispatch(result) is True


def test_wait_session_status_maps_rack_operation_to_external_wait() -> None:
    assert workline_effects._wait_session_status("RACK_OPERATION") == "WAITING_EXTERNAL"


@pytest.mark.asyncio
async def test_apply_orchestrator_effects_dispatches_runtime_intents(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, Any] = {}

    class CapturingApplier:
        async def apply(self, ctx: dict[str, Any], intents: list[RuntimeIntent]) -> None:
            called["session"] = ctx["session"]
            called["intents"] = intents

    monkeypatch.setattr("src.workline_runtime.runtime_intent_effects.RuntimeIntentEffectApplier", CapturingApplier)

    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    intents = [RuntimeIntent.update_context({"pkg_id": "PKG-001"})]
    await workline_effects._apply_orchestrator_effects(
        SimpleNamespace(add=MagicMock()),
        session=session,
        workline=SimpleNamespace(id=1, plugin_key="demo_plugin"),
        inbox=SimpleNamespace(id=10, trace_id="trace-runtime"),
        devices_by_role={},
        source_device=None,
        orch_result=OrchestratorResult(success=True, intents=intents),
    )

    assert called == {"session": session, "intents": intents}
