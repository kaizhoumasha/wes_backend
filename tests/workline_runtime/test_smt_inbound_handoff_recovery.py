"""SMT inbound handoff claim 后恢复扫描测试。"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.app.device.models.command import CommandResult, CommandStatus
from src.app.workline.domain.services.smt_inbound_handoff_reason import SmtInboundHandoffReasonCode
from src.app.workline.domain.services.smt_inbound_handoff_route_service import SmtInboundHandoffRouteService
from src.app.workline.models.inbox import InboxStatus, WorklineInbox
from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.app.workline.models.session import WorklineSession
from src.app.workline.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.workline.repositories.smt_inbound_handoff_repository import SmtInboundHandoffRepository
from src.app.workline.services.smt_inbound_handoff_service import SmtInboundHandoffService
from src.workline_plugins.smt_sorting_inbound.constants import SMT_SORTING_INBOUND_PLUGIN_KEY


def _demand(
    status: SmtInboundHandoffDemandStatus = SmtInboundHandoffDemandStatus.CLAIMED_BY_SORTING,
) -> SmtInboundHandoffDemand:
    return SmtInboundHandoffDemand(
        id=11,
        demand_key="smt-inbound-handoff:release-001",
        rack_release_id="release-001",
        single_layer_rack_code="RACK-001",
        status=status,
    )


def _item(
    *,
    status: SmtInboundHandoffSourceItemStatus = SmtInboundHandoffSourceItemStatus.PICK_REQUESTED,
    command_id: int | None = None,
    item_id: int = 22,
) -> SmtInboundHandoffSourceItem:
    return SmtInboundHandoffSourceItem(
        id=item_id,
        handoff_demand_id=11,
        item_key=f"11:A{item_id:02d}",
        status=status,
        claim_attempt_no=1,
        source_pick_inbox_id=2101,
        source_pick_command_id=command_id,
        source_pick_command_code="CMD-SOURCE-PICK-001" if command_id is not None else None,
        source_pick_dispatch_key="device-command:CMD-SOURCE-PICK-001" if command_id is not None else None,
    )


class FakeRecoveryRepository:
    def __init__(
        self,
        *,
        demands: list[SmtInboundHandoffDemand] | None = None,
        items: list[SmtInboundHandoffSourceItem] | None = None,
    ) -> None:
        self.demands = demands or []
        self.items = items or []
        self.calls: list[tuple[str, int]] = []

    async def list_due_recovery_demands(self, _db: Any, *, now: datetime, limit: int) -> list[SmtInboundHandoffDemand]:
        _ = now
        self.calls.append(("list_due_recovery_demands", limit))
        return self.demands[:limit]

    async def list_stuck_source_items_for_recovery(
        self,
        _db: Any,
        *,
        now: datetime,
        limit: int,
        stale_after_seconds: int,
    ) -> list[SmtInboundHandoffSourceItem]:
        _ = now, stale_after_seconds
        self.calls.append(("list_stuck_source_items_for_recovery", limit))
        return self.items[:limit]

    async def list_source_items(self, _db: Any, handoff_demand_id: int) -> list[SmtInboundHandoffSourceItem]:
        return [item for item in self.items if item.handoff_demand_id == handoff_demand_id]

    async def get_source_item_for_update(
        self,
        _db: Any,
        source_item_id: int,
    ) -> SmtInboundHandoffSourceItem | None:
        return next((item for item in self.items if item.id == source_item_id), None)

    async def get_device_command_by_id(self, db: Any, command_id: int) -> Any | None:
        return await db.get(type("DeviceCommand", (), {}), command_id)


class FakeDb:
    def __init__(
        self, *, demand: SmtInboundHandoffDemand, inbox: Any | None = None, command: Any | None = None
    ) -> None:
        self.demand = demand
        self.inbox = inbox
        self.command = command
        self.added: list[Any] = []
        self.flush = AsyncMock()

    async def get(self, model: type[Any], pk: int) -> Any | None:
        model_name = model.__name__
        if model_name == "SmtInboundHandoffDemand" and pk == self.demand.id:
            return self.demand
        if model_name == "WorklineInbox" and self.inbox is not None and pk == self.inbox.id:
            return self.inbox
        if model_name == "DeviceCommand" and self.command is not None and pk == self.command.id:
            return self.command
        return None

    def add(self, value: Any) -> None:
        self.added.append(value)


class FakeReadyClaimRepository(FakeRecoveryRepository):
    def __init__(
        self,
        *,
        demand: SmtInboundHandoffDemand,
        ready_items: list[SmtInboundHandoffSourceItem],
        workline: Any | None = None,
    ) -> None:
        super().__init__(demands=[], items=ready_items)
        self.demand = demand
        self.workline = workline or SimpleNamespace(id=31, line_code="SMT-SORT-01")
        self.claimed_ids: list[int] = []

    async def list_ready_source_items_for_claim(
        self,
        _db: Any,
        *,
        now: datetime,
        limit: int,
    ) -> list[SmtInboundHandoffSourceItem]:
        _ = now
        self.calls.append(("list_ready_source_items_for_claim", limit))
        return [
            item
            for item in self.items
            if item.status == SmtInboundHandoffSourceItemStatus.READY
            and (item.next_attempt_at is None or item.next_attempt_at <= now)
        ][:limit]

    async def list_stuck_source_items_for_recovery(
        self,
        _db: Any,
        *,
        now: datetime,
        limit: int,
        stale_after_seconds: int,
    ) -> list[SmtInboundHandoffSourceItem]:
        _ = now, stale_after_seconds
        self.calls.append(("list_stuck_source_items_for_recovery", limit))
        return [
            item
            for item in self.items
            if item.status
            in {
                SmtInboundHandoffSourceItemStatus.PICK_REQUESTED,
                SmtInboundHandoffSourceItemStatus.CLAIMED_BY_SORTING,
            }
            and item.source_pick_inbox_id is not None
        ][:limit]

    async def list_sorting_candidate_worklines(self, _db: Any) -> list[Any]:
        return [self.workline]

    async def lock_source_item_by_id(self, _db: Any, *, source_item_id: int) -> SmtInboundHandoffSourceItem | None:
        return next((item for item in self.items if item.id == source_item_id), None)

    async def lock_target_workline_by_id(self, _db: Any, *, workline_id: int) -> Any | None:
        return SimpleNamespace(id=workline_id, line_code="SMT-SORT-01")

    async def list_open_sorting_sessions_with_current_material(
        self,
        _db: Any,
        *,
        workline_id: int,
        limit: int,
    ) -> list[Any]:
        _ = workline_id, limit
        return []

    async def list_in_flight_source_items_by_target_workline(
        self,
        _db: Any,
        *,
        target_workline_id: int,
        limit: int,
    ) -> list[SmtInboundHandoffSourceItem]:
        _ = target_workline_id, limit
        return [
            item
            for item in self.items
            if item.target_workline_id == target_workline_id
            and item.status
            in {
                SmtInboundHandoffSourceItemStatus.PICK_REQUESTED,
                SmtInboundHandoffSourceItemStatus.CLAIMED_BY_SORTING,
                SmtInboundHandoffSourceItemStatus.PICKED,
                SmtInboundHandoffSourceItemStatus.SORTING,
            }
        ]


class FakeClaimDb(FakeDb):
    def __init__(self, *, demand: SmtInboundHandoffDemand) -> None:
        super().__init__(demand=demand)
        self.next_session_id = 5000
        self.flush = AsyncMock(side_effect=self._flush)

    async def _flush(self) -> None:
        for value in self.added:
            if isinstance(value, SmtInboundHandoffSourceItem):
                continue
            if getattr(value, "id", None) is not None:
                continue
            if isinstance(value, WorklineSession):
                value.id = self.next_session_id
                self.next_session_id += 1


class FakeInboxService:
    def __init__(self) -> None:
        self.next_inbox_id = 6000

    async def create_internal_event_inbox(self, *_args: Any, **_kwargs: Any) -> Any:
        inbox = SimpleNamespace(id=self.next_inbox_id)
        self.next_inbox_id += 1
        return inbox


class ReadyClaimRouteService:
    def __init__(self) -> None:
        self.ecs_probe_calls = 0

    async def resolve_route(
        self,
        db: Any,
        *,
        demand: SmtInboundHandoffDemand,
        source_item: SmtInboundHandoffSourceItem,
        candidate_worklines: list[Any],
    ) -> Any:
        _ = db, demand, source_item
        self.ecs_probe_calls += 1
        workline = candidate_worklines[0]
        return SimpleNamespace(
            kind="SELECTED",
            selected_workline_id=workline.id,
            selected_workline_code=workline.line_code,
            route_evidence={
                "source_station_code": "SOURCE-STATION",
                "source_position_code": "SOURCE-STATION",
                "target_rack_position_code": "TARGET-STATION",
            },
        )


class AvailableStationLeaseService:
    async def get_station_lease_status(self, *_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(available=True)


class EmptySessionRepository:
    async def list_open_by_workline_id(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        return []


class CountingEcsProbe:
    def __init__(self) -> None:
        self.calls: list[tuple[int | None, str | None]] = []

    async def __call__(self, _db: Any, *, workline: Any, route: Any) -> Any:
        _ = route
        self.calls.append((getattr(workline, "id", None), getattr(workline, "line_code", None)))
        return SimpleNamespace(available=True)


def _sorting_workline() -> Any:
    return SimpleNamespace(
        id=31,
        line_code="SMT-SORT-01",
        plugin_key=SMT_SORTING_INBOUND_PLUGIN_KEY,
        is_active=True,
        runtime_status=WorkLineRuntimeStatus.READY,
        config={
            "smt_inbound_handoff_route": {
                "enabled": True,
                "priority": 1,
                "source_rack_position_code": "SOURCE_STATION_A",
            }
        },
    )


@pytest.mark.asyncio
async def test_retryable_source_pick_inbox_releases_item_for_retry() -> None:
    demand = _demand()
    item = _item()
    retry_at = datetime(2026, 6, 11, 10, 0, 0)
    inbox = SimpleNamespace(id=2101, status=InboxStatus.FAILED, next_retry_at=retry_at, error_message="plugin boom")
    repo = FakeRecoveryRepository(items=[item])
    db = FakeDb(demand=demand, inbox=inbox)

    summary = await SmtInboundHandoffService(repository=repo).scan_smt_inbound_handoff_demands_batch(
        db,
        scan_limit=10,
        recovery_limit=10,
        claim_limit=0,
    )

    assert item.status == SmtInboundHandoffSourceItemStatus.READY
    assert item.claim_attempt_no == 2
    assert item.source_pick_inbox_id is None
    assert item.next_attempt_at == retry_at
    assert demand.status == SmtInboundHandoffDemandStatus.READY_FOR_SORTING
    assert summary["retry_scheduled"] == 1


@pytest.mark.asyncio
async def test_dead_letter_source_pick_inbox_moves_item_and_demand_to_manual_hold() -> None:
    demand = _demand()
    item = _item()
    inbox = SimpleNamespace(id=2101, status=InboxStatus.DEAD_LETTER, error_message="retry exhausted")
    repo = FakeRecoveryRepository(items=[item])
    db = FakeDb(demand=demand, inbox=inbox)

    summary = await SmtInboundHandoffService(repository=repo).scan_smt_inbound_handoff_demands_batch(
        db,
        scan_limit=10,
        recovery_limit=10,
        claim_limit=0,
    )

    assert item.status == SmtInboundHandoffSourceItemStatus.MANUAL_HOLD
    assert item.failure_code == SmtInboundHandoffReasonCode.SOURCE_PICK_INBOX_DEAD_LETTER.value
    assert demand.status == SmtInboundHandoffDemandStatus.MANUAL_HOLD
    assert demand.failure_code == SmtInboundHandoffReasonCode.SOURCE_PICK_INBOX_DEAD_LETTER.value
    assert summary["manual_hold"] == 1


@pytest.mark.asyncio
async def test_processed_source_pick_inbox_without_command_enters_manual_hold() -> None:
    demand = _demand()
    item = _item()
    inbox = SimpleNamespace(id=2101, status=InboxStatus.PROCESSED, error_message=None)
    repo = FakeRecoveryRepository(items=[item])
    db = FakeDb(demand=demand, inbox=inbox)

    summary = await SmtInboundHandoffService(repository=repo).scan_smt_inbound_handoff_demands_batch(
        db,
        scan_limit=10,
        recovery_limit=10,
        claim_limit=0,
    )

    assert item.status == SmtInboundHandoffSourceItemStatus.MANUAL_HOLD
    assert item.failure_code == SmtInboundHandoffReasonCode.SOURCE_PICK_COMMAND_NOT_CREATED.value
    assert demand.failure_code == SmtInboundHandoffReasonCode.SOURCE_PICK_COMMAND_NOT_CREATED.value
    assert summary["manual_hold"] == 1


@pytest.mark.asyncio
async def test_processing_source_pick_inbox_is_observed_without_bypassing_token_fencing() -> None:
    demand = _demand()
    item = _item()
    inbox = SimpleNamespace(id=2101, status=InboxStatus.PROCESSING, processor_token="worker-1")
    repo = FakeRecoveryRepository(items=[item])
    db = FakeDb(demand=demand, inbox=inbox)

    summary = await SmtInboundHandoffService(repository=repo).scan_smt_inbound_handoff_demands_batch(
        db,
        scan_limit=10,
        recovery_limit=10,
        claim_limit=0,
    )

    assert item.status == SmtInboundHandoffSourceItemStatus.PICK_REQUESTED
    assert db.added == []
    assert summary["scanned"] == 1
    assert summary["advanced"] == 0
    assert summary["manual_hold"] == 0


@pytest.mark.asyncio
async def test_success_source_pick_command_repairs_claimed_item_to_picked() -> None:
    demand = _demand()
    item = _item(status=SmtInboundHandoffSourceItemStatus.CLAIMED_BY_SORTING, command_id=88)
    inbox = SimpleNamespace(id=2101, status=InboxStatus.PROCESSED, error_message=None)
    command = SimpleNamespace(id=88, status=CommandStatus.COMPLETED, result=CommandResult.SUCCESS)
    repo = FakeRecoveryRepository(items=[item])
    db = FakeDb(demand=demand, inbox=inbox, command=command)

    summary = await SmtInboundHandoffService(repository=repo).scan_smt_inbound_handoff_demands_batch(
        db,
        scan_limit=10,
        recovery_limit=10,
        claim_limit=0,
    )

    assert item.status == SmtInboundHandoffSourceItemStatus.PICKED
    assert demand.status == SmtInboundHandoffDemandStatus.SORTING_IN_PROGRESS
    assert summary["advanced"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_status",
    [
        SmtInboundHandoffSourceItemStatus.PICK_REQUESTED,
        SmtInboundHandoffSourceItemStatus.CLAIMED_BY_SORTING,
    ],
)
async def test_record_source_pick_success_advances_requested_or_claimed_item_to_picked(
    source_status: SmtInboundHandoffSourceItemStatus,
) -> None:
    demand = _demand()
    demand.failure_code = "OLD_FAILURE"
    demand.failure_message = "old failure"
    item = _item(status=source_status, command_id=88)
    item.failure_code = "OLD_ITEM_FAILURE"
    item.failure_message = "old item failure"
    item.next_attempt_at = datetime(2026, 6, 11, 10, 5, 0)
    repo = FakeRecoveryRepository(items=[item])
    db = FakeDb(demand=demand)

    result = await SmtInboundHandoffService(repository=repo).record_source_pick_success(
        db,
        handoff_demand_id=demand.id,
        source_item_id=item.id,
        claim_attempt_no=item.claim_attempt_no,
        source_pick_inbox_id=item.source_pick_inbox_id,
        command_id=item.source_pick_command_id,
        trace_id="trace-source-pick-success",
    )

    assert result.outcome == "advanced"
    assert result.advanced is True
    assert result.already_terminal is False
    assert result.source_item is item
    assert item.status == SmtInboundHandoffSourceItemStatus.PICKED
    assert item.failure_code is None
    assert item.failure_message is None
    assert item.next_attempt_at is None
    assert demand.failure_code is None
    assert demand.failure_message is None
    assert demand.status == SmtInboundHandoffDemandStatus.SORTING_IN_PROGRESS


@pytest.mark.asyncio
async def test_record_source_pick_success_resolves_item_by_command_correlation() -> None:
    demand = _demand()
    item = _item(status=SmtInboundHandoffSourceItemStatus.CLAIMED_BY_SORTING, command_id=88)
    repo = FakeRecoveryRepository(items=[item])
    db = FakeDb(
        demand=demand,
        command=SimpleNamespace(
            id=88,
            params={
                "handoff_demand_id": demand.id,
                "handoff_source_item_id": item.id,
                "claim_attempt_no": item.claim_attempt_no,
                "source_pick_inbox_id": item.source_pick_inbox_id,
            },
        ),
    )

    result = await SmtInboundHandoffService(repository=repo).record_source_pick_success(
        db,
        command_id=88,
        trace_id="trace-source-pick-success",
    )

    assert result.outcome == "advanced"
    assert result.source_item is item
    assert item.status == SmtInboundHandoffSourceItemStatus.PICKED
    assert demand.status == SmtInboundHandoffDemandStatus.SORTING_IN_PROGRESS


@pytest.mark.asyncio
async def test_record_source_pick_success_rejects_mismatched_command_source_item_evidence() -> None:
    demand = _demand()
    item = _item(status=SmtInboundHandoffSourceItemStatus.CLAIMED_BY_SORTING, command_id=88)
    repo = FakeRecoveryRepository(items=[item])
    db = FakeDb(
        demand=demand,
        command=SimpleNamespace(
            id=88,
            params={
                "handoff_demand_id": demand.id,
                "handoff_source_item_id": 999,
                "claim_attempt_no": item.claim_attempt_no,
                "source_pick_inbox_id": item.source_pick_inbox_id,
            },
        ),
    )

    with pytest.raises(ValueError, match="source_item_id"):
        await SmtInboundHandoffService(repository=repo).record_source_pick_success(
            db,
            source_item_id=item.id,
            command_id=88,
            trace_id="trace-source-pick-success",
        )

    assert item.status == SmtInboundHandoffSourceItemStatus.CLAIMED_BY_SORTING


@pytest.mark.asyncio
async def test_success_source_pick_command_on_already_picked_item_is_noop_without_recounting_advanced() -> None:
    demand = _demand(SmtInboundHandoffDemandStatus.SORTING_IN_PROGRESS)
    item = _item(status=SmtInboundHandoffSourceItemStatus.PICKED, command_id=88)
    inbox = SimpleNamespace(id=2101, status=InboxStatus.PROCESSED, error_message=None)
    command = SimpleNamespace(id=88, status=CommandStatus.COMPLETED, result=CommandResult.SUCCESS)
    repo = FakeRecoveryRepository(items=[item])
    db = FakeDb(demand=demand, inbox=inbox, command=command)

    summary = await SmtInboundHandoffService(repository=repo).scan_smt_inbound_handoff_demands_batch(
        db,
        scan_limit=10,
        recovery_limit=10,
        claim_limit=0,
    )

    assert item.status == SmtInboundHandoffSourceItemStatus.PICKED
    assert demand.status == SmtInboundHandoffDemandStatus.SORTING_IN_PROGRESS
    assert summary["advanced"] == 0
    assert summary["recovery_errors"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_status",
    [
        SmtInboundHandoffSourceItemStatus.SORTED,
        SmtInboundHandoffSourceItemStatus.SKIPPED,
        SmtInboundHandoffSourceItemStatus.EXCHANGED,
    ],
)
async def test_late_source_pick_success_on_terminal_item_is_noop(
    terminal_status: SmtInboundHandoffSourceItemStatus,
) -> None:
    demand = _demand(SmtInboundHandoffDemandStatus.COMPLETED)
    item = _item(status=terminal_status, command_id=88)
    inbox = SimpleNamespace(id=2101, status=InboxStatus.PROCESSED, error_message=None)
    command = SimpleNamespace(id=88, status=CommandStatus.COMPLETED, result=CommandResult.SUCCESS)
    repo = FakeRecoveryRepository(items=[item])
    db = FakeDb(demand=demand, inbox=inbox, command=command)

    summary = await SmtInboundHandoffService(repository=repo).scan_smt_inbound_handoff_demands_batch(
        db,
        scan_limit=10,
        recovery_limit=10,
        claim_limit=0,
    )

    assert item.status == terminal_status
    assert demand.status == SmtInboundHandoffDemandStatus.COMPLETED
    assert summary["advanced"] == 0
    assert summary["recovery_errors"] == 0


@pytest.mark.asyncio
async def test_late_source_pick_success_on_manual_hold_item_keeps_controlled_hold() -> None:
    demand = _demand(SmtInboundHandoffDemandStatus.MANUAL_HOLD)
    demand.failure_code = SmtInboundHandoffReasonCode.SOURCE_PICK_INBOX_DEAD_LETTER.value
    demand.failure_message = "retry exhausted"
    item = _item(status=SmtInboundHandoffSourceItemStatus.MANUAL_HOLD, command_id=88)
    item.failure_code = SmtInboundHandoffReasonCode.SOURCE_PICK_INBOX_DEAD_LETTER.value
    item.failure_message = "retry exhausted"
    inbox = SimpleNamespace(id=2101, status=InboxStatus.PROCESSED, error_message=None)
    command = SimpleNamespace(id=88, status=CommandStatus.COMPLETED, result=CommandResult.SUCCESS)
    repo = FakeRecoveryRepository(items=[item])
    db = FakeDb(demand=demand, inbox=inbox, command=command)

    summary = await SmtInboundHandoffService(repository=repo).scan_smt_inbound_handoff_demands_batch(
        db,
        scan_limit=10,
        recovery_limit=10,
        claim_limit=0,
    )

    assert item.status == SmtInboundHandoffSourceItemStatus.MANUAL_HOLD
    assert item.failure_code == SmtInboundHandoffReasonCode.SOURCE_PICK_INBOX_DEAD_LETTER.value
    assert demand.status == SmtInboundHandoffDemandStatus.MANUAL_HOLD
    assert demand.failure_code == SmtInboundHandoffReasonCode.SOURCE_PICK_INBOX_DEAD_LETTER.value
    assert summary["manual_hold"] == 1
    assert summary["advanced"] == 0
    assert summary["recovery_errors"] == 0


def test_stuck_source_item_recovery_statement_uses_post_claim_hot_path() -> None:
    statement = SmtInboundHandoffRepository().build_stuck_source_item_recovery_statement(
        now=datetime(2026, 6, 11, 10, 0, 0),
        stale_after_seconds=300,
        limit=25,
    )

    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))

    assert "source_pick_inbox_id IS NOT NULL" in sql
    assert "PICK_REQUESTED" in sql
    assert "CLAIMED_BY_SORTING" in sql
    assert "ORDER BY" in sql
    assert "updated_at" in sql
    assert "LIMIT 25" in sql


@pytest.mark.asyncio
async def test_recovery_scan_reads_due_demands_and_stuck_source_items_with_batch_limit() -> None:
    demand = _demand(SmtInboundHandoffDemandStatus.READY_FOR_SORTING)
    repo = FakeRecoveryRepository(demands=[demand], items=[])
    db = FakeDb(demand=demand)

    summary = await SmtInboundHandoffService(repository=repo).scan_smt_inbound_handoff_demands_batch(
        db,
        scan_limit=7,
        recovery_limit=7,
        claim_limit=0,
    )

    assert repo.calls == [
        ("list_due_recovery_demands", 7),
        ("list_stuck_source_items_for_recovery", 7),
    ]
    assert summary == {
        "scanned": 1,
        "claimed": 0,
        "advanced": 0,
        "retry_scheduled": 0,
        "manual_hold": 0,
        "recovery_errors": 0,
    }


@pytest.mark.asyncio
async def test_recovery_scan_claims_due_ready_item_with_separate_claim_limit() -> None:
    demand = _demand(SmtInboundHandoffDemandStatus.READY_FOR_SORTING)
    ready_item = _item(status=SmtInboundHandoffSourceItemStatus.READY)
    ready_item.source_pick_inbox_id = None
    repo = FakeReadyClaimRepository(demand=demand, ready_items=[ready_item])
    db = FakeClaimDb(demand=demand)

    summary = await SmtInboundHandoffService(
        repository=repo,
        route_service=ReadyClaimRouteService(),
        inbox_service=FakeInboxService(),
    ).scan_smt_inbound_handoff_demands_batch(
        db,
        scan_limit=0,
        recovery_limit=0,
        claim_limit=1,
    )

    assert summary == {
        "scanned": 0,
        "claimed": 1,
        "advanced": 0,
        "retry_scheduled": 0,
        "manual_hold": 0,
        "recovery_errors": 0,
    }
    assert ready_item.status == SmtInboundHandoffSourceItemStatus.PICK_REQUESTED
    assert repo.calls == [("list_ready_source_items_for_claim", 1)]


@pytest.mark.asyncio
async def test_recovery_scan_uses_separate_scan_recovery_and_claim_limits() -> None:
    demand = _demand(SmtInboundHandoffDemandStatus.READY_FOR_SORTING)
    ready_items = [
        _item(status=SmtInboundHandoffSourceItemStatus.READY, item_id=22),
        _item(status=SmtInboundHandoffSourceItemStatus.READY, item_id=23),
        _item(status=SmtInboundHandoffSourceItemStatus.READY, item_id=24),
        _item(status=SmtInboundHandoffSourceItemStatus.READY, item_id=25),
    ]
    for item in ready_items:
        item.source_pick_inbox_id = None
    stuck_item = _item(status=SmtInboundHandoffSourceItemStatus.PICK_REQUESTED, item_id=23)
    repo = FakeReadyClaimRepository(demand=demand, ready_items=[*ready_items, stuck_item])
    repo.demands = [demand]
    db = FakeClaimDb(demand=demand)

    await SmtInboundHandoffService(
        repository=repo,
        route_service=ReadyClaimRouteService(),
        inbox_service=FakeInboxService(),
    ).scan_smt_inbound_handoff_demands_batch(
        db,
        scan_limit=2,
        recovery_limit=3,
        claim_limit=4,
    )

    assert repo.calls == [
        ("list_due_recovery_demands", 2),
        ("list_stuck_source_items_for_recovery", 3),
        ("list_ready_source_items_for_claim", 1),
        ("list_ready_source_items_for_claim", 1),
        ("list_ready_source_items_for_claim", 1),
        ("list_ready_source_items_for_claim", 1),
    ]


@pytest.mark.asyncio
async def test_recovery_scan_claim_limit_zero_keeps_old_recovery_behavior() -> None:
    demand = _demand(SmtInboundHandoffDemandStatus.READY_FOR_SORTING)
    ready_item = _item(status=SmtInboundHandoffSourceItemStatus.READY)
    ready_item.source_pick_inbox_id = None
    repo = FakeReadyClaimRepository(demand=demand, ready_items=[ready_item])
    db = FakeClaimDb(demand=demand)

    summary = await SmtInboundHandoffService(
        repository=repo,
        route_service=ReadyClaimRouteService(),
        inbox_service=FakeInboxService(),
    ).scan_smt_inbound_handoff_demands_batch(
        db,
        scan_limit=0,
        recovery_limit=0,
        claim_limit=0,
    )

    assert summary == {
        "scanned": 0,
        "claimed": 0,
        "advanced": 0,
        "retry_scheduled": 0,
        "manual_hold": 0,
        "recovery_errors": 0,
    }
    assert ready_item.status == SmtInboundHandoffSourceItemStatus.READY
    assert repo.calls == []


@pytest.mark.asyncio
async def test_recovery_scan_deduplicates_ecs_probe_by_target_workline_during_same_scan() -> None:
    demand = _demand(SmtInboundHandoffDemandStatus.READY_FOR_SORTING)
    ready_items = [
        _item(status=SmtInboundHandoffSourceItemStatus.READY, item_id=22),
        _item(status=SmtInboundHandoffSourceItemStatus.READY, item_id=23),
    ]
    for item in ready_items:
        item.source_pick_inbox_id = None
    workline = _sorting_workline()
    repo = FakeReadyClaimRepository(demand=demand, ready_items=ready_items, workline=workline)
    db = FakeClaimDb(demand=demand)
    ecs_probe = CountingEcsProbe()

    summary = await SmtInboundHandoffService(
        repository=repo,
        route_service=SmtInboundHandoffRouteService(
            station_lease_service=AvailableStationLeaseService(),
            session_repository=EmptySessionRepository(),
            ecs_status_probe=ecs_probe,
        ),
        inbox_service=FakeInboxService(),
    ).scan_smt_inbound_handoff_demands_batch(
        db,
        scan_limit=0,
        recovery_limit=0,
        claim_limit=2,
    )

    assert summary["claimed"] == 1
    assert ecs_probe.calls == [(workline.id, workline.line_code)]
