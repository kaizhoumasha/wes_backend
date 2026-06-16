"""粗分机 release fact producer 接入测试。"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.app.workline.services.single_layer_rack_orchestration_service import (
    SingleLayerRackOrchestrationDecisionCode,
    SingleLayerRackOrchestrationService,
)
from src.app.workline.services.station_lease_service import StationLeaseResult


@dataclass
class FakeStationLeaseService:
    status: StationLeaseResult

    def __post_init__(self) -> None:
        self.status_calls: list[dict[str, Any]] = []
        self.claim_calls: list[dict[str, Any]] = []

    async def get_station_lease_status(self, *_args: Any, **kwargs: Any) -> StationLeaseResult:
        self.status_calls.append(kwargs)
        return self.status

    async def claim_station_dispatch_lease(self, *_args: Any, **kwargs: Any) -> None:
        self.claim_calls.append(kwargs)


class FakeHandoffService:
    def __init__(self) -> None:
        self.release_calls: list[dict[str, Any]] = []
        self.evaluate_calls: list[dict[str, Any]] = []
        self.claim_calls: list[dict[str, Any]] = []
        self.result = SimpleNamespace(id=501, demand_key="smt-inbound-handoff:release-001", status="READY_FOR_SORTING")
        self.claim_result = SimpleNamespace(
            kind="CLAIMED",
            failure_code=None,
            failure_message=None,
            next_attempt_at=None,
            source_item=SimpleNamespace(id=601),
            session=SimpleNamespace(id=701),
            inbox=SimpleNamespace(id=801),
        )

    async def create_or_get_from_release(self, db: Any, **payload: Any) -> Any:
        self.release_calls.append({"db": db, "payload": payload})
        return self.result

    async def evaluate(self, db: Any, **payload: Any) -> Any:
        self.evaluate_calls.append({"db": db, "payload": payload})
        return self.result

    async def claim_next_source_item(self, db: Any, **payload: Any) -> Any:
        self.claim_calls.append({"db": db, "payload": payload})
        return self.claim_result


def ready_workline() -> SimpleNamespace:
    return SimpleNamespace(id=1001, line_code="WL-SMT-ROUGH-01", runtime_status=WorkLineRuntimeStatus.READY)


def available_status() -> StationLeaseResult:
    return StationLeaseResult(workline_code="WL-SMT-ROUGH-01", position_code="SINGLE_LAYER_A", available=True)


def release_fact_payload() -> dict[str, Any]:
    return {
        "rack_release_id": "release-001",
        "single_layer_rack_code": "RACK-001",
        "release_reason_code": "NO_COMPATIBLE_OR_EMPTY_CELL",
        "bin_snapshots": [{"slot_code": "A", "bin_code": "BIN-A", "usage": 0.25}],
        "trace_id": "trace-release-001",
    }


@pytest.mark.asyncio
async def test_rough_sorter_release_fact_invokes_handoff_release_producer_once() -> None:
    handoff = FakeHandoffService()
    lease = FakeStationLeaseService(status=available_status())
    orchestrator = SingleLayerRackOrchestrationService(
        station_lease_service=lease,
        smt_inbound_handoff_service=handoff,
    )
    db = object()

    decision = await orchestrator.plan_single_layer_rack_dispatch(
        db,
        business_demand_key="ROUGH-SORTER-RELEASE-001",
        demand_type="ROUGH_SORTER_RELEASE_FACT",
        workline=ready_workline(),
        station_code="SINGLE_LAYER_A",
        fact_payload=release_fact_payload(),
    )

    assert decision.decision == SingleLayerRackOrchestrationDecisionCode.WAITING
    assert decision.reason == "ROUGH_SORTER_RELEASE_FACT_RECORDED"
    assert decision.diagnostics["handoff_demand_id"] == 501
    assert decision.diagnostics["handoff_claim_result"] == "CLAIMED"
    assert decision.diagnostics["handoff_claim_source_item_id"] == 601
    assert decision.diagnostics["handoff_claim_session_id"] == 701
    assert decision.diagnostics["handoff_claim_inbox_id"] == 801
    assert decision.fact_payload is not None
    assert decision.fact_payload["business_demand_key"] == "ROUGH-SORTER-RELEASE-001"
    assert handoff.release_calls == [
        {
            "db": db,
            "payload": {
                "rack_release_id": "release-001",
                "single_layer_rack_code": "RACK-001",
                "source_workline_id": 1001,
                "source_workline_code": "WL-SMT-ROUGH-01",
                "release_reason_code": "NO_COMPATIBLE_OR_EMPTY_CELL",
                "bin_snapshots": [{"slot_code": "A", "bin_code": "BIN-A", "usage": 0.25}],
                "trace_id": "trace-release-001",
                "business_demand_key": "ROUGH-SORTER-RELEASE-001",
                "station_code": "SINGLE_LAYER_A",
            },
        }
    ]
    assert handoff.evaluate_calls == [
        {
            "db": db,
            "payload": {
                "demand": handoff.result,
                "prefer_full_box_exchange": False,
                "trace_id": "trace-release-001",
            },
        }
    ]
    assert handoff.claim_calls == [
        {
            "db": db,
            "payload": {
                "trace_id": "trace-release-001",
                "demand_id": 501,
            },
        }
    ]
    assert lease.status_calls == []
    assert lease.claim_calls == []


@pytest.mark.asyncio
async def test_non_release_demand_does_not_invoke_handoff_release_producer() -> None:
    handoff = FakeHandoffService()
    lease = FakeStationLeaseService(status=available_status())
    orchestrator = SingleLayerRackOrchestrationService(
        station_lease_service=lease,
        smt_inbound_handoff_service=handoff,
    )

    decision = await orchestrator.plan_single_layer_rack_dispatch(
        object(),
        business_demand_key=None,
        demand_type=None,
        workline=ready_workline(),
        station_code="SINGLE_LAYER_A",
        rack_snapshot_ref="snapshot:ready-rack",
    )

    assert decision.decision == SingleLayerRackOrchestrationDecisionCode.WAITING
    assert decision.reason == "BUSINESS_DEMAND_REQUIRED"
    assert handoff.release_calls == []
    assert handoff.evaluate_calls == []
    assert handoff.claim_calls == []
