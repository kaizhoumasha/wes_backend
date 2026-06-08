from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.resource.models import RackKind, RackPlacement
from src.app.sys.models.outbox import (
    DispatchEnvelope,
    SystemOutbox,
    SystemOutboxDispatchType,
    SystemOutboxStatus,
    SystemOutboxTargetType,
)
from src.app.workline.models.session import SessionStatus, WorklineSession
from src.app.workline.services.station_lease_service import (
    StationLeaseReasonCode,
    WorklineStationLeaseService,
)
from src.utils.timezone import timezone


@dataclass
class StationLeaseHarness:
    service: WorklineStationLeaseService
    db: FakeDb
    rack_position_service: FakeRackPositionService
    rack_placement_repo: FakeRackPlacementRepository
    outbox_repo: FakeSystemOutboxRepository
    session_repo: FakeWorklineSessionRepository


class FakeRackPositionService:
    def __init__(self, *, rack_kind: RackKind = RackKind.SINGLE_LAYER) -> None:
        self.rack_kind = rack_kind
        self.calls: list[tuple[str, str, RackKind]] = []
        self.locked_calls: list[tuple[str, str, RackKind]] = []

    async def require_enabled_position(
        self,
        _db: object,
        *,
        workline_code: str,
        position_code: str,
        rack_kind: RackKind,
    ) -> SimpleNamespace:
        self.calls.append((workline_code, position_code, rack_kind))
        if self.rack_kind != rack_kind:
            raise ValueError(f"allowed rack kind mismatch: expected {self.rack_kind}, got {rack_kind}")
        return self._position(workline_code=workline_code, position_code=position_code)

    async def require_enabled_position_for_update(
        self,
        _db: object,
        *,
        workline_code: str,
        position_code: str,
        rack_kind: RackKind,
    ) -> SimpleNamespace:
        self.locked_calls.append((workline_code, position_code, rack_kind))
        if self.rack_kind != rack_kind:
            raise ValueError(f"allowed rack kind mismatch: expected {self.rack_kind}, got {rack_kind}")
        return self._position(workline_code=workline_code, position_code=position_code)

    @staticmethod
    def _position(*, workline_code: str, position_code: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=501,
            workline_id=1001,
            workline_code=workline_code,
            position_code=position_code,
            position_role="SMT_SORTER_STATION",
            allowed_rack_kind=RackKind.SINGLE_LAYER,
            capacity=1,
            logic_location_code="LOGIC-STATION-A",
            external_location_code="RCS-STATION-A",
            enabled=True,
        )


class FakeRackPlacementRepository:
    def __init__(self, placements: list[RackPlacement] | None = None) -> None:
        self.placements = placements or []
        self.calls: list[tuple[str, str]] = []

    async def list_active_by_workline_position(
        self,
        _db: object,
        *,
        workline_code: str,
        position_code: str,
    ) -> list[RackPlacement]:
        self.calls.append((workline_code, position_code))
        return self.placements


class FakeSystemOutboxRepository:
    def __init__(self, outboxes: list[SystemOutbox] | None = None, *, expose_created_as_active: bool = False) -> None:
        self.outboxes = outboxes or []
        self.expose_created_as_active = expose_created_as_active
        self.created: list[dict[str, Any]] = []
        self.calls: list[tuple[int, str]] = []

    async def get_active_external_station_dispatch(
        self,
        _db: object,
        *,
        workline_id: int,
        position_code: str,
    ) -> SystemOutbox | None:
        self.calls.append((workline_id, position_code))
        for outbox in self.outboxes:
            if outbox.workline_id != workline_id or outbox.dispatch_type != SystemOutboxDispatchType.EXTERNAL_HTTP:
                continue
            if outbox.finished_at is not None and outbox.status != SystemOutboxStatus.BLOCKED_RESOURCE:
                continue
            if outbox.status not in {
                SystemOutboxStatus.NEW,
                SystemOutboxStatus.DISPATCHING,
                SystemOutboxStatus.SENT,
                SystemOutboxStatus.BLOCKED_RESOURCE,
            }:
                continue
            payload = outbox.payload_json or {}
            if payload.get("position_code") == position_code or payload.get("target_position_code") == position_code:
                return outbox
            station = payload.get("station")
            if isinstance(station, dict) and station.get("position_code") == position_code:
                return outbox
        return None

    async def create(self, _db: object, data: dict[str, Any]) -> SystemOutbox:
        self.created.append(data)
        created = SystemOutbox(**data)
        if self.expose_created_as_active:
            self.outboxes.append(created)
        return created


