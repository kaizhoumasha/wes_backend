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
            RuntimeIntent.update_context({"rack_supply": {"status": "ARRIVED"}}),
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
                target_code="http://wms-rcs/api/full-box-exchange",
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
    assert outbox.target_code == "http://wms-rcs/api/full-box-exchange"
    assert outbox.payload_json == {"rack_release_id": "release-001"}
    assert session.status == "WAITING_EXTERNAL"
    assert session.current_wait_type == "EXTERNAL_HTTP"
    assert session.awaiting_command_id is None
    assert session.current_wait_timeout_seconds == 1800
    assert session.deadline_at == ctx["now"] + timedelta(seconds=1800)
    assert [timeline["action_type"].value for timeline in timelines] == ["EXTERNAL_CALL_STARTED", "WAIT_STARTED"]
    assert timelines[1]["payload"]["wait_token"] == "external:smt:release-001:FULL_BIN_EXCHANGE"


@pytest.mark.asyncio
async def test_rack_task_request_creates_rack_task_outbox_without_waiting_material_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timelines: list[dict[str, Any]] = []
    task_calls: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)

    class RecordingRackTaskService:
        async def record_requested_from_rack_task_request(self, **kwargs: Any) -> SimpleNamespace:
            task_calls.append(kwargs)
            return SimpleNamespace(id=901, task_key=kwargs["task_key"])

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        timelines.append(kwargs)

    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier(rack_task_service=RecordingRackTaskService()).apply(
        ctx,
        [
            RuntimeIntent.rack_task_request(
                task_type="RACK_SUPPLY",
                task_key="rack-task:supply:trace-runtime",
                dispatch_key="external:smt_classifier:trace-runtime:RACK_SUPPLY",
                target_code="http://wms-rcs/api/rack-supply",
                payload={
                    "request_type": "SMT_RACK_SUPPLY",
                    "dispatch_key": "external:smt_classifier:trace-runtime:RACK_SUPPLY",
                },
                timeout_seconds=1800,
                source_system="WMS_RCS",
                rack_code="RACK-001",
                position_code="SINGLE_LAYER_A",
                context_patch={"rack_supply": {"status": "REQUESTED"}},
            )
        ],
    )

    outbox = db.add.call_args.args[0]
    assert outbox.session_id is None
    assert outbox.workline_id == 1
    assert outbox.dispatch_type == "EXTERNAL_HTTP"
    assert outbox.target_type == "HTTP_ENDPOINT"
    assert outbox.dispatch_key == "external:smt_classifier:trace-runtime:RACK_SUPPLY"
    assert len(task_calls) == 1
    assert task_calls[0]["session"] is session
    assert task_calls[0]["workline"] is ctx["workline"]
    assert task_calls[0]["outbox"] is outbox
    assert task_calls[0]["task_type"] == "RACK_SUPPLY"
    assert task_calls[0]["task_key"] == "rack-task:supply:trace-runtime"
    assert task_calls[0]["rack_code"] == "RACK-001"
    assert task_calls[0]["position_code"] == "SINGLE_LAYER_A"
    assert session.status == "RUNNING"
    assert session.current_wait_type is None
    assert session.awaiting_command_id is None
    assert session.context_json["rack_supply"]["status"] == "REQUESTED"
    assert session.context_json["waiting_rack_task_id"] == 901
    assert [timeline["action_type"].value for timeline in timelines] == ["EXTERNAL_CALL_STARTED"]


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
                    target_code="http://wms-rcs/api/full-box-exchange",
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
                target_code="http://wms-rcs/api/full-box-exchange",
                payload={"rack_release_id": "release-001"},
                timeout_seconds=1800,
            )
        ],
    )

    assert workline_effects._result_requires_outbox_dispatch(result) is True


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
