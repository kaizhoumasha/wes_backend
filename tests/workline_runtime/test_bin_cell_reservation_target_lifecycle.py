"""Material-flow CellReservation 目标生命周期合同。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from src.app.runtime.capabilities.material_flow.bin_cell_reservation_service import (
    BinCellReservationStatusCode,
    WorklineBinCellReservationService,
)
from src.app.runtime.orchestration.models.bin_cell_reservation import BinCellReservationStatus
from src.utils.timezone import timezone


class _RuntimeHoldRecorder:
    """记录 RuntimeHold 创建请求，避免测试依赖完整 hold 状态机。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_for_resource_reconciliation(self, _db, **kwargs):
        self.calls.append(kwargs)
        return {"runtime_hold": kwargs}


async def _claim(
    service: WorklineBinCellReservationService,
    db_session,
    *,
    session_id: int,
    pkg_code: str,
    bin_code: str = "BIN-P4-001",
    bin_cell_index: str = "1",
):
    return await service.claim_bin_cell(
        db_session,
        workline_id=1,
        workline_code="WL-P4",
        session_id=session_id,
        trace_id=f"trace-{session_id}",
        pkg_code=pkg_code,
        bin_code=bin_code,
        bin_cell_code="C01",
        bin_cell_index=bin_cell_index,
        source_event_id=f"evt-claim-{session_id}",
        reserved_at=timezone.now_for_db(),
        material_identity_key=f"mat-{pkg_code}",
    )


@pytest.mark.asyncio
async def test_claim_conflict_persists_reconciling_and_keeps_cell_frozen(db_session) -> None:
    """同格位不同 owner 的预占冲突必须持久冻结，而不是只返回服务态。"""

    hold_recorder = _RuntimeHoldRecorder()
    service = WorklineBinCellReservationService(runtime_hold_creator=hold_recorder)

    claimed = await _claim(service, db_session, session_id=101, pkg_code="PKG-A")
    assert claimed.status == BinCellReservationStatusCode.CLAIMED
    assert claimed.reservation is not None

    conflict = await _claim(service, db_session, session_id=202, pkg_code="PKG-B")

    assert conflict.status == BinCellReservationStatusCode.RECONCILING
    assert conflict.reason_code == "BIN_CELL_RESERVATION_CONFLICT"
    assert conflict.reservation is not None
    assert conflict.reservation.id == claimed.reservation.id
    assert conflict.reservation.reservation_status == BinCellReservationStatus.RECONCILING
    assert hold_recorder.calls[-1]["source_reason"] == "BIN_CELL_RESERVATION_CONFLICT"

    frozen = await service.reservation_repository.get_active_or_frozen_by_bin_cell(
        db_session,
        bin_code="BIN-P4-001",
        bin_cell_index="1",
    )
    assert frozen is not None
    assert frozen.id == claimed.reservation.id
    assert frozen.reservation_status == BinCellReservationStatus.RECONCILING


@pytest.mark.asyncio
async def test_owner_mismatch_persists_reconciling_and_keeps_cell_frozen(db_session) -> None:
    """owner mismatch 不能只返回服务结果，必须持久冻结格位。"""

    hold_recorder = _RuntimeHoldRecorder()
    service = WorklineBinCellReservationService(runtime_hold_creator=hold_recorder)

    claimed = await _claim(service, db_session, session_id=101, pkg_code="PKG-A")
    assert claimed.status == BinCellReservationStatusCode.CLAIMED
    assert claimed.reservation is not None

    mismatch = await service.consume_bin_cell(
        db_session,
        workline_id=1,
        session_id=202,
        trace_id="trace-mismatch",
        bin_code="BIN-P4-001",
        bin_cell_index="1",
        source_event_id="evt-consume-owner-mismatch",
        consumed_at=timezone.now_for_db(),
    )

    assert mismatch.status == BinCellReservationStatusCode.RECONCILING
    assert mismatch.reason_code == "BIN_CELL_RESERVATION_OWNER_MISMATCH"
    assert mismatch.reservation is not None
    assert mismatch.reservation.reservation_status == BinCellReservationStatus.RECONCILING
    assert hold_recorder.calls[-1]["source_reason"] == "BIN_CELL_RESERVATION_OWNER_MISMATCH"

    frozen = await service.reservation_repository.get_active_or_frozen_by_bin_cell(
        db_session,
        bin_code="BIN-P4-001",
        bin_cell_index="1",
    )
    assert frozen is not None
    assert frozen.id == claimed.reservation.id
    assert frozen.reservation_status == BinCellReservationStatus.RECONCILING

    competing = await _claim(service, db_session, session_id=303, pkg_code="PKG-B")
    assert competing.status == BinCellReservationStatusCode.RECONCILING
    assert competing.reason_code == "BIN_CELL_RESERVATION_CONFLICT"