class FakeDb:
    def __init__(self, outbox_repo: FakeSystemOutboxRepository) -> None:
        self.outbox_repo = outbox_repo
        self.added: list[SystemOutbox] = []
        self._flushed = 0

    def add(self, item: SystemOutbox) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        for index, outbox in enumerate(self.added[self._flushed :], start=self._flushed + 1):
            if getattr(outbox, "id", None) is None:
                outbox.id = index
            self.outbox_repo.created.append(
                {
                    "session_id": outbox.session_id,
                    "workline_id": outbox.workline_id,
                    "device_id": outbox.device_id,
                    "operation_domain": outbox.operation_domain,
                    "operation_key": outbox.operation_key,
                    "dispatch_type": outbox.dispatch_type,
                    "dispatch_key": outbox.dispatch_key,
                    "target_type": outbox.target_type,
                    "target_code": outbox.target_code,
                    "payload_json": outbox.payload_json,
                    "trace_id": outbox.trace_id,
                }
            )
            if self.outbox_repo.expose_created_as_active:
                self.outbox_repo.outboxes.append(outbox)
        self._flushed = len(self.added)


class FakeWorklineSessionRepository:
    def __init__(self, sessions: list[WorklineSession] | None = None) -> None:
        self.sessions = sessions or []
        self.calls: list[tuple[int, int]] = []

    async def list_open_by_workline_id(
        self,
        _db: object,
        *,
        workline_id: int,
        limit: int = 50,
    ) -> list[WorklineSession]:
        self.calls.append((workline_id, limit))
        return self.sessions[:limit]

    async def list_all_open_by_workline_id(
        self,
        _db: object,
        *,
        workline_id: int,
    ) -> list[WorklineSession]:
        self.calls.append((workline_id, 0))
        return self.sessions

    async def get_by_workline_id(
        self,
        _db: object,
        workline_id: int,
        status: str | None = None,
    ) -> list[WorklineSession]:
        self.calls.append((workline_id, 0 if status is None else len(self.calls) + 1))
        return [session for session in self.sessions if status is None or session.status == status]


def build_harness(
    *,
    rack_kind: RackKind = RackKind.SINGLE_LAYER,
    placements: list[RackPlacement] | None = None,
    outboxes: list[SystemOutbox] | None = None,
    sessions: list[WorklineSession] | None = None,
    expose_created_as_active: bool = False,
) -> StationLeaseHarness:
    rack_position_service = FakeRackPositionService(rack_kind=rack_kind)
    rack_placement_repo = FakeRackPlacementRepository(placements)
    outbox_repo = FakeSystemOutboxRepository(outboxes, expose_created_as_active=expose_created_as_active)
    db = FakeDb(outbox_repo)
    session_repo = FakeWorklineSessionRepository(sessions)
    service = WorklineStationLeaseService(
        rack_position_service=rack_position_service,
        rack_placement_repository=rack_placement_repo,
        outbox_repository=outbox_repo,
        session_repository=session_repo,
    )
    return StationLeaseHarness(
        service=service,
        db=db,
        rack_position_service=rack_position_service,
        rack_placement_repo=rack_placement_repo,
        outbox_repo=outbox_repo,
        session_repo=session_repo,
    )


def active_placement() -> RackPlacement:
    return RackPlacement(
        id=301,
        rack_id=901,
        rack_code="RACK-001",
        workline_id=1001,
        workline_code="SMT_SORTER_01",
        position_code="STATION-A",
        status="ACTIVE",
    )


def outbox(
    *,
    status: SystemOutboxStatus,
    finished: bool = False,
    position_code: str = "STATION-A",
    dispatch_key: str = "dispatch-1",
) -> SystemOutbox:
    return SystemOutbox(
        id=401,
        workline_id=1001,
        operation_domain="WORKLINE",
        operation_key="op-1",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key=dispatch_key,
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        target_code="WMS",
        payload_json={"station": {"position_code": position_code}, "target_position_code": position_code},
        status=status,
        finished_at=timezone.now_for_db() if finished else None,
    )


