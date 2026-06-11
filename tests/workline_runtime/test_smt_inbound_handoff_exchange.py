"""SMT inbound handoff full-box exchange decision and callback tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from src.app.workline.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.workline.services.smt_inbound_handoff_service import SmtInboundHandoffService


class RecordingHandlingOperationService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def request_bin_operation(self, _db: Any, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(id=len(self.calls), operation_key=kwargs["operation_key"])


class RecordingHandoffService(SmtInboundHandoffService):
    def __init__(self, *, handling_operation_service: RecordingHandlingOperationService | None = None) -> None:
        self.recalculate_reasons: list[str | None] = []
        super().__init__(handling_operation_service=handling_operation_service or RecordingHandlingOperationService())

    async def recalculate_demand_status(
        self,
        db: Any,
        demand: SmtInboundHandoffDemand,
        *,
        reason: str | None = None,
    ) -> SmtInboundHandoffDemand:
        self.recalculate_reasons.append(reason)
        return await super().recalculate_demand_status(db, demand, reason=reason)


def _release_bin(slot_code: str, usage: float) -> dict[str, Any]:
    return {
        "slot_code": slot_code,
        "bin_code": f"BIN-{slot_code}",
        "usage": usage,
        "status": "IN_USE",
        "cells": [
            {
                "bin_code": f"BIN-{slot_code}",
                "bin_cell_index": 1,
                "bin_cell_code": f"BIN-{slot_code}-1",
                "status": "OCCUPIED",
                "material_identity_key": f"MAT-{slot_code}",
                "pkg_code": f"PKG-{slot_code}",
                "reel_thickness_mm": "1.2",
            }
        ],
    }


def _release_payload(*, rack_release_id: str, usages: tuple[float, ...]) -> dict[str, Any]:
    return {
        "rack_release_id": rack_release_id,
        "single_layer_rack_code": f"RACK-{rack_release_id}",
        "source_workline_id": 1001,
        "source_workline_code": "WL-SMT-ROUGH-01",
        "release_reason_code": "NO_COMPATIBLE_OR_EMPTY_CELL",
        "bin_snapshots": [_release_bin(chr(ord("A") + index), usage) for index, usage in enumerate(usages)],
        "trace_id": f"trace-{rack_release_id}",
    }


async def _create_demand(
    db_session: Any,
    service: SmtInboundHandoffService,
    *,
    rack_release_id: str,
    usages: tuple[float, ...],
) -> SmtInboundHandoffDemand:
    return await service.create_or_get_from_release(
        db_session,
        **_release_payload(rack_release_id=rack_release_id, usages=usages),
    )


async def _items_for_demand(db_session: Any, demand: SmtInboundHandoffDemand) -> list[SmtInboundHandoffSourceItem]:
    result = await db_session.execute(
        select(SmtInboundHandoffSourceItem)
        .where(SmtInboundHandoffSourceItem.handoff_demand_id == demand.id)
        .order_by(SmtInboundHandoffSourceItem.item_key)
    )
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_usage_below_half_skips_exchange_and_creates_ready_source_items(db_session: Any) -> None:
    handling = RecordingHandlingOperationService()
    service = RecordingHandoffService(handling_operation_service=handling)
    demand = await _create_demand(db_session, service, rack_release_id="release-low", usages=(0.25, 0.4))

    evaluated = await service.evaluate(db_session, demand=demand)
    items = await _items_for_demand(db_session, evaluated)

    assert handling.calls == []
    assert evaluated.status == SmtInboundHandoffDemandStatus.READY_FOR_SORTING
    assert evaluated.decision_status == "DIRECT_SORTING"
    assert evaluated.handling_operation_key is None
    assert [item.status for item in items] == [SmtInboundHandoffSourceItemStatus.READY] * 2
    assert service.recalculate_reasons == ["evaluate"]


@pytest.mark.asyncio
async def test_usage_at_or_above_required_threshold_requests_single_layer_full_box_exchange(
    db_session: Any,
) -> None:
    handling = RecordingHandlingOperationService()
    service = RecordingHandoffService(handling_operation_service=handling)
    demand = await _create_demand(db_session, service, rack_release_id="release-high", usages=(0.8,))

    evaluated = await service.evaluate(db_session, demand=demand)

    assert evaluated.status == SmtInboundHandoffDemandStatus.WAITING_FULL_BOX_EXCHANGE
    assert evaluated.decision_status == "REQUIRED_FULL_BOX_EXCHANGE_REQUESTED"
    assert evaluated.handling_operation_key == "smt-inbound-handoff:release-high:full-box-exchange"
    assert len(handling.calls) == 1
    call = handling.calls[0]
    assert call["operation_type"] == "SINGLE_LAYER_FULL_BOX_EXCHANGE"
    assert call["operation_key"] == evaluated.handling_operation_key
    assert call["trace_id"] == "trace-release-high"
    assert call["workline_id"] == 1001
    assert call["workline_code"] == "WL-SMT-ROUGH-01"
    assert call["carrier_type"] == "CTU"
    assert call["carrier_code"] == "RACK-release-high"
    assert call["moves"] == [
        {
            "source_type": "RACK_SLOT",
            "source_code": "RACK-release-high:A",
            "target_type": "FULL_BOX_EXCHANGE_BUFFER",
            "target_code": "SMT_FULL_BOX_EXCHANGE",
            "rack_code": "RACK-release-high",
            "rack_slot_code": "A",
            "bin_code": "BIN-A",
            "required": True,
        }
    ]
    forbidden_move_fields = {
        "dispatch_key",
        "target_code_payload",
        "payload_json",
        "http_headers",
        "url",
        "auth",
        "retry",
    }
    assert forbidden_move_fields.isdisjoint(handling.calls[0]["moves"][0])


@pytest.mark.asyncio
async def test_preferred_exchange_can_fallback_to_sorting_after_reject(db_session: Any) -> None:
    handling = RecordingHandlingOperationService()
    service = RecordingHandoffService(handling_operation_service=handling)
    demand = await _create_demand(db_session, service, rack_release_id="release-preferred", usages=(0.65,))
    await service.evaluate(db_session, demand=demand, prefer_full_box_exchange=True)

    handled = await service.handle_exchange_callback(
        db_session,
        handling_operation_key="smt-inbound-handoff:release-preferred:full-box-exchange",
        callback_payload={
            "rack_release_id": "release-preferred",
            "exchange_status": "REJECTED",
            "reason_code": "WMS_REJECTED",
            "reason_message": "No full box available",
        },
        trace_id="trace-release-preferred-callback",
    )
    items = await _items_for_demand(db_session, handled)

    assert len(handling.calls) == 1
    assert handled.status == SmtInboundHandoffDemandStatus.READY_FOR_SORTING
    assert handled.failure_code is None
    assert handled.decision_status == "PREFERRED_FULL_BOX_EXCHANGE_FALLBACK_SORTING"
    assert [item.status for item in items] == [SmtInboundHandoffSourceItemStatus.READY]
    assert service.recalculate_reasons[-1] == "exchange_callback"


@pytest.mark.asyncio
async def test_business_completed_marks_exchanged_items_and_leaves_remaining_ready(db_session: Any) -> None:
    handling = RecordingHandlingOperationService()
    service = RecordingHandoffService(handling_operation_service=handling)
    demand = await _create_demand(db_session, service, rack_release_id="release-business", usages=(0.9, 0.9))
    await service.evaluate(db_session, demand=demand)
    before_items = await _items_for_demand(db_session, demand)

    handled = await service.handle_exchange_callback(
        db_session,
        handling_operation_key="smt-inbound-handoff:release-business:full-box-exchange",
        callback_payload={
            "rack_release_id": "release-business",
            "exchange_status": "BUSINESS_COMPLETED",
            "post_exchange_relations": [
                {
                    "item_key": before_items[0].item_key,
                    "exchange_result": "EXCHANGED",
                }
            ],
        },
        trace_id="trace-release-business-callback",
    )
    after_items = await _items_for_demand(db_session, handled)

    assert handled.status == SmtInboundHandoffDemandStatus.READY_FOR_SORTING
    assert handled.failure_code is None
    assert handled.decision_status == "FULL_BOX_EXCHANGED"
    assert [item.status for item in after_items] == [
        SmtInboundHandoffSourceItemStatus.EXCHANGED,
        SmtInboundHandoffSourceItemStatus.READY,
    ]


@pytest.mark.asyncio
async def test_physical_completed_without_relations_stays_reconciling(db_session: Any) -> None:
    service = RecordingHandoffService()
    demand = await _create_demand(db_session, service, rack_release_id="release-reconcile", usages=(0.9,))
    await service.evaluate(db_session, demand=demand)

    handled = await service.handle_exchange_callback(
        db_session,
        handling_operation_key="smt-inbound-handoff:release-reconcile:full-box-exchange",
        callback_payload={
            "rack_release_id": "release-reconcile",
            "exchange_status": "PHYSICAL_COMPLETED",
        },
        trace_id="trace-release-reconcile-callback",
    )

    assert handled.status == SmtInboundHandoffDemandStatus.RECONCILING
    assert handled.failure_code == "POST_EXCHANGE_RELATIONS_MISSING"
    assert handled.failure_message
    assert handled.decision_status == "RECONCILING"
    assert service.recalculate_reasons[-1] == "exchange_callback"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exchange_status", "expected_failure_code"),
    [
        ("FAILED", "WMS_RCS_REJECTED"),
        ("REJECTED", "WMS_RCS_REJECTED"),
        ("TIMEOUT", "WMS_RCS_TIMEOUT"),
    ],
)
async def test_required_exchange_failure_enters_manual_hold(
    db_session: Any,
    exchange_status: str,
    expected_failure_code: str,
) -> None:
    service = RecordingHandoffService()
    demand = await _create_demand(
        db_session, service, rack_release_id=f"release-{exchange_status.lower()}", usages=(0.9,)
    )
    await service.evaluate(db_session, demand=demand)

    handled = await service.handle_exchange_callback(
        db_session,
        handling_operation_key=f"smt-inbound-handoff:release-{exchange_status.lower()}:full-box-exchange",
        callback_payload={
            "rack_release_id": f"release-{exchange_status.lower()}",
            "exchange_status": exchange_status,
            "reason_message": f"{exchange_status} from WMS/RCS",
        },
        trace_id=f"trace-release-{exchange_status.lower()}-callback",
    )

    assert handled.status == SmtInboundHandoffDemandStatus.MANUAL_HOLD
    assert handled.failure_code == expected_failure_code
    assert handled.failure_message


@pytest.mark.asyncio
async def test_rack_release_id_mismatch_enters_manual_hold(db_session: Any) -> None:
    service = RecordingHandoffService()
    demand = await _create_demand(db_session, service, rack_release_id="release-mismatch", usages=(0.9,))
    await service.evaluate(db_session, demand=demand)

    handled = await service.handle_exchange_callback(
        db_session,
        handling_operation_key="smt-inbound-handoff:release-mismatch:full-box-exchange",
        callback_payload={
            "rack_release_id": "release-other",
            "exchange_status": "BUSINESS_COMPLETED",
            "post_exchange_relations": [{"item_key": "release-mismatch:BIN-A:BIN-A-1"}],
        },
        trace_id="trace-release-mismatch-callback",
    )

    assert handled.status == SmtInboundHandoffDemandStatus.MANUAL_HOLD
    assert handled.failure_code == "WMS_RCS_RACK_RELEASE_ID_MISMATCH"
    assert handled.failure_message
    assert service.recalculate_reasons[-1] == "exchange_callback"


@pytest.mark.asyncio
async def test_manual_reconcile_and_retry_exchange_call_demand_aggregation(db_session: Any) -> None:
    handling = RecordingHandlingOperationService()
    service = RecordingHandoffService(handling_operation_service=handling)
    demand = await _create_demand(db_session, service, rack_release_id="release-actions", usages=(0.9,))
    await service.evaluate(db_session, demand=demand)
    await service.handle_exchange_callback(
        db_session,
        handling_operation_key="smt-inbound-handoff:release-actions:full-box-exchange",
        callback_payload={
            "rack_release_id": "release-actions",
            "exchange_status": "PHYSICAL_COMPLETED",
        },
        trace_id="trace-release-actions-reconcile",
    )

    items = await _items_for_demand(db_session, demand)
    reconciled = await service.manual_reconcile_exchange(
        db_session,
        demand=demand,
        post_exchange_relations=[{"item_key": items[0].item_key, "exchange_result": "EXCHANGED"}],
        trace_id="trace-release-actions-manual",
    )
    assert reconciled.status == SmtInboundHandoffDemandStatus.COMPLETED

    await service.handle_exchange_callback(
        db_session,
        handling_operation_key="smt-inbound-handoff:release-actions:full-box-exchange",
        callback_payload={
            "rack_release_id": "release-actions",
            "exchange_status": "FAILED",
            "reason_message": "simulate retry path",
        },
        trace_id="trace-release-actions-failed",
    )
    retried = await service.retry_exchange(db_session, demand=demand, trace_id="trace-release-actions-retry")

    assert retried.status == SmtInboundHandoffDemandStatus.WAITING_FULL_BOX_EXCHANGE
    assert service.recalculate_reasons[-3:] == ["manual_reconcile_exchange", "exchange_callback", "retry_exchange"]
    assert handling.calls[-1]["operation_key"] == "smt-inbound-handoff:release-actions:full-box-exchange:retry"
