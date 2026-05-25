from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.handling.models import (
    HandlingMove,
    HandlingObjectType,
    HandlingOperation,
    HandlingOperationStatus,
    HandlingStep,
    HandlingStepKind,
    HandlingStepStatus,
    SystemOutbox,
    SystemOutboxDispatchType,
)
from src.app.handling.services import HandlingOperationService, WmsRcsHandlingGateway


class FakeOperationRepository:
    def __init__(self) -> None:
        self.by_key: dict[str, SimpleNamespace] = {}
        self.created: list[dict[str, Any]] = []

    async def get_by_operation_key(self, _db: Any, operation_key: str) -> SimpleNamespace | None:
        return self.by_key.get(operation_key)

    async def create(self, _db: Any, data: dict[str, Any]) -> SimpleNamespace:
        operation = SimpleNamespace(id=len(self.created) + 1, **data)
        self.created.append(data)
        self.by_key[operation.operation_key] = operation
        return operation


class FakeMoveRepository:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create(self, _db: Any, data: dict[str, Any]) -> SimpleNamespace:
        move = SimpleNamespace(id=len(self.created) + 1, **data)
        self.created.append(data)
        return move


class FakeStepRepository:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create(self, _db: Any, data: dict[str, Any]) -> SimpleNamespace:
        step = SimpleNamespace(id=len(self.created) + 1, **data)
        self.created.append(data)
        return step


class FakeOutboxRepository:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create(self, _db: Any, data: dict[str, Any]) -> SimpleNamespace:
        outbox = SimpleNamespace(id=len(self.created) + 1, **data)
        self.created.append(data)
        return outbox


class FakeGateway:
    def build_ctu_move_envelope(self, *, operation: Any, move: Any, sequence_no: int) -> dict[str, Any]:
        return {
            "dispatch_key": f"handling:{operation.operation_key}:move:{sequence_no}",
            "target_code": "WMS_RCS_BIN_OPERATION",
            "payload_json": {
                "request_type": "BIN_MOVE",
                "operation_key": operation.operation_key,
                "source": {"type": move.source_type, "code": move.source_code},
                "target": {"type": move.target_type, "code": move.target_code},
                "carrier": {"type": move.carrier_type, "code": move.carrier_code},
            },
        }


def test_handling_models_are_system_level_contracts() -> None:
    assert HandlingOperation.__tablename__ == "handling_operations"
    assert HandlingMove.__tablename__ == "handling_operation_moves"
    assert HandlingStep.__tablename__ == "handling_operation_steps"
    assert SystemOutbox.__tablename__ == "system_outbox"
    assert "workline_id" in HandlingOperation.model_fields
    assert "material_session_id" in HandlingOperation.model_fields
    assert "operation_key" in HandlingOperation.model_fields
    assert "object_type" in HandlingMove.model_fields
    assert "dispatch_key" in HandlingStep.model_fields
    assert "trace_id" in HandlingStep.model_fields
    assert "dispatch_type" in SystemOutbox.model_fields


def test_wms_rcs_gateway_builds_documented_ctu_request_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WMS_RCS_BIN_OPERATION_URL", "http://wms-rcs/api/wes/transport-request")
    operation = SimpleNamespace(
        operation_key="full-box:release-001",
        operation_type="FULL_BOX_EXCHANGE_BIN_MOVE",
        trace_id="trace-full-box-001",
        workline_code="SMT_SORTER_01",
        material_session_id=81,
    )
    move = SimpleNamespace(
        object_type=HandlingObjectType.BIN.value,
        bin_code="BIN-001",
        placeholder_key=None,
        candidate_authorized_bin_ids=["BIN-001", "BIN-002"],
        source_type="RACK_SLOT",
        source_code="RACK-001:A",
        target_type="BUFFER",
        target_code="SMT_BUFFER",
        carrier_type="CTU",
        carrier_code=None,
        rack_code="RACK-001",
        rack_slot_code="A",
        metadata_json={"rack_type": "SINGLE_LAYER", "priority": 8},
    )

    envelope = WmsRcsHandlingGateway().build_ctu_move_envelope(operation=operation, move=move, sequence_no=1)
    payload = envelope["payload_json"]

    assert envelope["target_code"] == "WMS_RCS_FULL_BOX_EXCHANGE"
    assert envelope["dispatch_key"] == "handling:full-box:release-001:move:1"
    assert payload["request_id"] == envelope["dispatch_key"]
    assert payload["dispatch_key"] == envelope["dispatch_key"]
    assert payload["exchange_request_code"] == envelope["dispatch_key"]
    assert payload["callback_type"] == "WMS_FULL_BOX_EXCHANGE_RESULT"
    assert payload["request_type"] == "FULL_BIN_EXCHANGE"
    assert payload["rack_id"] == "RACK-001"
    assert payload["rack_type"] == "SINGLE_LAYER"
    assert payload["from_location"] == "RACK-001:A"
    assert payload["to_location"] == "SMT_BUFFER"
    assert payload["priority"] == 8