def session(*, context_json: dict[str, object], session_id: int = 601) -> WorklineSession:
    return WorklineSession(
        id=session_id,
        session_code=f"S-{session_id}",
        workline_id=1001,
        plugin_key="smt_sorting",
        status=SessionStatus.RUNNING,
        context_json=context_json,
    )


async def lease_status(harness: StationLeaseHarness):
    return await harness.service.get_station_lease_status(
        object(),
        workline_id=1001,
        workline_code="SMT_SORTER_01",
        position_code="STATION-A",
    )


def claim_envelope(*, business_demand_key: str, dispatch_key: str) -> DispatchEnvelope:
    return DispatchEnvelope(
        dispatch_key=dispatch_key,
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        target_code="WMS",
        operation_domain="WORKLINE",
        operation_key=f"rack-op-{business_demand_key}",
        workline_id=1001,
        payload_json={"business_demand_key": business_demand_key, "position_code": "OTHER-STATION"},
    )


def test_station_lease_service_exports_plan_contract_names() -> None:
    from src.app.workline.services import (
        StationLeaseService,
        WorklineStationLeaseService,
        station_lease_service,
        workline_station_lease_service,
    )

    assert StationLeaseService is WorklineStationLeaseService
    assert station_lease_service is workline_station_lease_service


@pytest.mark.asyncio
async def test_station_lease_available_when_no_wes_binding_exists() -> None:
    harness = build_harness()

    result = await lease_status(harness)

    assert result.available is True
    assert result.reason_code is None
    assert result.workline_code == "SMT_SORTER_01"
    assert result.position_code == "STATION-A"
    assert result.active_rack_code is None
    assert result.active_session_id is None
    assert result.active_dispatch_key is None


@pytest.mark.asyncio
async def test_station_lease_busy_when_active_rack_placement_exists() -> None:
    harness = build_harness(placements=[active_placement()])

    result = await lease_status(harness)

    assert result.available is False
    assert result.reason_code == StationLeaseReasonCode.ACTIVE_RACK_BOUND
    assert result.active_rack_code == "RACK-001"
    assert harness.outbox_repo.calls == []
    assert harness.session_repo.calls == []


@pytest.mark.asyncio
async def test_station_lease_can_allow_active_rack_bound_for_target_snapshot_reads() -> None:
    harness = build_harness(placements=[active_placement()])

    result = await harness.service.get_station_lease_status(
        object(),
        workline_id=1001,
        workline_code="SMT_SORTER_01",
        position_code="STATION-A",
        allow_active_rack_bound=True,
    )

    assert result.available is True
    assert result.reason_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        SystemOutboxStatus.NEW,
        SystemOutboxStatus.DISPATCHING,
        SystemOutboxStatus.SENT,
        SystemOutboxStatus.BLOCKED_RESOURCE,
    ],
)
async def test_station_lease_busy_when_active_outbox_targets_position(status: SystemOutboxStatus) -> None:
    harness = build_harness(outboxes=[outbox(status=status)])

    result = await lease_status(harness)

    assert result.available is False
    assert result.reason_code == StationLeaseReasonCode.ACTIVE_DISPATCH_LEASE
    assert result.active_dispatch_key == "dispatch-1"
    assert harness.session_repo.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "finished"),
    [
        (SystemOutboxStatus.SENT, True),
        (SystemOutboxStatus.FAILED, True),
        (SystemOutboxStatus.CANCELLED, True),
    ],
)
async def test_station_lease_ignores_terminal_outbox_for_position(
    status: SystemOutboxStatus,
    finished: bool,
) -> None:
    harness = build_harness(outboxes=[outbox(status=status, finished=finished)])

    result = await lease_status(harness)

    assert result.available is True
    assert result.reason_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [SystemOutboxStatus.FAILED, SystemOutboxStatus.CANCELLED])
async def test_station_lease_ignores_failed_or_cancelled_unfinished_outbox(
    status: SystemOutboxStatus,
) -> None:
    harness = build_harness(outboxes=[outbox(status=status, finished=False)])

    result = await lease_status(harness)

    assert result.available is True
    assert result.reason_code is None


