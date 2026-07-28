"""SMT source-pick 聚合公共入口锁序合同。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.app.runtime.orchestration.models.smt_inbound_handoff import (
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import SmtInboundHandoffService
from src.utils.timezone import timezone


class _LockProtocolRepository:
    def __init__(self, *, item: SimpleNamespace, demand: SimpleNamespace) -> None:
        self.item = item
        self.demand = demand
        self.events: list[str] = []

    async def get_source_item_by_id(self, _db: object, _source_item_id: int) -> object:
        self.events.append("plain_item_read")
        raise AssertionError("人工 retry 禁止普通读改 source item")

    async def get_source_item_for_update(self, _db: object, _source_item_id: int) -> SimpleNamespace:
        self.events.append("item")
        return self.item

    async def lock_source_item_by_id(self, _db: object, *, source_item_id: int) -> SimpleNamespace:
        assert source_item_id == self.item.id
        self.events.append("item")
        return self.item

    async def lock_demand_by_id(self, _db: object, *, demand_id: int) -> SimpleNamespace:
        assert demand_id == self.demand.id
        self.events.append("demand")
        return self.demand


class _LockProtocolService(SmtInboundHandoffService):
    async def recalculate_demand_status(self, _db: object, demand: object, *, reason: str | None = None) -> object:
        _ = reason
        return demand


class _DB:
    def add(self, _value: object) -> None:
        pass


def _held_item() -> SimpleNamespace:
    return SimpleNamespace(
        id=12,
        handoff_demand_id=11,
        claim_attempt_no=3,
        status=SmtInboundHandoffSourceItemStatus.MANUAL_HOLD,
        failure_code="SOURCE_PICK_COMMAND_NOT_CREATED",
        failure_message="需要人工重试",
        source_pick_inbox_id=31,
        source_pick_command_id=41,
        source_pick_command_code="CMD-41",
        source_pick_dispatch_key="device-command:CMD-41",
        sorting_session_id=21,
        target_workline_id=7,
        target_workline_code="SMT",
        claimed_at=timezone.now_for_db(),
        next_attempt_at=None,
    )


def _demand() -> SimpleNamespace:
    return SimpleNamespace(
        id=11,
        status=SmtInboundHandoffDemandStatus.MANUAL_HOLD,
        failure_code="SOURCE_PICK_COMMAND_NOT_CREATED",
        failure_message="需要人工重试",
    )


@pytest.mark.asyncio
async def test_claim_final_recheck_locks_item_before_parent_demand() -> None:
    item = _held_item()
    item.status = SmtInboundHandoffSourceItemStatus.READY
    demand = _demand()
    demand.status = SmtInboundHandoffDemandStatus.READY_FOR_SORTING
    repository = _LockProtocolRepository(item=item, demand=demand)
    service = _LockProtocolService(repository=repository)  # type: ignore[arg-type]

    locked_demand, locked_item, blocked = await service._lock_claimable_demand_and_ready_candidate_or_retry(
        object(),  # type: ignore[arg-type]
        candidate=item,
        now=timezone.now_for_db(),
    )

    assert blocked is None
    assert locked_item is item
    assert locked_demand is demand
    assert repository.events == ["item", "demand"]


@pytest.mark.asyncio
async def test_manual_retry_public_entry_locks_item_then_parent_and_fences_attempt() -> None:
    item = _held_item()
    demand = _demand()
    repository = _LockProtocolRepository(item=item, demand=demand)
    service = _LockProtocolService(repository=repository)  # type: ignore[arg-type]

    result = await service.retry_source_pick_action(_DB(), source_item_id=item.id)  # type: ignore[arg-type]

    assert repository.events == ["item", "demand"]
    assert result["status"] == SmtInboundHandoffSourceItemStatus.READY.value
    assert item.claim_attempt_no == 4
    assert item.source_pick_command_id is None
