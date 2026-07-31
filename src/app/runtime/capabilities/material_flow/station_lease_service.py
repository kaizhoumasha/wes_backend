# 已从 workline/services/ 迁入
"""工作线 Station lease 观察与派发准入服务。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from src.app.resource.models import RackKind
from src.app.resource.repositories import RackPlacementRepository, rack_placement_repository
from src.app.runtime.orchestration.repositories.session_repository import (
    WorklineSessionRepository,
    workline_session_repository,
)
from src.app.sys.models.outbox import DispatchEnvelope, SystemOutbox, SystemOutboxDispatchType
from src.app.sys.repositories.outbox_repository import SystemOutboxRepository, system_outbox_repository

# rack_position_service 在 stable rack-position service boundary 内保留原地
# (workline/services/rack_position_service.py)。
# 这里继续走 workline.services 入口以便跨层兼容。
from src.app.workline.services.rack_position_service import (
    WorklineRackPositionService,
    workline_rack_position_service,
)
from src.utils.value_normalization import coerce_optional_str

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.runtime.orchestration.models.session import WorklineSession

_SMT_SORTING_TARGET_STATION_CODE = "TARGET_STATION"


class StationLeaseReasonCode(StrEnum):
    """Station lease 不可用原因。"""

    ACTIVE_RACK_BOUND = "ACTIVE_RACK_BOUND"
    ACTIVE_SESSION_BOUND = "ACTIVE_SESSION_BOUND"
    ACTIVE_DISPATCH_LEASE = "ACTIVE_DISPATCH_LEASE"


@dataclass(frozen=True)
class StationLeaseResult:
    """Station lease 准入观察结果。

    该结果只表达当前 WES 业务绑定观察，不是并发互斥本身；真正创建 WMS dispatch 时必须在同一事务内
    通过 claim_station_dispatch_lease 重新检查并创建 outbox。
    """

    workline_code: str
    position_code: str
    available: bool
    reason_code: StationLeaseReasonCode | None = None
    active_rack_code: str | None = None
    active_session_id: int | None = None
    active_dispatch_key: str | None = None


class WorklineStationLeaseService:
    """Station lease 最小服务。"""

    def __init__(
        self,
        *,
        rack_position_service: WorklineRackPositionService = workline_rack_position_service,
        rack_placement_repository: RackPlacementRepository = rack_placement_repository,
        outbox_repository: SystemOutboxRepository = system_outbox_repository,
        session_repository: WorklineSessionRepository = workline_session_repository,
    ) -> None:
        self.rack_position_service = rack_position_service
        self.rack_placement_repository = rack_placement_repository
        self.outbox_repository = outbox_repository
        self.session_repository = session_repository

    async def get_station_lease_status(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        workline_code: str,
        position_code: str,
        rack_kind: RackKind = RackKind.SINGLE_LAYER,
        allow_active_rack_bound: bool = False,
        allow_active_operation_key: str | None = None,
    ) -> StationLeaseResult:
        """按 Station scope 查询 WES 侧占用状态。"""

        return await self._build_station_lease_status(
            db,
            workline_id=workline_id,
            workline_code=workline_code,
            position_code=position_code,
            rack_kind=rack_kind,
            lock_position=False,
            allow_active_rack_bound=allow_active_rack_bound,
            allow_active_operation_key=allow_active_operation_key,
        )

    async def claim_station_dispatch_lease(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        workline_code: str,
        position_code: str,
        envelope: DispatchEnvelope,
        allow_active_rack_bound: bool = False,
        allow_active_operation_key: str | None = None,
    ) -> SystemOutbox | None:
        """在同一事务内锁定 Station scope、重查 lease 并创建外部派发 outbox。

        锁定复用 WorklineRackPosition 配置行，避免为 Station lease 引入独立表。
        调用方仍需负责事务边界。
        """

        if envelope.dispatch_type != SystemOutboxDispatchType.EXTERNAL_HTTP:
            raise ValueError("station dispatch lease only supports EXTERNAL_HTTP outbox")
        frozen_binding = envelope.frozen_binding
        if frozen_binding is None:
            raise ValueError("station dispatch lease requires frozen EXTERNAL_HTTP binding")

        status = await self._build_station_lease_status(
            db,
            workline_id=workline_id,
            workline_code=workline_code,
            position_code=position_code,
            rack_kind=RackKind.SINGLE_LAYER,
            lock_position=True,
            allow_active_rack_bound=allow_active_rack_bound,
            allow_active_operation_key=allow_active_operation_key,
        )
        if not status.available:
            return None

        payload_json = self._payload_with_station_scope(
            envelope.payload_json,
            workline_code=workline_code,
            position_code=position_code,
        )
        outbox = SystemOutbox(
            session_id=envelope.session_id,
            workline_id=workline_id,
            device_id=envelope.device_id,
            operation_domain=envelope.operation_domain,
            operation_key=envelope.operation_key,
            dispatch_type=envelope.dispatch_type,
            dispatch_key=envelope.dispatch_key,
            idempotency_key=envelope.idempotency_key,
            target_type=envelope.target_type,
            target_code=envelope.target_code,
            provider_profile_identity=envelope.provider_profile_identity,
            provider_profile_hash=frozen_binding.provider_profile_hash,
            operation_identity=envelope.operation_identity,
            binding_revision=frozen_binding.binding_revision,
            target_snapshot_json=frozen_binding.target_snapshot.as_json(),
            target_snapshot_hash=frozen_binding.target_snapshot_hash,
            auth_scheme=frozen_binding.auth_scheme,
            network_trust_mode=frozen_binding.network_trust_mode,
            credential_reference=frozen_binding.credential_reference,
            payload_json=payload_json,
            canonical_payload_bytes=envelope.canonical_payload_bytes,
            payload_hash=envelope.payload_hash,
            trace_id=envelope.trace_id,
        )
        db.add(outbox)
        await db.flush()
        return outbox

    async def _build_station_lease_status(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        workline_code: str,
        position_code: str,
        rack_kind: RackKind,
        lock_position: bool,
        allow_active_rack_bound: bool = False,
        allow_active_operation_key: str | None = None,
    ) -> StationLeaseResult:
        if lock_position:
            _ = await self.rack_position_service.require_enabled_position_for_update(
                db,
                workline_code=workline_code,
                position_code=position_code,
                rack_kind=rack_kind,
            )
        else:
            _ = await self.rack_position_service.require_enabled_position(
                db,
                workline_code=workline_code,
                position_code=position_code,
                rack_kind=rack_kind,
            )

        placements = await self.rack_placement_repository.list_active_by_workline_position(
            db,
            workline_code=workline_code,
            position_code=position_code,
        )
        if placements and not allow_active_rack_bound:
            return StationLeaseResult(
                workline_code=workline_code,
                position_code=position_code,
                available=False,
                reason_code=StationLeaseReasonCode.ACTIVE_RACK_BOUND,
                active_rack_code=getattr(placements[0], "rack_code", None),
            )

        active_outbox = await self.outbox_repository.get_active_external_station_dispatch(
            db,
            workline_id=workline_id,
            position_code=position_code,
        )
        active_operation_key = coerce_optional_str(getattr(active_outbox, "operation_key", None))
        if active_outbox is not None and (
            allow_active_operation_key is None or active_operation_key != allow_active_operation_key
        ):
            return StationLeaseResult(
                workline_code=workline_code,
                position_code=position_code,
                available=False,
                reason_code=StationLeaseReasonCode.ACTIVE_DISPATCH_LEASE,
                active_dispatch_key=active_outbox.dispatch_key,
                active_session_id=active_outbox.session_id,
            )

        sessions = await self.session_repository.list_open_station_conflict_candidates(
            db,
            workline_id=workline_id,
            position_code=position_code,
        )
        for session in sessions:
            reason_code = self._session_station_lease_reason(
                session,
                position_code=position_code,
                allow_active_operation_key=allow_active_operation_key,
            )
            if reason_code is not None:
                return StationLeaseResult(
                    workline_code=workline_code,
                    position_code=position_code,
                    available=False,
                    reason_code=reason_code,
                    active_session_id=session.id,
                )

        return StationLeaseResult(workline_code=workline_code, position_code=position_code, available=True)

    @classmethod
    def _session_station_lease_reason(
        cls,
        session: WorklineSession,
        *,
        position_code: str,
        allow_active_operation_key: str | None = None,
    ) -> StationLeaseReasonCode | None:
        context = session.context_json if isinstance(session.context_json, dict) else {}

        station = context.get("station")
        if isinstance(station, dict) and station.get("position_code") == position_code:
            return StationLeaseReasonCode.ACTIVE_SESSION_BOUND

        if context.get("position_code") == position_code:
            return StationLeaseReasonCode.ACTIVE_DISPATCH_LEASE

        active_bin_rack = context.get("active_bin_rack")
        if isinstance(active_bin_rack, dict) and active_bin_rack.get("position_code") == position_code:
            return StationLeaseReasonCode.ACTIVE_DISPATCH_LEASE

        sorting = context.get("sorting")
        if (
            position_code == _SMT_SORTING_TARGET_STATION_CODE
            and isinstance(sorting, dict)
            and isinstance(sorting.get("pending_target_placement"), dict)
            and sorting["pending_target_placement"]
        ):
            return StationLeaseReasonCode.ACTIVE_SESSION_BOUND

        rack_operation = context.get("rack_operation")
        if isinstance(rack_operation, dict) and (
            rack_operation.get("target_position_code") == position_code
            or rack_operation.get("work_position_code") == position_code
        ):
            operation_key = coerce_optional_str(rack_operation.get("operation_key")) or coerce_optional_str(
                context.get("waiting_rack_operation_key")
            )
            if allow_active_operation_key is not None and operation_key == allow_active_operation_key:
                return None
            return StationLeaseReasonCode.ACTIVE_DISPATCH_LEASE

        return None

    @staticmethod
    def _payload_with_station_scope(
        payload_json: dict[str, Any],
        *,
        workline_code: str,
        position_code: str,
    ) -> dict[str, Any]:
        payload = dict(payload_json or {})
        station = payload.get("station")
        station_payload = dict(station) if isinstance(station, dict) else {}
        if station_payload.get("workline_code") != workline_code:
            raise ValueError("station dispatch canonical payload must freeze station.workline_code in gateway")
        if station_payload.get("position_code") != position_code:
            raise ValueError("station dispatch canonical payload must freeze station.position_code in gateway")
        if payload.get("workline_code") != workline_code:
            raise ValueError("station dispatch canonical payload must freeze workline_code in gateway")
        if payload.get("position_code") != position_code:
            raise ValueError("station dispatch canonical payload must freeze position_code in gateway")
        return payload


workline_station_lease_service = WorklineStationLeaseService()
StationLeaseService = WorklineStationLeaseService
station_lease_service = workline_station_lease_service


__all__ = [
    "StationLeaseReasonCode",
    "StationLeaseResult",
    "StationLeaseService",
    "WorklineStationLeaseService",
    "station_lease_service",
    "workline_station_lease_service",
]