@pytest.mark.asyncio
async def test_request_bin_operation_uses_internal_move_specs_and_creates_steps() -> None:
    operation_repo = FakeOperationRepository()
    move_repo = FakeMoveRepository()
    step_repo = FakeStepRepository()
    outbox_repo = FakeOutboxRepository()
    service = HandlingOperationService(
        operation_repository=operation_repo,
        move_repository=move_repo,
        step_repository=step_repo,
        outbox_repository=outbox_repo,
        gateway=FakeGateway(),
    )

    operation = await service.request_bin_operation(
        SimpleNamespace(),
        operation_type="SORTER_FEED_BIN",
        operation_key="bin-op:trace-001",
        moves=[
            {
                "placeholder_key": "feed-batch-001:slot-01",
                "candidate_authorized_bin_ids": ["BIN-001", "BIN-002"],
                "source_type": "RACK_SLOT",
                "source_code": "FIVE_LAYER_A1",
                "target_type": "SORTER_HANDOFF",
                "target_code": "SORTER_01_INFEED",
                "rack_code": "RACK-001",
                "rack_slot_code": "A1",
            }
        ],
        workline_id=9,
        workline_code="SMT_SORTER_01",
        material_session_id=81,
        trace_id="trace-001",
        carrier_type="CTU",
        carrier_code=None,
        timeout_seconds=300,
    )

    assert operation.operation_key == "bin-op:trace-001"
    assert operation.object_type == HandlingObjectType.BIN.value
    assert operation.operation_status == HandlingOperationStatus.REQUESTED.value
    assert operation_repo.created[0]["workline_code"] == "SMT_SORTER_01"
    assert move_repo.created[0]["placeholder_key"] == "feed-batch-001:slot-01"
    assert move_repo.created[0]["candidate_authorized_bin_ids"] == ["BIN-001", "BIN-002"]
    assert move_repo.created[0]["rack_code"] == "RACK-001"
    assert move_repo.created[0]["rack_slot_code"] == "A1"
    assert step_repo.created[0]["step_kind"] == HandlingStepKind.EXTERNAL_REQUEST.value
    assert step_repo.created[0]["step_status"] == HandlingStepStatus.REQUESTED.value
    assert step_repo.created[0]["dispatch_key"] == "handling:bin-op:trace-001:move:1"
    assert outbox_repo.created[0]["dispatch_type"] == SystemOutboxDispatchType.EXTERNAL_HTTP.value
    assert outbox_repo.created[0]["dispatch_key"] == "handling:bin-op:trace-001:move:1"
    assert outbox_repo.created[0]["target_code"] == "WMS_RCS_BIN_OPERATION"


@pytest.mark.asyncio
async def test_request_bin_operation_rejects_idempotency_key_context_mismatch() -> None:
    operation_repo = FakeOperationRepository()
    operation_repo.by_key["bin-op:trace-001"] = SimpleNamespace(
        operation_key="bin-op:trace-001",
        operation_type="SORTER_FEED_BIN",
        workline_id=9,
        workline_code="SMT_SORTER_01",
        material_session_id=81,
        trace_id="trace-001",
        request_json={
            "moves": [
                {
                    "source_type": "RACK_SLOT",
                    "source_code": "FIVE_LAYER_A1",
                    "target_type": "SORTER_HANDOFF",
                    "target_code": "SORTER_01_INFEED",
                }
            ],
            "timeout_seconds": 300,
        },
    )
    service = HandlingOperationService(
        operation_repository=operation_repo,
        move_repository=FakeMoveRepository(),
        step_repository=FakeStepRepository(),
        outbox_repository=FakeOutboxRepository(),
        gateway=FakeGateway(),
    )

    with pytest.raises(ValueError, match="operation_key 已存在但请求上下文不一致"):
        await service.request_bin_operation(
            SimpleNamespace(),
            operation_type="SORTER_FEED_BIN",
            operation_key="bin-op:trace-001",
            moves=[
                {
                    "source_type": "RACK_SLOT",
                    "source_code": "FIVE_LAYER_A1",
                    "target_type": "SORTER_HANDOFF",
                    "target_code": "SORTER_02_INFEED",
                }
            ],
            workline_id=9,
            workline_code="SMT_SORTER_01",
            material_session_id=82,
            trace_id="trace-001",
            timeout_seconds=300,
        )


@pytest.mark.asyncio
async def test_request_bin_operation_rejects_external_protocol_fields_from_callers() -> None:
    service = HandlingOperationService(
        operation_repository=FakeOperationRepository(),
        move_repository=FakeMoveRepository(),
        step_repository=FakeStepRepository(),
        outbox_repository=FakeOutboxRepository(),
        gateway=FakeGateway(),
    )

    with pytest.raises(ValueError, match="插件不得传入外部派发字段"):
        await service.request_bin_operation(
            SimpleNamespace(),
            operation_type="SORTER_FEED_BIN",
            operation_key="bin-op:trace-002",
            moves=[
                {
                    "source_type": "BUFFER",
                    "source_code": "BUF-01",
                    "target_type": "SORTER_HANDOFF",
                    "target_code": "SORTER_01_INFEED",
                    "dispatch_key": "caller-provided-dispatch",
                }
            ],
            trace_id="trace-002",
        )
