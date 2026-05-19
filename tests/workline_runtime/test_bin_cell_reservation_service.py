from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.workline.models.bin_cell_reservation import (
    BinCellReservationStatus,
    WorklineBinCellReservation,
)
from src.app.workline.services.bin_cell_reservation_service import (
    BinCellReservationStatusCode,
    WorklineBinCellReservationService,
)


class RecordingReservationRepo:
    def __init__(self, active: WorklineBinCellReservation | None = None) -> None:
        self.active = active
        self.created: list[dict[str, Any]] = []
        self.consumed: list[WorklineBinCellReservation] = []

    async def get_active_by_bin_cell(
        self,
        _db: object,
        *,
        bin_code: str,
        bin_cell_index: str,
    ) -> WorklineBinCellReservation | None:
        assert bin_code == "BIN-001"
        assert bin_cell_index == "4"
        return self.active

    async def create(self, _db: object, data: dict[str, Any]) -> WorklineBinCellReservation:
        self.created.append(data)
        return WorklineBinCellReservation(**data)

    async def mark_consumed(
        self,
        _db: object,
        reservation: WorklineBinCellReservation,
        *,
        consumed_at: datetime,
    ) -> WorklineBinCellReservation:
        reservation.reservation_status = BinCellReservationStatus.CONSUMED
        reservation.consumed_at = consumed_at
        self.consumed.append(reservation)
        return reservation


class RecordingMaterialMountRepo:
    def __init__(self, active: object | None = None) -> None:
        self.active = active

    async def get_active_by_bin_cell(self, _db: object, *, bin_code: str, bin_cell_index: str) -> object | None:
        assert bin_code == "BIN-001"
        assert bin_cell_index == "4"
        return self.active


class RecordingRuntimeHoldCreator:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create_for_resource_reconciliation(self, _db: object, **kwargs: Any) -> SimpleNamespace:
        self.created.append(kwargs)
        return SimpleNamespace(id=9901, **kwargs)


class RecordingRuntimeHoldRepository:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create_open_hold(self, _db: object, **data: Any) -> SimpleNamespace:
        self.created.append(data)
        return SimpleNamespace(id=9901, **data)


class RecordingWorkLineRepository:
    def __init__(self, workline: SimpleNamespace) -> None:
        self.workline = workline
        self.locked_ids: list[int] = []

    async def get_for_update(self, _db: object, workline_id: int) -> SimpleNamespace:
        self.locked_ids.append(workline_id)
        return self.workline


def _reservation(**overrides: Any) -> WorklineBinCellReservation:
    values: dict[str, Any] = {
        "reservation_key": "reserve:old",
        "workline_id": 1001,
        "workline_code": "SMT_SORTER_01",
        "session_id": 2001,
        "trace_id": "trace-old",
        "pkg_code": "PKG-OLD",
        "bin_code": "BIN-001",
        "bin_cell_code": "BIN-001-4",
        "bin_cell_index": "4",
        "reservation_status": BinCellReservationStatus.PLANNED,
        "reserved_at": datetime(2026, 5, 18, 8, 0, 0),
    }
    values.update(overrides)
    return WorklineBinCellReservation(**values)


@pytest.mark.asyncio
async def test_claim_bin_cell_creates_planned_reservation_when_cell_is_free() -> None:
    reservation_repo = RecordingReservationRepo()
    service = WorklineBinCellReservationService(
        reservation_repository=reservation_repo,
        material_mount_repository=RecordingMaterialMountRepo(),
    )

    result = await service.claim_bin_cell(
        SimpleNamespace(),
        workline_id=1001,
        workline_code="SMT_SORTER_01",
        session_id=2002,
        trace_id="trace-001",
        pkg_code="PKG-001",
        bin_code="BIN-001",
        bin_cell_code="BIN-001-4",
        bin_cell_index="4",
        source_event_id="CMD-MOVE-001",
        reserved_at=datetime(2026, 5, 18, 9, 0, 0),
    )

    assert result.status == BinCellReservationStatusCode.CLAIMED
    assert result.reservation is not None
    assert reservation_repo.created[0]["reservation_status"] == BinCellReservationStatus.PLANNED.value
    assert reservation_repo.created[0]["reservation_key"] == "SMT_SORTER_01:2002:BIN-001:4:PKG-001"


