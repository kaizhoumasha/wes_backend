"""SMT inbound handoff API 查询投影服务测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.app.device.models.command import CommandStatus
from src.app.sys.models.outbox import SystemOutboxStatus
from src.app.workline.domain.services.smt_inbound_handoff_reason import SmtInboundHandoffReasonCode
from src.app.workline.models.inbox import InboxStatus
from src.app.workline.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.workline.services.smt_inbound_handoff_service import SmtInboundHandoffService


def _demand(
    status: SmtInboundHandoffDemandStatus = SmtInboundHandoffDemandStatus.MANUAL_HOLD,
) -> SmtInboundHandoffDemand:
    return SmtInboundHandoffDemand(
        id=11,
        demand_key="smt-inbound-handoff:release-001",
        rack_release_id="release-001",
        single_layer_rack_code="RACK-001",
        status=status,
        bin_snapshots_json={"bins": [{"bin_code": "BIN-A"}]},
    )


def _dead_letter_item() -> SmtInboundHandoffSourceItem:
    return SmtInboundHandoffSourceItem(
        id=22,
        handoff_demand_id=11,
        item_key="release-001:BIN-A:A01",
        bin_code="BIN-A",
        bin_cell_code="A01",
        status=SmtInboundHandoffSourceItemStatus.MANUAL_HOLD,
        claim_attempt_no=1,
        source_pick_inbox_id=2101,
        source_pick_command_id=88,
        source_pick_command_code="CMD-SOURCE-PICK-001",
        source_pick_dispatch_key="device-command:CMD-SOURCE-PICK-001",
        failure_code=SmtInboundHandoffReasonCode.SOURCE_PICK_INBOX_DEAD_LETTER.value,
        failure_message="source-pick inbox dead-letter",
    )


class FakeProjectionRepository:
    def __init__(self, *, demand: SmtInboundHandoffDemand, item: SmtInboundHandoffSourceItem) -> None:
        self.demand = demand
        self.item = item
        self.inbox = SimpleNamespace(
            id=2101,
            status=InboxStatus.DEAD_LETTER,
            event_id="smt-inbound-handoff-source-item:22:claim:1",
            attempt_count=3,
            max_attempts=3,
            next_retry_at=None,
            processed_at=None,
            error_message="plugin boom",
        )
        self.command = SimpleNamespace(
            id=88,
            command_code="CMD-SOURCE-PICK-001",
            status=CommandStatus.PENDING,
            result=None,
            result_data=None,
            error_detail=None,
            sent_at=None,
            ack_received_at=None,
            completed_at=None,
        )
        self.outbox = SimpleNamespace(
            id=98,
            dispatch_key="device-command:CMD-SOURCE-PICK-001",
            status=SystemOutboxStatus.NEW,
            attempt_count=0,
            next_retry_at=None,
            last_error=None,
            sent_at=None,
            finished_at=None,
        )

    async def list_demands_for_api(
        self,
        _db: Any,
        *,
        limit: int,
        offset: int,
        status: str | None,
    ) -> list[SmtInboundHandoffDemand]:
        _ = limit, offset, status
        return [self.demand]

    async def count_demands_for_api(self, _db: Any, *, status: str | None) -> int:
        _ = status
        return 1

    async def list_source_items(self, _db: Any, handoff_demand_id: int) -> list[SmtInboundHandoffSourceItem]:
        return [self.item] if handoff_demand_id == self.demand.id else []

    async def get_by_id(self, _db: Any, demand_id: int) -> SmtInboundHandoffDemand | None:
        return self.demand if demand_id == self.demand.id else None

    async def get_source_item_by_id(self, _db: Any, source_item_id: int) -> SmtInboundHandoffSourceItem | None:
        return self.item if source_item_id == self.item.id else None

    async def get_workline_inbox_by_id(self, _db: Any, inbox_id: int) -> Any | None:
        return self.inbox if inbox_id == self.inbox.id else None

    async def get_device_command_by_id(self, _db: Any, command_id: int) -> Any | None:
        return self.command if command_id == self.command.id else None

    async def get_outbox_by_dispatch_key(self, _db: Any, dispatch_key: str) -> Any | None:
        return self.outbox if dispatch_key == self.outbox.dispatch_key else None


class FakeProjectionDb:
    def __init__(self, demand: SmtInboundHandoffDemand) -> None:
        self.demand = demand
        self.added: list[Any] = []
        self.flush = AsyncMock()

    async def get(self, model: type[Any], pk: int) -> Any | None:
        if model.__name__ == "SmtInboundHandoffDemand" and pk == self.demand.id:
            return self.demand
        return None

    def add(self, value: Any) -> None:
        self.added.append(value)


@pytest.mark.asyncio
async def test_demand_summary_aggregates_retry_source_pick_actions_from_source_items() -> None:
    demand = _demand()
    item = _dead_letter_item()
    service = SmtInboundHandoffService(repository=FakeProjectionRepository(demand=demand, item=item))

    result = await service.list_handoff_demand_summaries(object(), limit=20, offset=0, status=None)

    summary = result["items"][0]
    assert summary["item_status_counts"] == {"MANUAL_HOLD": 1}
    assert summary["claim_recovery_summary"] == {"dead_letter": 1, "manual_hold": 1}
    assert summary["available_actions"] == ["RETRY_SOURCE_PICK", "RELEASE_HOLD"]


@pytest.mark.asyncio
async def test_demand_detail_exposes_source_pick_evidence() -> None:
    demand = _demand()
    item = _dead_letter_item()
    service = SmtInboundHandoffService(repository=FakeProjectionRepository(demand=demand, item=item))

    detail = await service.get_handoff_demand_detail(object(), demand_id=11)

    assert detail is not None
    assert detail["release_snapshot"] == {"bins": [{"bin_code": "BIN-A"}]}
    source_item = detail["source_items"][0]
    assert source_item["source_pick_inbox"]["status"] == "DEAD_LETTER"
    assert source_item["source_pick_command"]["command_code"] == "CMD-SOURCE-PICK-001"
    assert source_item["source_pick_outbox"]["dispatch_key"] == "device-command:CMD-SOURCE-PICK-001"


@pytest.mark.asyncio
async def test_retry_source_pick_action_releases_dead_letter_item_for_next_claim() -> None:
    demand = _demand()
    item = _dead_letter_item()
    repo = FakeProjectionRepository(demand=demand, item=item)
    db = FakeProjectionDb(demand)
    service = SmtInboundHandoffService(repository=repo)

    result = await service.retry_source_pick_action(db, source_item_id=22)

    assert result == {"id": 22, "status": "READY", "available_actions": []}
    assert item.claim_attempt_no == 2
    assert item.source_pick_inbox_id is None
    assert item.source_pick_command_id is None