@pytest.mark.asyncio
async def test_station_lease_busy_when_open_session_binds_position() -> None:
    harness = build_harness(sessions=[session(context_json={"station": {"position_code": "STATION-A"}})])

    result = await lease_status(harness)

    assert result.available is False
    assert result.reason_code == StationLeaseReasonCode.ACTIVE_SESSION_BOUND
    assert result.active_session_id == 601


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "context_json",
    [
        {"rack_operation": {"target_position_code": "STATION-A"}},
        {"rack_operation": {"work_position_code": "STATION-A"}},
        {"position_code": "STATION-A"},
        {"active_bin_rack": {"position_code": "STATION-A"}},
        {"waiting_rack_operation_key": "rack-op-1", "rack_operation": {"target_position_code": "STATION-A"}},
    ],
)
async def test_station_lease_busy_when_open_session_waits_dispatch_to_position(
    context_json: dict[str, object],
) -> None:
    harness = build_harness(sessions=[session(context_json=context_json)])

    result = await lease_status(harness)

    assert result.available is False
    assert result.reason_code == StationLeaseReasonCode.ACTIVE_DISPATCH_LEASE
    assert result.active_session_id == 601


@pytest.mark.asyncio
async def test_station_lease_checks_open_session_conflicts_beyond_first_page() -> None:
    sessions = [session(context_json={}, session_id=index) for index in range(1, 52)]
    sessions.append(session(context_json={"station": {"position_code": "STATION-A"}}, session_id=652))
    harness = build_harness(sessions=sessions)

    result = await lease_status(harness)

    assert result.available is False
    assert result.reason_code == StationLeaseReasonCode.ACTIVE_SESSION_BOUND
    assert result.active_session_id == 652
    assert harness.session_repo.calls == [(1001, 1), (1001, 2), (1001, 3), (1001, 4), (1001, 5)]


@pytest.mark.asyncio
async def test_station_lease_rejects_non_single_layer_position() -> None:
    harness = build_harness(rack_kind=RackKind.FIVE_LAYER)

    with pytest.raises(ValueError, match="allowed rack kind mismatch"):
        await lease_status(harness)


@pytest.mark.asyncio
async def test_station_lease_does_not_check_external_location_occupancy() -> None:
    harness = build_harness()

    result = await lease_status(harness)

    assert result.available is True
    assert harness.rack_position_service.calls == [("SMT_SORTER_01", "STATION-A", RackKind.SINGLE_LAYER)]
    assert harness.rack_placement_repo.calls == [("SMT_SORTER_01", "STATION-A")]


@pytest.mark.asyncio
async def test_claim_station_dispatch_lease_only_allows_one_open_station_claim() -> None:
    harness = build_harness()

    first = await harness.service.claim_station_dispatch_lease(
        harness.db,
        workline_id=1001,
        workline_code="SMT_SORTER_01",
        position_code="STATION-A",
        envelope=DispatchEnvelope(
            dispatch_key="claim-1",
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code="WMS",
            operation_domain="WORKLINE",
            operation_key="rack-op-1",
            workline_id=1001,
            payload_json={"demand_id": "D1", "position_code": "OTHER-STATION"},
        ),
    )
    harness.outbox_repo.outboxes = [first]

    second = await harness.service.claim_station_dispatch_lease(
        harness.db,
        workline_id=1001,
        workline_code="SMT_SORTER_01",
        position_code="STATION-A",
        envelope=DispatchEnvelope(
            dispatch_key="claim-2",
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code="WMS",
            operation_domain="WORKLINE",
            operation_key="rack-op-2",
            workline_id=1001,
            payload_json={"demand_id": "D2"},
        ),
    )

    assert first.dispatch_key == "claim-1"
    assert second is None
    assert len(harness.outbox_repo.created) == 1
    assert harness.outbox_repo.created[0]["payload_json"]["position_code"] == "STATION-A"
    assert harness.rack_position_service.locked_calls == [
        ("SMT_SORTER_01", "STATION-A", RackKind.SINGLE_LAYER),
        ("SMT_SORTER_01", "STATION-A", RackKind.SINGLE_LAYER),
    ]