@pytest.mark.asyncio
async def test_claim_bin_cell_conflict_creates_hold_without_overwriting_active_claim() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    reservation_repo = RecordingReservationRepo(active=_reservation(session_id=2001, pkg_code="PKG-OLD"))
    service = WorklineBinCellReservationService(
        reservation_repository=reservation_repo,
        material_mount_repository=RecordingMaterialMountRepo(),
        runtime_hold_creator=runtime_holds,
    )

    result = await service.claim_bin_cell(
        SimpleNamespace(),
        workline_id=1001,
        workline_code="SMT_SORTER_01",
        session_id=2002,
        trace_id="trace-001",
        pkg_code="PKG-001",
        bin_code="BIN-001",
        bin_cell_code="BIN-001-4",
        bin_cell_index="4",
        source_event_id="CMD-MOVE-001",
        reserved_at=datetime(2026, 5, 18, 9, 0, 0),
    )

    assert result.status == BinCellReservationStatusCode.RECONCILING
    assert result.runtime_hold is not None
    assert reservation_repo.created == []
    assert runtime_holds.created[0]["source_reason"] == "BIN_CELL_RESERVATION_CONFLICT"
    assert runtime_holds.created[0]["evidence"]["active_session_id"] == 2001


@pytest.mark.asyncio
async def test_consume_bin_cell_marks_current_session_reservation_consumed() -> None:
    active = _reservation(session_id=2002, pkg_code="PKG-001")
    reservation_repo = RecordingReservationRepo(active=active)
    service = WorklineBinCellReservationService(
        reservation_repository=reservation_repo,
        material_mount_repository=RecordingMaterialMountRepo(),
    )

    result = await service.consume_bin_cell(
        SimpleNamespace(),
        session_id=2002,
        bin_code="BIN-001",
        bin_cell_index="4",
        consumed_at=datetime(2026, 5, 18, 9, 5, 0),
    )

    assert result.status == BinCellReservationStatusCode.CONSUMED
    assert reservation_repo.consumed == [active]
    assert active.reservation_status == BinCellReservationStatus.CONSUMED


@pytest.mark.asyncio
async def test_consume_bin_cell_owner_mismatch_creates_hold_and_freezes_workline() -> None:
    from src.app.workline.models.safety import WorkLineRuntimeStatus
    from src.app.workline.services.runtime_hold_creation_service import RuntimeHoldCreationService

    active = _reservation(session_id=2001, pkg_code="PKG-OLD")
    reservation_repo = RecordingReservationRepo(active=active)
    hold_repo = RecordingRuntimeHoldRepository()
    workline = SimpleNamespace(
        id=1001,
        line_code="SMT_SORTER_01",
        runtime_status=WorkLineRuntimeStatus.READY,
        stopped_at=None,
        stopped_reason=None,
    )
    runtime_hold_creator = RuntimeHoldCreationService(
        repository=hold_repo,
        workline_repository=RecordingWorkLineRepository(workline),
    )
    service = WorklineBinCellReservationService(
        reservation_repository=reservation_repo,
        material_mount_repository=RecordingMaterialMountRepo(),
        runtime_hold_creator=runtime_hold_creator,
    )

    result = await service.apply_runtime_reservation(
        db=SimpleNamespace(),
        session=SimpleNamespace(id=2002),
        workline=workline,
        operation="CONSUME_BIN_CELL",
        payload_json={"bin_code": "BIN-001", "bin_cell_index": "4"},
        idempotency_key="CONSUME_BIN_CELL:2002:CMD-OUTPUT-001:BIN-001:4",
        trace_id="trace-001",
    )

    assert result.status == BinCellReservationStatusCode.RECONCILING
    assert result.runtime_hold is not None
    assert reservation_repo.consumed == []
    assert hold_repo.created[0]["source_reason"] == "BIN_CELL_RESERVATION_OWNER_MISMATCH"
    assert hold_repo.created[0]["source_idempotency_key"] == (
        "resource-reconciliation:BIN_CELL_RESERVATION_OWNER_MISMATCH:CONSUME_BIN_CELL:2002:CMD-OUTPUT-001:BIN-001:4"
    )
    assert hold_repo.created[0]["evidence_snapshot_json"]["active_session_id"] == 2001
    assert hold_repo.created[0]["evidence_snapshot_json"]["incoming_session_id"] == 2002
    assert workline.runtime_status == WorkLineRuntimeStatus.RECONCILING
    assert workline.stopped_reason == "BIN_CELL_RESERVATION_OWNER_MISMATCH"
