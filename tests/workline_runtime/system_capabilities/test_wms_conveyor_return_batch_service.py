"""E13 退料队列候选冻结与 preparation 的纯服务合同。"""

from __future__ import annotations

from datetime import UTC, datetime
from importlib import import_module
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.effect_state_contract import EffectReducerEvent, EffectReducerEventType
from src.app.runtime.orchestration.services.wms_fulfillment_domain_projector import (
    WmsFulfillmentDomainProjector,
)
from src.app.wms_integration.operation_contract import WmsDomainProjectionKind
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES

E13 = "wms.fulfillment.move_bins_from_conveyor_exit@v1"
RETURN_SERVICE_MODULE = "src.app.runtime.orchestration.services.wms_conveyor_return_batch_service"


def _return_types() -> tuple[type[Any], type[Any]]:
    module = import_module(RETURN_SERVICE_MODULE)
    return module.WmsConveyorReturnBatchService, module.WmsConveyorReturnCandidateRow


class _Repository:
    def __init__(self, rows: tuple[Any, ...]) -> None:
        self.rows = rows
        self.lock_calls: list[dict[str, Any]] = []
        self.claim_calls: list[dict[str, Any]] = []

    async def lock_fifo_candidates(self, db: Any, **kwargs: Any) -> tuple[Any, ...]:
        self.lock_calls.append({"db": db, **kwargs})
        return self.rows

    async def claim_prepared_batch(self, db: Any, **kwargs: Any) -> None:
        self.claim_calls.append({"db": db, **kwargs})


def _candidate_rows(row_type: type[Any]) -> tuple[Any, ...]:
    return (
        row_type(
            membership_id=21,
            route_instance_id="route-oldest",
            bin_code="BIN-002",
            scan3_enqueued_at=datetime(2026, 7, 30, 8, 0),
            queue_position=2,
        ),
        row_type(
            membership_id=22,
            route_instance_id="route-next",
            bin_code="BIN-001",
            scan3_enqueued_at=datetime(2026, 7, 30, 8, 1),
            queue_position=1,
        ),
    )


def test_e13_operation_binds_authored_return_batch_projection_kind() -> None:
    operation = WMS_OPERATION_BY_IDENTITY[E13]

    assert operation.domain_projection_kind is WmsDomainProjectionKind.CONVEYOR_RETURN_BATCH
    assert operation.max_candidate_count == 12


@pytest.mark.asyncio
async def test_e13_reserve_freezes_bounded_fifo_candidates_without_rack_capacity_lookup() -> None:
    service_type, row_type = _return_types()
    repository = _Repository(_candidate_rows(row_type))
    service = service_type(
        repository=repository,
        id_factory=lambda: "winner-token",
    )
    db = SimpleNamespace()

    reservation = await service.reserve_batch(
        db,
        workline_id=9,
        queue_code="RETURN_QUEUE",
    )

    assert reservation.created is True
    assert reservation.operation is WMS_OPERATION_BY_IDENTITY[E13]
    assert reservation.claim is not None
    assert reservation.request is not None
    assert repository.lock_calls == [
        {
            "db": db,
            "workline_id": 9,
            "queue_code": "RETURN_QUEUE",
            "limit": 12,
        }
    ]
    assert tuple(item.bin_id for item in reservation.request.candidate_items) == ("BIN-002", "BIN-001")
    assert tuple(item.route_instance_id for item in reservation.request.candidate_items) == (
        "route-oldest",
        "route-next",
    )
    assert reservation.request.candidate_digest == reservation.claim.candidate_digest
    assert reservation.request.batch_id.startswith("wms-e13:9:")
    assert reservation.request.dispatch_key == reservation.request.batch_id


@pytest.mark.asyncio
async def test_e13_reserve_returns_empty_without_creating_an_effect() -> None:
    service_type, _row_type = _return_types()
    service = service_type(repository=_Repository(()), id_factory=lambda: "unused")

    reservation = await service.reserve_batch(
        SimpleNamespace(),
        workline_id=9,
        queue_code="RETURN_QUEUE",
    )

    assert reservation.created is False
    assert reservation.claim is None
    assert reservation.operation is None
    assert reservation.request is None


