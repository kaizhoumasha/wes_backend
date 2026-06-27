from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.wms_integration.services import WmsTransportContractService
from src.app.workline.services import write_back_service as workline_effects
from src.app.workline.services.inbox_batch_processor import _result_requires_outbox_dispatch
from src.app.workline.services.inbox_service import DuplicateInboxError
from src.workline_runtime.effect_result import WriteBackDisposition
from src.workline_runtime.orchestrator import OrchestratorResult
from src.workline_runtime.runtime_intent import RuntimeIntent, RuntimeIntentKind
from src.workline_runtime.runtime_intent_effects import (
    RuntimeIntentEffectApplier,
)
from tests.workline_runtime.support.runtime_intent_effects import (
    RecordingHandlingOperationService,
    RecordingResourceProjectionService,
    _ctx,
    _session,
)


@pytest.mark.asyncio
async def test_external_request_intent_creates_external_outbox_and_immediate_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timelines: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
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
    assert session.awaiting_device_command_code is None
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
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
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
    assert session.awaiting_device_command_code is None
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
async def test_single_layer_rack_operation_creates_waiting_external_outbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timelines: list[dict[str, Any]] = []
    operation_calls: list[dict[str, Any]] = []
    created_outboxes: list[SimpleNamespace] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["workline"].line_code = "WL-SMT-01"

    contract = WmsTransportContractService().build_single_layer_rack_operation_request(
        business_demand_key="WMS-DEMAND-001",
        workline_code="WL-SMT-01",
        endpoint_code="SINGLE_LAYER_A",
        rack_kind="SINGLE_LAYER",
        rack_snapshot_ref="snapshot:WL-SMT-01:SINGLE_LAYER_A",
        operation_type="SUPPLY_SINGLE_LAYER_RACK",
        payload={
            "station": {"position_code": "SINGLE_LAYER_A"},
            "target_position_code": "SINGLE_LAYER_A",
            "rack_tasks": [
                {
                    "sequence_no": 1,
                    "task_type": "ALLOCATE_AND_MOVE_RACK",
                    "rack_kind": "SINGLE_LAYER",
                    "target_position_code": "SINGLE_LAYER_A",
                    "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                }
            ],
            "trace_id": "trace-single-layer-001",
        },
        timeout_seconds=1800,
    )

    class RecordingRackOperationService:
        async def request_operation_tasks(self, db: Any, **kwargs: Any) -> list[SimpleNamespace]:
            operation_calls.append({"db": db, **kwargs})
            task_spec = kwargs["task_specs"][0]
            dispatch_key = "rack-operation:{operation_key}:{sequence_no}:{task_type}".format(
                operation_key=kwargs["operation_key"],
                sequence_no=task_spec["sequence_no"],
                task_type=task_spec["task_type"],
            )
            created_outboxes.append(
                SimpleNamespace(
                    dispatch_type="EXTERNAL_HTTP",
                    target_type="HTTP_ENDPOINT",
                    dispatch_key=dispatch_key,
                    target_code=kwargs["target_code"],
                    payload_json=task_spec["request_json"],
                )
            )
            return [
                SimpleNamespace(
                    id=901,
                    operation_key=kwargs["operation_key"],
                    sequence_no=1,
                    task_type=task_spec["task_type"],
                    dispatch_key=dispatch_key,
                    actions_json={"required": True},
                    rack_code=None,
                    target_position_code=task_spec["target_position_code"],
                )
            ]

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        timelines.append(kwargs)

    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier(rack_operation_service=RecordingRackOperationService()).apply(
        ctx,
        [RuntimeIntent.rack_operation_request(**contract)],
    )

    assert db.add.call_count == 0
    assert operation_calls[0]["operation_key"] == "wms-rack-operation:WMS-DEMAND-001:WL-SMT-01:SINGLE_LAYER_A"
    assert operation_calls[0]["target_code"] == "WMS_RCS_RACK_OPERATION"
    assert operation_calls[0]["trace_id"] == "trace-single-layer-001"
    assert operation_calls[0]["task_specs"][0]["request_json"]["business_demand_key"] == "WMS-DEMAND-001"
    assert operation_calls[0]["task_specs"][0]["request_json"]["station"]["position_code"] == "SINGLE_LAYER_A"
    assert created_outboxes[0].dispatch_type == "EXTERNAL_HTTP"
    assert created_outboxes[0].target_type == "HTTP_ENDPOINT"
    assert created_outboxes[0].dispatch_key == (
        "rack-operation:wms-rack-operation:WMS-DEMAND-001:WL-SMT-01:SINGLE_LAYER_A:1:ALLOCATE_AND_MOVE_RACK"
    )
    assert created_outboxes[0].target_code == "WMS_RCS_RACK_OPERATION"
    assert session.status == "WAITING_EXTERNAL"
    assert session.current_wait_type == "RACK_OPERATION"
    assert session.context_json["waiting_rack_operation_key"] == (
        "wms-rack-operation:WMS-DEMAND-001:WL-SMT-01:SINGLE_LAYER_A"
    )
    assert session.context_json["rack_operation"]["task_dispatch_keys"] == [
        "rack-operation:wms-rack-operation:WMS-DEMAND-001:WL-SMT-01:SINGLE_LAYER_A:1:ALLOCATE_AND_MOVE_RACK"
    ]
    assert session.context_json["rack_operation"]["target_position_code"] == "SINGLE_LAYER_A"
    assert timelines[0]["payload"]["wait_token"] == "wms-rack-operation:WMS-DEMAND-001:WL-SMT-01:SINGLE_LAYER_A"


@pytest.mark.asyncio
async def test_rack_operation_station_lease_race_returns_resource_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None, context_json={})
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["workline"].plugin_key = "rough_sorter"
    emit_timeline = AsyncMock()
    persist_external_wait = AsyncMock()
    record_resource_wait = AsyncMock()

    class LosingRackOperationService:
        async def request_operation_tasks(self, db: Any, **kwargs: Any) -> list[SimpleNamespace]:
            _ = db, kwargs
            raise ValueError("station dispatch lease is not available")

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(
        "src.app.workline.repositories.session_repository.WorklineSessionRepository.persist_external_wait",
        persist_external_wait,
    )
    monkeypatch.setattr(
        "src.app.workline.services.diagnostic_service.workline_diagnostic_service.record_resource_wait",
        record_resource_wait,
    )

    result = await RuntimeIntentEffectApplier(rack_operation_service=LosingRackOperationService()).apply(
        ctx,
        [
            RuntimeIntent.rack_operation_request(
                operation_type="REPLACE_CLASSIFIER_WORK_RACK",
                operation_key="rack-operation:trace-runtime",
                target_code="WMS_RCS_RACK_OPERATION",
                payload={
                    "trace_id": "trace-runtime",
                    "rack_tasks": [
                        {
                            "sequence_no": 1,
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

    assert result.disposition == WriteBackDisposition.RESOURCE_RETRY
    assert session.status == "WAITING_EXTERNAL"
    assert session.current_wait_type == "RESOURCE_WAIT"
    assert session.context_json["resource_wait"]["subject_type"] == "SINGLE_LAYER_A"
    assert session.context_json["resource_wait"]["subject_key"] == "station:SINGLE_LAYER_A"
    assert session.context_json["resource_wait"]["projection_type"] == "STATION_LEASE"
    assert session.context_json["resource_wait"]["reason_code"] == "STATION_LEASE_CLAIM_FAILED"
    assert "rack_operation" not in session.context_json
    persist_external_wait.assert_awaited_once()
    record_resource_wait.assert_awaited_once()
    emit_timeline.assert_awaited_once()


@pytest.mark.asyncio
async def test_rack_operation_request_stores_operation_wait_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_calls: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(
        status="RUNNING",
        current_wait_type=None,
        awaiting_device_command_code=None,
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
async def test_rack_operation_request_carries_material_context_into_task_specs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_calls: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)

    class RecordingRackOperationService:
        async def request_operation_tasks(self, _db: Any, **kwargs: Any) -> list[SimpleNamespace]:
            operation_calls.append(kwargs)
            return [
                SimpleNamespace(
                    id=901,
                    operation_key=kwargs["operation_key"],
                    sequence_no=1,
                    task_type="ALLOCATE_AND_MOVE_RACK",
                    dispatch_key="rack-operation:rack-operation:trace-runtime:1:ALLOCATE_AND_MOVE_RACK",
                    actions_json={"required": True},
                    rack_code=None,
                )
            ]

    async def capture_timeline(_ctx: dict[str, Any], **_kwargs: Any) -> None:
        return None

    material = {
        "HHPN": "IC001",
        "LotCode": "LOT-I",
        "DateCode": "20260413",
        "PkgID": "PKG-IC001-LOT-I-001",
        "reel_diameter": "330.0",
        "reel_thickness": "24.0",
    }
    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier(rack_operation_service=RecordingRackOperationService()).apply(
        ctx,
        [
            RuntimeIntent.rack_operation_request(
                operation_type="REPLACE_CLASSIFIER_WORK_RACK",
                operation_key="rack-operation:trace-runtime",
                target_code="WMS_RCS_RACK_OPERATION",
                payload={
                    "material": material,
                    "rack_tasks": [
                        {
                            "sequence_no": 1,
                            "task_type": "ALLOCATE_AND_MOVE_RACK",
                            "rack_kind": "SINGLE_LAYER",
                            "target_position_code": "SINGLE_LAYER_A",
                        }
                    ],
                },
                timeout_seconds=1800,
            )
        ],
    )

    assert operation_calls[0]["task_specs"][0]["request_json"]["material"] == material


@pytest.mark.asyncio
async def test_rack_operation_request_persists_external_wait_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(
        status="WAITING_DEVICE_RESULT",
        current_wait_type="COMMAND_RESULT",
        awaiting_device_command_code="CMD-TEST-001",
        context_json={},
    )
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    persist_wait = AsyncMock()

    class RecordingRackOperationService:
        async def request_operation_tasks(self, _db: Any, **kwargs: Any) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(
                    id=901,
                    operation_key=kwargs["operation_key"],
                    sequence_no=1,
                    task_type="ALLOCATE_AND_MOVE_RACK",
                    dispatch_key="rack-operation:rack-operation:trace-runtime:1:ALLOCATE_AND_MOVE_RACK",
                    actions_json={"required": True},
                    rack_code=None,
                )
            ]

    class RecordingSessionRepository:
        async def persist_external_wait(self, *args: Any, **kwargs: Any) -> None:
            await persist_wait(*args, **kwargs)

    async def capture_timeline(_ctx: dict[str, Any], **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)
    monkeypatch.setattr(
        "src.app.workline.repositories.session_repository.WorklineSessionRepository",
        RecordingSessionRepository,
    )

    await RuntimeIntentEffectApplier(rack_operation_service=RecordingRackOperationService()).apply(
        ctx,
        [
            RuntimeIntent.rack_operation_request(
                operation_type="REPLACE_CLASSIFIER_WORK_RACK",
                operation_key="rack-operation:trace-runtime",
                target_code="WMS_RCS_RACK_OPERATION",
                payload={
                    "rack_tasks": [
                        {
                            "sequence_no": 1,
                            "task_type": "ALLOCATE_AND_MOVE_RACK",
                            "rack_kind": "SINGLE_LAYER",
                            "target_position_code": "SINGLE_LAYER_A",
                        }
                    ]
                },
                timeout_seconds=1800,
            )
        ],
    )

    persist_wait.assert_awaited_once_with(
        db,
        session_id=session.id,
        wait_type="RACK_OPERATION",
        occurred_at=ctx["now"],
        timeout_seconds=1800,
        context_json=session.context_json,
    )
    assert session.status == "WAITING_EXTERNAL"
    assert session.current_wait_type == "RACK_OPERATION"
    assert session.awaiting_device_command_code is None


@pytest.mark.asyncio
async def test_rack_operation_request_preserves_operation_metadata_written_by_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    operation_key = "rack-operation:trace-runtime"
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
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
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
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
    assert session.awaiting_device_command_code is None
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
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    service = RecordingHandlingOperationService()

    monkeypatch.setattr(workline_effects, "_emit_timeline", AsyncMock())

    await RuntimeIntentEffectApplier(handling_operation_service=service).apply(
        ctx,
        [
            RuntimeIntent.rack_bin_exchange_request(
                operation_type="SINGLE_LAYER_FULL_BOX_EXCHANGE",
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
    assert service.calls[0]["operation_type"] == "SINGLE_LAYER_FULL_BOX_EXCHANGE"
    assert service.calls[0]["moves"][1]["placeholder_key"] == "EMPTY_BIN_FOR:SINGLE_LAYER_A:01"
    assert session.current_wait_type == "HANDLING_OPERATION"
    assert session.context_json["handling_operation"]["rack_code"] == "RACK-SINGLE-01"
    assert session.context_json["handling_operation"]["move_sequences"] == [1, 2]


def test_rack_operation_wait_released_rack_codes_include_only_move_out_tasks() -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
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
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
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
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
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
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None, trace_id=None)
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
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
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
    assert session.awaiting_device_command_code is None


@pytest.mark.asyncio
async def test_external_request_before_terminal_intent_is_rejected_before_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
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

    assert _result_requires_outbox_dispatch(result) is True


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

    assert _result_requires_outbox_dispatch(result) is True


def test_wait_session_status_maps_rack_operation_to_external_wait() -> None:
    assert workline_effects._wait_session_status("RACK_OPERATION") == "WAITING_EXTERNAL"


@pytest.mark.asyncio
async def test_resource_fact_then_device_event_creates_storage_retry_inbox() -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    resource_projection = RecordingResourceProjectionService()

    class RecordingInboxService:
        def __init__(self) -> None:
            self.created: dict[str, Any] = {}

        async def create_device_event_inbox(self, **kwargs: Any) -> object:
            self.created = kwargs
            return SimpleNamespace(id=789)

    recording_inbox_service = RecordingInboxService()
    retry_event_id = "rough-sorter-storage-retry:external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION:321"

    await RuntimeIntentEffectApplier(
        resource_projection_service=resource_projection,
        inbox_service=recording_inbox_service,
    ).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="RACK_ARRIVED",
                payload={
                    "operation_key": "external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION",
                    "rack_code": "RACK-001",
                },
                idempotency_key="RACK_ARRIVED:external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION",
            ),
            RuntimeIntent.device_event(
                device_code="RS-CONVEYOR-01",
                event_type="ROUGH_SORTER_STORAGE_RETRY",
                timestamp=1770000000000,
                data={"PkgID": "PKG-ROUGH-001", "idempotency_key": retry_event_id},
                event_id=retry_event_id,
                causation_id="wms-rack-arrived-001",
                canonical_event_type="ROUGH_SORTER_STORAGE_RETRY",
            ),
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "RACK_ARRIVED"
    assert recording_inbox_service.created["event_type"] == "ROUGH_SORTER_STORAGE_RETRY"
    assert recording_inbox_service.created["event_id"] == retry_event_id
    assert recording_inbox_service.created["data"]["idempotency_key"] == retry_event_id
    assert recording_inbox_service.created["canonical_event_type"] == "ROUGH_SORTER_STORAGE_RETRY"
    assert recording_inbox_service.created["auto_commit"] is False


@pytest.mark.asyncio
async def test_resource_fact_duplicate_storage_retry_device_event_is_treated_as_idempotent() -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    resource_projection = RecordingResourceProjectionService()

    class DuplicateInboxService:
        def __init__(self) -> None:
            self.calls = 0

        async def create_device_event_inbox(self, **_kwargs: Any) -> object:
            self.calls += 1
            raise DuplicateInboxError(
                "设备事件已存在（幂等键重复）: device_event:retry",
                existing_inbox=SimpleNamespace(id=789),
            )

    duplicate_inbox_service = DuplicateInboxService()
    retry_event_id = "rough-sorter-storage-retry:external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION:321"

    await RuntimeIntentEffectApplier(
        resource_projection_service=resource_projection,
        inbox_service=duplicate_inbox_service,
    ).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="RACK_ARRIVED",
                payload={
                    "operation_key": "external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION",
                    "rack_code": "RACK-001",
                },
                idempotency_key="RACK_ARRIVED:external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION",
            ),
            RuntimeIntent.device_event(
                device_code="RS-CONVEYOR-01",
                event_type="ROUGH_SORTER_STORAGE_RETRY",
                timestamp=1770000000000,
                data={"PkgID": "PKG-ROUGH-001", "idempotency_key": retry_event_id},
                event_id=retry_event_id,
                causation_id="wms-rack-arrived-duplicate",
                canonical_event_type="ROUGH_SORTER_STORAGE_RETRY",
            ),
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "RACK_ARRIVED"
    assert duplicate_inbox_service.calls == 1