@pytest.mark.asyncio
async def test_claim_station_dispatch_lease_allows_same_operation_active_dispatch() -> None:
    harness = build_harness(outboxes=[outbox(status=SystemOutboxStatus.NEW, dispatch_key="claim-1")])

    created = await harness.service.claim_station_dispatch_lease(
        harness.db,
        workline_id=1001,
        workline_code="SMT_SORTER_01",
        position_code="STATION-A",
        envelope=DispatchEnvelope(
            dispatch_key="claim-2",
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code="WMS",
            operation_domain="WORKLINE",
            operation_key="op-1",
            workline_id=1001,
            payload_json={"demand_id": "D2"},
        ),
        allow_active_operation_key="op-1",
    )

    assert created is not None
    assert created.dispatch_key == "claim-2"
    assert harness.outbox_repo.created[0]["payload_json"]["station"]["position_code"] == "STATION-A"


@pytest.mark.asyncio
async def test_concurrent_station_dispatch_claims_only_create_one_active_lease() -> None:
    harness = build_harness(expose_created_as_active=True)

    first, second = await asyncio.gather(
        harness.service.claim_station_dispatch_lease(
            harness.db,
            workline_id=1001,
            workline_code="SMT_SORTER_01",
            position_code="STATION-A",
            envelope=claim_envelope(business_demand_key="D1", dispatch_key="claim-D1"),
        ),
        harness.service.claim_station_dispatch_lease(
            harness.db,
            workline_id=1001,
            workline_code="SMT_SORTER_01",
            position_code="STATION-A",
            envelope=claim_envelope(business_demand_key="D2", dispatch_key="claim-D2"),
        ),
    )

    created = [claim for claim in (first, second) if claim is not None]
    blocked = [claim for claim in (first, second) if claim is None]
    assert len(created) == 1
    assert len(blocked) == 1
    assert len(harness.outbox_repo.created) == 1
    assert harness.outbox_repo.created[0]["payload_json"]["station"]["position_code"] == "STATION-A"
    assert harness.outbox_repo.created[0]["payload_json"]["position_code"] == "STATION-A"


@pytest.mark.asyncio
async def test_claim_station_dispatch_lease_allows_new_claim_after_terminal_outbox() -> None:
    finished = outbox(status=SystemOutboxStatus.SENT, finished=True, dispatch_key="claim-1")
    harness = build_harness(outboxes=[finished])

    created = await harness.service.claim_station_dispatch_lease(
        harness.db,
        workline_id=1001,
        workline_code="SMT_SORTER_01",
        position_code="STATION-A",
        envelope=DispatchEnvelope(
            dispatch_key="claim-2",
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code="WMS",
            operation_domain="WORKLINE",
            operation_key="rack-op-2",
            workline_id=1001,
            payload_json={"demand_id": "D2"},
        ),
    )

    assert created is not None
    assert created.dispatch_key == "claim-2"
    assert harness.outbox_repo.created[0]["payload_json"]["station"]["position_code"] == "STATION-A"


@pytest.mark.asyncio
async def test_claim_station_dispatch_lease_allows_move_out_to_claim_active_rack_bound_station() -> None:
    harness = build_harness(placements=[active_placement()])

    created = await harness.service.claim_station_dispatch_lease(
        harness.db,
        workline_id=1001,
        workline_code="SMT_SORTER_01",
        position_code="STATION-A",
        envelope=claim_envelope(business_demand_key="MOVE-OUT", dispatch_key="claim-move-out"),
        allow_active_rack_bound=True,
    )

    assert created is not None
    assert created.dispatch_key == "claim-move-out"
    assert harness.outbox_repo.created[0]["payload_json"]["station"]["position_code"] == "STATION-A"


@pytest.mark.asyncio
async def test_claim_station_dispatch_lease_still_blocks_move_out_when_active_dispatch_exists() -> None:
    harness = build_harness(
        placements=[active_placement()],
        outboxes=[outbox(status=SystemOutboxStatus.NEW, dispatch_key="active-dispatch")],
    )

    created = await harness.service.claim_station_dispatch_lease(
        harness.db,
        workline_id=1001,
        workline_code="SMT_SORTER_01",
        position_code="STATION-A",
        envelope=claim_envelope(business_demand_key="MOVE-OUT", dispatch_key="claim-move-out"),
        allow_active_rack_bound=True,
    )

    assert created is None
    assert harness.outbox_repo.created == []
