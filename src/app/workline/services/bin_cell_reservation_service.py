"""工作线料箱格位预占服务。"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from src.app.resource.repositories import (
    BinMaterialMountRepository,
    bin_material_mount_repository,
)
from src.app.workline.models.bin_cell_reservation import (
    BinCellReservationStatus,
    WorklineBinCellReservation,
)
from src.app.workline.repositories.bin_cell_reservation_repository import (
    WorklineBinCellReservationRepository,
    workline_bin_cell_reservation_repository,
)
from src.app.workline.services.runtime_hold_creation_service import (
    runtime_hold_creation_service as default_runtime_hold_creation_service,
)
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


class BinCellReservationStatusCode(str, Enum):
    """预占处理结果。"""

    CLAIMED = "CLAIMED"
    CONSUMED = "CONSUMED"
    RELEASED = "RELEASED"
    DUPLICATE = "DUPLICATE"
    RECONCILING = "RECONCILING"


class BinCellReservationResult(BaseModel):
    """预占处理结果。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    status: BinCellReservationStatusCode
    reservation: WorklineBinCellReservation | None = None
    runtime_hold: Any | None = None
    reason_code: str | None = None
    message: str | None = None


class WorklineBinCellReservationService:
    """工作线料箱格位计划预占服务。"""

    def __init__(
        self,
        *,
        reservation_repository: WorklineBinCellReservationRepository = workline_bin_cell_reservation_repository,
        material_mount_repository: BinMaterialMountRepository = bin_material_mount_repository,
        runtime_hold_creator: Any = default_runtime_hold_creation_service,
    ) -> None:
        self.reservation_repository = reservation_repository
        self.material_mount_repository = material_mount_repository
        self.runtime_hold_creator = runtime_hold_creator

    async def claim_bin_cell(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        workline_code: str,
        session_id: int,
        trace_id: str | None,
        pkg_code: str,
        bin_code: str,
        bin_cell_code: str | None,
        bin_cell_index: str,
        source_event_id: str | None,
        reserved_at: datetime,
    ) -> BinCellReservationResult:
        """为 OUTPUT_ARM 物理动作创建 session 级料格预占。"""

        active_mount = await self.material_mount_repository.get_active_by_bin_cell(
            db,
            bin_code=bin_code,
            bin_cell_index=bin_cell_index,
        )
        if active_mount is not None:
            runtime_hold = await self._create_conflict_hold(
                db,
                workline_id=workline_id,
                session_id=session_id,
                trace_id=trace_id,
                reason_code="BIN_CELL_ALREADY_OCCUPIED",
                evidence={
                    "bin_code": bin_code,
                    "bin_cell_index": bin_cell_index,
                    "active_pkg_code": getattr(active_mount, "pkg_code", None),
                    "incoming_pkg_code": pkg_code,
                },
            )
            return BinCellReservationResult(
                status=BinCellReservationStatusCode.RECONCILING,
                runtime_hold=runtime_hold,
                reason_code="BIN_CELL_ALREADY_OCCUPIED",
                message="料箱格位已有 active 物理占用，不能创建计划预占",
            )

        active_reservation = await self.reservation_repository.get_active_by_bin_cell(
            db,
            bin_code=bin_code,
            bin_cell_index=bin_cell_index,
        )
        if active_reservation is not None:
            if active_reservation.session_id == session_id and active_reservation.pkg_code == pkg_code:
                return BinCellReservationResult(
                    status=BinCellReservationStatusCode.DUPLICATE,
                    reservation=active_reservation,
                )
            runtime_hold = await self._create_conflict_hold(
                db,
                workline_id=workline_id,
                session_id=session_id,
                trace_id=trace_id,
                reason_code="BIN_CELL_RESERVATION_CONFLICT",
                evidence={
                    "bin_code": bin_code,
                    "bin_cell_index": bin_cell_index,
                    "active_session_id": active_reservation.session_id,
                    "active_pkg_code": active_reservation.pkg_code,
                    "incoming_session_id": session_id,
                    "incoming_pkg_code": pkg_code,
                },
            )
            return BinCellReservationResult(
                status=BinCellReservationStatusCode.RECONCILING,
                reservation=active_reservation,
                runtime_hold=runtime_hold,
                reason_code="BIN_CELL_RESERVATION_CONFLICT",
                message="料箱格位已有其他 active 预占",
            )

        reservation_key = f"{workline_code}:{session_id}:{bin_code}:{bin_cell_index}:{pkg_code}"
        reservation = await self.reservation_repository.create(
            db,
            {
                "reservation_key": reservation_key,
                "workline_id": workline_id,
                "workline_code": workline_code,
                "session_id": session_id,
                "trace_id": trace_id,
                "pkg_code": pkg_code,
                "bin_code": bin_code,
                "bin_cell_code": bin_cell_code,
                "bin_cell_index": bin_cell_index,
                "reservation_status": BinCellReservationStatus.PLANNED.value,
                "source_event_id": source_event_id,
                "reserved_at": reserved_at,
            },
        )
        return BinCellReservationResult(status=BinCellReservationStatusCode.CLAIMED, reservation=reservation)

    async def consume_bin_cell(
        self,
        db: AsyncSession,
        *,
        workline_id: int | None = None,
        session_id: int,
        trace_id: str | None = None,
        bin_code: str,
        bin_cell_index: str,
        source_event_id: str | None = None,
        consumed_at: datetime,
    ) -> BinCellReservationResult:
        """物理占用成功后消耗当前 session 的预占。"""

        active_reservation = await self.reservation_repository.get_active_by_bin_cell(
            db,
            bin_code=bin_code,
            bin_cell_index=bin_cell_index,
        )
        if active_reservation is None:
            return BinCellReservationResult(status=BinCellReservationStatusCode.DUPLICATE)
        if active_reservation.session_id != session_id:
            reason_code = "BIN_CELL_RESERVATION_OWNER_MISMATCH"
            runtime_hold = await self._create_conflict_hold(
                db,
                workline_id=workline_id or int(active_reservation.workline_id),
                session_id=session_id,
                trace_id=trace_id,
                reason_code=reason_code,
                evidence={
                    "source_event_id": source_event_id or f"CONSUME_BIN_CELL:{session_id}:{bin_code}:{bin_cell_index}",
                    "bin_code": bin_code,
                    "bin_cell_index": bin_cell_index,
                    "active_session_id": active_reservation.session_id,
                    "active_pkg_code": active_reservation.pkg_code,
                    "incoming_session_id": session_id,
                },
            )
            return BinCellReservationResult(
                status=BinCellReservationStatusCode.RECONCILING,
                reservation=active_reservation,
                runtime_hold=runtime_hold,
                reason_code=reason_code,
                message="物理占用成功时 active 预占属于其他 session",
            )
        reservation = await self.reservation_repository.mark_consumed(
            db,
            active_reservation,
            consumed_at=consumed_at,
        )
        return BinCellReservationResult(status=BinCellReservationStatusCode.CONSUMED, reservation=reservation)

    async def apply_runtime_reservation(
        self,
        *,
        db: AsyncSession,
        session: Any,
        workline: Any,
        operation: str,
        payload_json: dict[str, Any],
        idempotency_key: str | None = None,
        trace_id: str | None = None,
    ) -> BinCellReservationResult:
        """RuntimeIntent 入口：执行预占 claim/consume/release。"""

        if operation == "CLAIM_BIN_CELL":
            return await self.claim_bin_cell(
                db,
                workline_id=int(workline.id),
                workline_code=str(getattr(workline, "line_code", None) or getattr(workline, "workline_code", "")),
                session_id=int(session.id),
                trace_id=trace_id,
                pkg_code=str(payload_json["pkg_code"]),
                bin_code=str(payload_json["bin_code"]),
                bin_cell_code=payload_json.get("bin_cell_code"),
                bin_cell_index=str(payload_json["bin_cell_index"]),
                source_event_id=payload_json.get("source_event_id"),
                reserved_at=payload_json.get("reserved_at") or timezone.now_for_db(),
            )
        if operation == "CONSUME_BIN_CELL":
            raw_source_event_id = payload_json.get("source_event_id") or idempotency_key
            return await self.consume_bin_cell(
                db,
                workline_id=int(workline.id),
                session_id=int(session.id),
                trace_id=trace_id,
                bin_code=str(payload_json["bin_code"]),
                bin_cell_index=str(payload_json["bin_cell_index"]),
                source_event_id=str(raw_source_event_id) if raw_source_event_id else None,
                consumed_at=payload_json.get("consumed_at") or timezone.now_for_db(),
            )
        raise ValueError(f"unsupported resource reservation operation: {operation}")

    async def _create_conflict_hold(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        session_id: int,
        trace_id: str | None,
        reason_code: str,
        evidence: dict[str, Any],
    ) -> Any:
        return await self.runtime_hold_creator.create_for_resource_reconciliation(
            db,
            workline_id=workline_id,
            session_id=session_id,
            source_reason=reason_code,
            source_event_id=evidence.get("source_event_id") or evidence.get("bin_code"),
            trace_id=trace_id,
            evidence=evidence,
        )


workline_bin_cell_reservation_service = WorklineBinCellReservationService()


__all__ = [
    "BinCellReservationResult",
    "BinCellReservationStatusCode",
    "WorklineBinCellReservationService",
    "workline_bin_cell_reservation_service",
]