@pytest.mark.asyncio
async def test_reconciling_reservation_cannot_be_released_by_normal_failure_path(db_session) -> None:
    """RECONCILING 表示现场事实不确定，普通失败释放路径不得静默释放。"""

    hold_recorder = _RuntimeHoldRecorder()
    service = WorklineBinCellReservationService(runtime_hold_creator=hold_recorder)

    claimed = await _claim(service, db_session, session_id=111, pkg_code="PKG-FROZEN")
    assert claimed.reservation is not None

    await service.consume_bin_cell(
        db_session,
        workline_id=1,
        session_id=222,
        trace_id="trace-freeze",
        bin_code="BIN-P4-001",
        bin_cell_index="1",
        source_event_id="evt-freeze",
        consumed_at=timezone.now_for_db(),
    )

    release = await service.release_bin_cell(
        db_session,
        workline_id=1,
        session_id=111,
        trace_id="trace-release-frozen",
        bin_code="BIN-P4-001",
        bin_cell_index="1",
        source_event_id="evt-release-frozen",
        released_at=timezone.now_for_db(),
    )

    assert release.status == BinCellReservationStatusCode.RECONCILING
    assert release.reason_code == "BIN_CELL_RESERVATION_FROZEN"
    assert release.reservation is not None
    assert release.reservation.reservation_status == BinCellReservationStatus.RECONCILING
    assert release.reservation.released_at is None


@pytest.mark.asyncio
async def test_ttl_release_only_expires_planned_reservations(db_session) -> None:
    """TTL 只释放未发生物理投放的 PLANNED，不能释放 RECONCILING。"""

    hold_recorder = _RuntimeHoldRecorder()
    service = WorklineBinCellReservationService(runtime_hold_creator=hold_recorder)
    now = timezone.now_for_db()

    expired = await _claim(
        service,
        db_session,
        session_id=301,
        pkg_code="PKG-EXPIRED",
        bin_code="BIN-P4-TTL",
        bin_cell_index="1",
    )
    assert expired.reservation is not None
    expired.reservation.expires_at = now

    frozen = await _claim(
        service,
        db_session,
        session_id=302,
        pkg_code="PKG-FROZEN-TTL",
        bin_code="BIN-P4-TTL",
        bin_cell_index="2",
    )
    assert frozen.reservation is not None
    frozen.reservation.expires_at = now
    await service.reservation_repository.mark_reconciling(
        db_session,
        frozen.reservation,
        reason_code="WMS_REJECT",
        evidence={"source_event_id": "evt-wms-reject"},
    )

    future = await _claim(
        service,
        db_session,
        session_id=303,
        pkg_code="PKG-FUTURE",
        bin_code="BIN-P4-TTL",
        bin_cell_index="3",
    )
    assert future.reservation is not None
    future.reservation.expires_at = now + timedelta(days=1)
    db_session.add_all([expired.reservation, frozen.reservation, future.reservation])
    await db_session.flush()

    released = await service.release_expired_planned_reservations(
        db_session,
        expired_at=now,
        limit=50,
    )
    await db_session.flush()

    assert [reservation.id for reservation in released] == [expired.reservation.id]
    assert expired.reservation.reservation_status == BinCellReservationStatus.RELEASED
    assert expired.reservation.released_at == now
    assert frozen.reservation.reservation_status == BinCellReservationStatus.RECONCILING
    assert frozen.reservation.released_at is None
    assert future.reservation.reservation_status == BinCellReservationStatus.PLANNED


@pytest.mark.asyncio
async def test_claim_preserves_correlation_and_provider_evidence_in_idempotency_scope(db_session) -> None:
    """预约幂等键和 evidence 必须覆盖 correlation/source/provider 口径。"""

    service = WorklineBinCellReservationService(runtime_hold_creator=_RuntimeHoldRecorder())

    claimed = await service.claim_bin_cell(
        db_session,
        workline_id=1,
        workline_code="WL-P4",
        session_id=401,
        trace_id="trace-evidence",
        pkg_code="PKG-EVIDENCE",
        bin_code="BIN-P4-EVIDENCE",
        bin_cell_code="C02",
        bin_cell_index="2",
        source_event_id="evt-claim-evidence",
        reserved_at=timezone.now_for_db(),
        material_identity_key="mat-evidence",
        correlation_id="corr-evidence",
        provider_code="WMS",
        source_version="wms-v3",
        evidence_json={"fixture_set": "material-flow-local"},
    )

    assert claimed.status == BinCellReservationStatusCode.CLAIMED
    assert claimed.reservation is not None
    assert claimed.reservation.correlation_id == "corr-evidence"
    assert "corr-evidence" in claimed.reservation.reservation_key
    assert "evt-claim-evidence" in claimed.reservation.reservation_key
    assert claimed.reservation.evidence_json == {
        "trace_id": "trace-evidence",
        "correlation_id": "corr-evidence",
        "source_event_id": "evt-claim-evidence",
        "provider_code": "WMS",
        "source_version": "wms-v3",
        "fixture_set": "material-flow-local",
    }