@pytest.mark.asyncio
async def test_e13_prepare_claims_the_frozen_source_memberships_before_outbox() -> None:
    service_type, row_type = _return_types()
    repository = _Repository(_candidate_rows(row_type))
    service = service_type(
        repository=repository,
        id_factory=lambda: "winner-token",
        now_for_db=lambda: datetime(2026, 7, 30, 8, 2),
        now_ms=lambda: 1234,
        claim_lease_seconds=30,
    )
    db = SimpleNamespace()
    reservation = await service.reserve_batch(db, workline_id=9, queue_code="RETURN_QUEUE")
    assert reservation.claim is not None
    assert reservation.request is not None

    await service.prepare_effect(
        db,
        claim=reservation.claim,
        request=reservation.request,
        intent_id=71,
    )

    assert len(repository.claim_calls) == 1
    call = repository.claim_calls[0]
    assert call["intent_id"] == 71
    assert call["claim_token"] == reservation.claim.claim_token
    assert call["claim_until"] == datetime(2026, 7, 30, 8, 2, 30)
    assert call["staged_at_ms"] == 1234
    assert tuple(candidate.membership_id for candidate in call["candidates"]) == (21, 22)


@pytest.mark.asyncio
async def test_e13_projector_delegates_only_preparation_to_the_return_service() -> None:
    service_type, row_type = _return_types()
    repository = _Repository(_candidate_rows(row_type))
    return_service = service_type(repository=repository, id_factory=lambda: "winner-token")
    db = SimpleNamespace()
    reservation = await return_service.reserve_batch(db, workline_id=9, queue_code="RETURN_QUEUE")
    assert reservation.claim is not None
    assert reservation.request is not None
    execution = SimpleNamespace(
        ctx={"wms_conveyor_return_batch_claim": reservation.claim},
        intent_log=SimpleNamespace(id=71),
    )

    await WmsFulfillmentDomainProjector(conveyor_return_batch=return_service).prepare_effect(
        db,
        operation=WMS_OPERATION_BY_IDENTITY[E13],
        request=reservation.request,
        execution=execution,
    )

    assert len(repository.claim_calls) == 1


def test_e13_wire_timestamp_is_normalized_from_naive_utc_storage() -> None:
    service_type, row_type = _return_types()
    repository = _Repository(_candidate_rows(row_type))
    service = service_type(repository=repository, id_factory=lambda: "winner-token")

    assert (
        service.candidate_timestamp(_candidate_rows(row_type)[0].scan3_enqueued_at)
        == datetime(
            2026,
            7,
            30,
            8,
            0,
            tzinfo=UTC,
        ).isoformat()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type",
    [
        EffectReducerEventType.ASYNC_SUBMIT_REJECTED,
        EffectReducerEventType.STATUS_COMPLETED,
    ],
)
async def test_e13_unimplemented_event_projection_fails_closed_without_rack_demand_fallback(
    event_type: EffectReducerEventType,
) -> None:
    event = EffectReducerEvent(
        event_type=event_type,
        dispatch_key=REQUEST_FIXTURES[E13]["dispatch_key"],
        occurred_at_ms=1,
        source_event_id=f"e13-unbound:{event_type.value}",
        attempt_no=1 if event_type is EffectReducerEventType.ASYNC_SUBMIT_REJECTED else None,
        evidence_json={},
    )

    with pytest.raises(RuntimeError, match="E13 event projection is not bound"):
        await WmsFulfillmentDomainProjector().project_event(
            SimpleNamespace(),
            operation=WMS_OPERATION_BY_IDENTITY[E13],
            request_payload=REQUEST_FIXTURES[E13],
            event=event,
            reduction=SimpleNamespace(state_changed=True, contradiction=False),
        )


@pytest.mark.asyncio
async def test_e13_unimplemented_reconciliation_projection_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="E13 reconciliation projection is not bound"):
        await WmsFulfillmentDomainProjector().project_reconciliation_opened(
            SimpleNamespace(),
            operation=WMS_OPERATION_BY_IDENTITY[E13],
            dispatch_key=REQUEST_FIXTURES[E13]["dispatch_key"],
            reason_code="TEST_E13_RECONCILIATION",
            evidence_json={},
        )
