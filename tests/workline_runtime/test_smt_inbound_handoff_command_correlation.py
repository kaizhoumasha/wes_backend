"""SMT inbound handoff source-pick command correlation tests."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.workline.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.workline.services import write_back_service as workline_effects
from src.app.workline.services.smt_inbound_handoff_service import (
    SmtInboundHandoffService,
    smt_inbound_handoff_service,
)
from src.workline_plugins.smt_sorting_inbound.constants import COMMAND_SOURCE_PICK, ROLE_SORTING_SOURCE_ARM
from src.workline_runtime.orchestrator import OrchestratorResult
from src.workline_runtime.runtime_intent import RuntimeIntent
from src.workline_runtime.runtime_intent_effects import RuntimeIntentEffectApplier
from src.workline_runtime.trace_context import TraceContext


def _session(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": 123,
        "workline_id": 1,
        "status": "RUNNING",
        "context_json": {},
        "trace_id": None,
        "last_inbox_id": None,
        "plugin_key": "SMT_SORTING_INBOUND",
        "contract_version": "2026-06-01.p0",
        "current_wait_type": None,
        "waiting_since": None,
        "deadline_at": None,
        "current_wait_timeout_seconds": None,
        "awaiting_command_id": None,
        "ended_at": None,
        "failure_domain": None,
        "failure_code": None,
        "failure_message": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _ctx(*, session: Any | None = None, db: Any | None = None) -> dict[str, Any]:
    resolved_session = session or _session()
    return {
        "db": db or SimpleNamespace(add=MagicMock(), execute=AsyncMock()),
        "session": resolved_session,
        "workline": SimpleNamespace(id=1, plugin_key="SMT_SORTING_INBOUND", contract_version="2026-06-01.p0"),
        "inbox": SimpleNamespace(
            id=2101,
            trace_id="trace-handoff-1",
            payload_json={"canonical_event_type": "SORTING_SOURCE_PICK_REQUESTED"},
        ),
        "devices_by_role": {},
        "source_device": None,
        "orch_result": OrchestratorResult(success=True, intents=[]),
        "current_status": "RUNNING",
        "trace_id": "trace-handoff-1",
        "trace": TraceContext.from_runtime(session=resolved_session, trace_id="trace-handoff-1"),
        "session_ctx": dict(getattr(resolved_session, "context_json", {}) or {}),
        "now": datetime(2026, 1, 1, 0, 2, 0),
        "awaiting_command_id": None,
        "awaiting_command_code": None,
        "next_timeline_seq_no": None,
    }


class FakeHandoffRepository:
    def __init__(self, item: SmtInboundHandoffSourceItem) -> None:
        self.item = item

    async def get_source_item_for_update(self, _db: Any, source_item_id: int) -> SmtInboundHandoffSourceItem:
        assert source_item_id == self.item.id
        return self.item

    async def list_source_items(self, _db: Any, handoff_demand_id: int) -> list[SmtInboundHandoffSourceItem]:
        assert handoff_demand_id == self.item.handoff_demand_id
        return [self.item]


@pytest.mark.asyncio
async def test_handoff_service_records_source_pick_command_correlation() -> None:
    item = SmtInboundHandoffSourceItem(
        id=22,
        handoff_demand_id=11,
        item_key="11:A03",
        status=SmtInboundHandoffSourceItemStatus.PICK_REQUESTED,
        claim_attempt_no=2,
        source_pick_inbox_id=2101,
    )
    demand = SmtInboundHandoffDemand(
        id=11,
        demand_key="smt-inbound-handoff:release-001",
        rack_release_id="release-001",
        single_layer_rack_code="RACK-001",
        status=SmtInboundHandoffDemandStatus.READY_FOR_SORTING,
    )
    db = SimpleNamespace(add=MagicMock(), flush=AsyncMock(), get=AsyncMock(return_value=demand))
    service = SmtInboundHandoffService(repository=FakeHandoffRepository(item))

    result = await service.record_source_pick_command_correlation(
        db,
        handoff_demand_id=11,
        source_item_id=22,
        claim_attempt_no=2,
        source_pick_inbox_id=2101,
        command_id=88,
        command_code="CMD-SOURCE-PICK-001",
        dispatch_key="device-command:CMD-SOURCE-PICK-001",
        trace_id="trace-handoff-1",
    )

    assert result is item
    assert item.status == SmtInboundHandoffSourceItemStatus.CLAIMED_BY_SORTING
    assert item.source_pick_command_id == 88
    assert item.source_pick_command_code == "CMD-SOURCE-PICK-001"
    assert item.source_pick_dispatch_key == "device-command:CMD-SOURCE-PICK-001"
    assert demand.status == SmtInboundHandoffDemandStatus.CLAIMED_BY_SORTING
    assert db.add.call_count >= 2
    db.flush.assert_awaited()


@pytest.mark.asyncio
async def test_runtime_effect_writes_source_pick_command_correlation(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SimpleNamespace(id=1, device_code="SORT-SOURCE-ARM", device_role=ROLE_SORTING_SOURCE_ARM)
    created_command = SimpleNamespace(
        id=88,
        command_code="CMD-SOURCE-PICK-001",
        task_type=COMMAND_SOURCE_PICK,
        priority=5,
        timeout_ms=30000,
        params={},
    )
    correlation_calls: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session()
    ctx = _ctx(session=session, db=db)
    ctx["source_device"] = source
    ctx["devices_by_role"] = {ROLE_SORTING_SOURCE_ARM: [source]}

    async def fake_create(_repo: Any, _db: Any, _payload: dict[str, Any]) -> SimpleNamespace:
        return created_command

    async def record_correlation(_db: Any, **kwargs: Any) -> None:
        correlation_calls.append({"db": _db, **kwargs})

    monkeypatch.setattr(workline_effects, "_enforce_device_command_governance", lambda *_, **__: None)
    monkeypatch.setattr("src.app.device.repositories.command_repository.DeviceCommandRepository.create", fake_create)
    monkeypatch.setattr(workline_effects, "_emit_timeline", AsyncMock())
    monkeypatch.setattr(smt_inbound_handoff_service, "record_source_pick_command_correlation", record_correlation)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.command(
                device_role=ROLE_SORTING_SOURCE_ARM,
                action=COMMAND_SOURCE_PICK,
                payload={
                    "handoff_demand_id": 11,
                    "handoff_source_item_id": 22,
                    "claim_attempt_no": 2,
                    "source_pick_inbox_id": 2101,
                    "source_pick_request_event_id": "smt-inbound-handoff-source-item:22:claim:2",
                    "bin_code": "SRC-BIN-01",
                    "source_bin_code": "SRC-BIN-01",
                    "bin_cell_index": 3,
                    "source_cell_code": "A03",
                    "material_identity_key": "mid:pkg-001",
                    "pkg_code": "PKG-001",
                    "reel_thickness": "7.125",
                },
            )
        ],
    )

    assert correlation_calls == [
        {
            "db": db,
            "handoff_demand_id": 11,
            "source_item_id": 22,
            "claim_attempt_no": 2,
            "source_pick_inbox_id": 2101,
            "command_id": 88,
            "command_code": "CMD-SOURCE-PICK-001",
            "dispatch_key": "device-command:CMD-SOURCE-PICK-001",
            "trace_id": "trace-handoff-1",
        }
    ]
