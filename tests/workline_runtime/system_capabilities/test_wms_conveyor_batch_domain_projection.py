"""E12 只接 preparation hook；ACK/terminal 在后续任务前必须显式 fail-closed。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.effect_state_contract import EffectReducerEvent, EffectReducerEventType
from src.app.runtime.orchestration.services.wms_conveyor_batch_service import (
    WmsConveyorBatchCandidate,
    WmsConveyorBatchClaim,
)
from src.app.runtime.orchestration.services.wms_fulfillment_domain_projector import (
    WmsFulfillmentDomainProjector,
)
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES

E12 = "wms.fulfillment.move_bins_to_conveyor_entry@v1"


class _RecordingBatchService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def prepare_effect(self, db: Any, *, claim: Any, request: Any, intent_id: int) -> None:
        self.calls.append({"db": db, "claim": claim, "request": request, "intent_id": intent_id})


def _claim(request: Any) -> WmsConveyorBatchClaim:
    return WmsConveyorBatchClaim(
        workline_id=9,
        binding_id=17,
        binding_version=3,
        plugin_config_hash="a" * 64,
        queue_code=request.destination_station_code,
        entry_capacity=4,
        capacity_snapshot_version=request.capacity_snapshot_version,
        source_rack_code=request.items[0].source_rack_id,
        batch_id=request.batch_id,
        candidates=tuple(
            WmsConveyorBatchCandidate(
                route_instance_id=item.route_instance_id,
                bin_code=item.bin_id,
                source_rack_code=item.source_rack_id,
                source_slot_code=item.source_slot_id,
                reserved_queue_position=item.reserved_queue_position,
            )
            for item in request.items
        ),
    )


@pytest.mark.asyncio
async def test_e12_projector_delegates_preparation_before_outbox() -> None:
    operation = WMS_OPERATION_BY_IDENTITY[E12]
    request = operation.request_model.model_validate(REQUEST_FIXTURES[E12])
    claim = _claim(request)
    batch_service = _RecordingBatchService()
    projector = WmsFulfillmentDomainProjector(conveyor_batch=batch_service)
    db = SimpleNamespace()
    execution = SimpleNamespace(
        ctx={"wms_conveyor_batch_claim": claim},
        intent_log=SimpleNamespace(id=71),
    )

    await projector.prepare_effect(db, operation=operation, request=request, execution=execution)

    assert batch_service.calls == [{"db": db, "claim": claim, "request": request, "intent_id": 71}]


@pytest.mark.asyncio
async def test_e12_reducer_event_fails_closed_until_terminal_projection_is_implemented() -> None:
    operation = WMS_OPERATION_BY_IDENTITY[E12]
    event = EffectReducerEvent(
        event_type=EffectReducerEventType.STATUS_COMPLETED,
        dispatch_key=REQUEST_FIXTURES[E12]["dispatch_key"],
        occurred_at_ms=1,
        source_event_id="e12-terminal-not-yet-supported",
        evidence_json={},
    )

    with pytest.raises(RuntimeError, match=r"E12 ACK/terminal convergence is not bound"):
        await WmsFulfillmentDomainProjector().project_event(
            SimpleNamespace(),
            operation=operation,
            request_payload=REQUEST_FIXTURES[E12],
            event=event,
            reduction=SimpleNamespace(state_changed=True, contradiction=False),
        )
