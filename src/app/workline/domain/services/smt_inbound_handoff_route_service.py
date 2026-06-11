"""SMT 入库 handoff 目标 WorkLine 路由服务。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from src.app.workline.domain.services.smt_inbound_handoff_reason import (
    SMT_INBOUND_HANDOFF_REASON_CATALOG,
    SmtInboundHandoffReasonCatalog,
    SmtInboundHandoffReasonCode,
)
from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.utils.timezone import timezone
from src.workline_plugins.smt_sorting_inbound.constants import SMT_SORTING_INBOUND_PLUGIN_KEY

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class SmtInboundHandoffRouteResult:
    """SMT 入库 handoff route 结果。"""

    kind: str
    manual_hold: bool = False
    retryable: bool = False
    selected_workline: object | None = None
    selected_workline_id: int | None = None
    selected_workline_code: str | None = None
    source_station_code: str | None = None
    source_position_code: str | None = None
    route_evidence: dict[str, Any] = field(default_factory=dict)
    failure_code: str | None = None
    failure_message: str | None = None
    next_attempt_at: datetime | None = None


class SmtInboundHandoffRouteService:
    """选择并准入可承接 SMT inbound handoff 的分拣 WorkLine。"""

    def __init__(
        self,
        *,
        station_lease_service: object | None = None,
        session_repository: object | None = None,
        ecs_status_probe: object | None = None,
        reason_catalog: SmtInboundHandoffReasonCatalog = SMT_INBOUND_HANDOFF_REASON_CATALOG,
        retry_delay_seconds: int = 30,
    ) -> None:
        self.station_lease_service = station_lease_service
        self.session_repository = session_repository
        self.ecs_status_probe = ecs_status_probe or _allow_idle_ecs_probe
        self.reason_catalog = reason_catalog
        self.retry_delay_seconds = retry_delay_seconds

    async def resolve_route(
        self,
        db: AsyncSession,
        *,
        demand: object,
        source_item: object,
        candidate_worklines: Sequence[object],
    ) -> SmtInboundHandoffRouteResult:
        """按配置候选与运行态准入选择目标 WorkLine。"""

        ordered_candidates = self._ordered_config_candidates(candidate_worklines)
        candidate_order = [self._candidate_order_item(workline) for workline in ordered_candidates]
        if not ordered_candidates:
            return self._manual_hold(SmtInboundHandoffReasonCode.ROUTE_NOT_FOUND, {"candidate_order": candidate_order})

        workline = ordered_candidates[0]
        route_config = self._route_config(workline)
        route_evidence = {
            "candidate_order": candidate_order,
            "route_priority": self._route_priority(workline),
            "source_station_code": self._source_station_code(route_config),
            "source_position_code": self._source_position_code(route_config),
        }

        if getattr(workline, "runtime_status", None) != WorkLineRuntimeStatus.READY:
            return self._retry(SmtInboundHandoffReasonCode.TARGET_WORKLINE_NOT_READY, route_evidence)

        lease_result = await self._station_lease_service().get_station_lease_status(
            db,
            workline_id=getattr(workline, "id", None),
            position_code=route_evidence["source_position_code"],
            source_workline_code=getattr(demand, "source_workline_code", None),
            handoff_source_item_id=getattr(source_item, "id", None),
        )
        if not bool(getattr(lease_result, "available", False)):
            evidence = {**route_evidence, "station_lease_reason_code": getattr(lease_result, "reason_code", None)}
            return self._retry(SmtInboundHandoffReasonCode.SOURCE_STATION_BUSY, evidence)

        if await self._has_open_current_material(db, workline_id=int(workline.id)):
            return self._retry(SmtInboundHandoffReasonCode.TARGET_SESSION_BUSY, route_evidence)

        ecs_result = await self.ecs_status_probe(db, workline=workline, route=route_evidence)
        if not bool(getattr(ecs_result, "available", False)):
            evidence = {**route_evidence, "ecs_reason_code": getattr(ecs_result, "reason_code", None)}
            return self._retry(SmtInboundHandoffReasonCode.ECS_DEVICE_NOT_IDLE, evidence)

        return SmtInboundHandoffRouteResult(
            kind="SELECTED",
            selected_workline=workline,
            selected_workline_id=getattr(workline, "id", None),
            selected_workline_code=getattr(workline, "line_code", None),
            source_station_code=route_evidence["source_station_code"],
            source_position_code=route_evidence["source_position_code"],
            route_evidence=route_evidence,
        )

    def _ordered_config_candidates(self, candidate_worklines: Sequence[object]) -> list[object]:
        candidates = [
            workline
            for workline in candidate_worklines
            if getattr(workline, "plugin_key", None) == SMT_SORTING_INBOUND_PLUGIN_KEY
            and bool(getattr(workline, "is_active", True))
            and self._route_enabled(workline)
        ]
        return sorted(
            candidates,
            key=lambda workline: (
                self._route_priority(workline),
                str(getattr(workline, "line_code", "")),
                int(getattr(workline, "id", 0) or 0),
            ),
        )

    async def _has_open_current_material(self, db: AsyncSession, *, workline_id: int) -> bool:
        sessions = await self._session_repository().list_open_by_workline_id(db, workline_id=workline_id, limit=50)
        for session in sessions:
            context = getattr(session, "context_json", None)
            if not isinstance(context, dict):
                continue
            sorting = context.get("sorting")
            if isinstance(sorting, dict) and isinstance(sorting.get("current_material"), dict):
                return True
        return False

    def _manual_hold(
        self,
        code: SmtInboundHandoffReasonCode,
        evidence: dict[str, Any],
    ) -> SmtInboundHandoffRouteResult:
        reason = self.reason_catalog.get(code)
        return SmtInboundHandoffRouteResult(
            kind="MANUAL_HOLD",
            manual_hold=True,
            failure_code=reason.failure_code,
            failure_message=reason.default_message,
            route_evidence=evidence,
        )

    def _station_lease_service(self) -> object:
        if self.station_lease_service is None:
            from src.app.workline.services.station_lease_service import workline_station_lease_service

            self.station_lease_service = workline_station_lease_service
        return self.station_lease_service

    def _session_repository(self) -> object:
        if self.session_repository is None:
            from src.app.workline.repositories.session_repository import workline_session_repository

            self.session_repository = workline_session_repository
        return self.session_repository

    def _retry(
        self,
        code: SmtInboundHandoffReasonCode,
        evidence: dict[str, Any],
    ) -> SmtInboundHandoffRouteResult:
        reason = self.reason_catalog.get(code)
        return SmtInboundHandoffRouteResult(
            kind="RETRY",
            retryable=True,
            failure_code=reason.failure_code,
            failure_message=reason.default_message,
            next_attempt_at=timezone.now_for_db() + timedelta(seconds=self.retry_delay_seconds),
            route_evidence=evidence,
        )

    def _candidate_order_item(self, workline: object) -> dict[str, Any]:
        return {
            "priority": self._route_priority(workline),
            "workline_code": getattr(workline, "line_code", None),
            "workline_id": getattr(workline, "id", None),
        }

    @classmethod
    def _route_enabled(cls, workline: object) -> bool:
        return bool(cls._route_config(workline).get("enabled", False))

    @classmethod
    def _route_priority(cls, workline: object) -> int:
        try:
            return int(cls._route_config(workline).get("priority", 100))
        except (TypeError, ValueError):
            return 100

    @staticmethod
    def _route_config(workline: object) -> dict[str, Any]:
        for attr_name in ("runtime_config_json", "config"):
            raw = getattr(workline, attr_name, None)
            if not isinstance(raw, dict):
                continue
            route_config = raw.get("smt_inbound_handoff_route")
            if isinstance(route_config, dict):
                return dict(route_config)
        return {}

    @staticmethod
    def _source_station_code(route_config: dict[str, Any]) -> str | None:
        value = route_config.get("source_station_code") or route_config.get("station_code")
        return str(value).strip() if value is not None and str(value).strip() else None

    @classmethod
    def _source_position_code(cls, route_config: dict[str, Any]) -> str | None:
        value = route_config.get("source_position_code") or route_config.get("position_code")
        if value is not None and str(value).strip():
            return str(value).strip()
        return cls._source_station_code(route_config)


async def _allow_idle_ecs_probe(db: AsyncSession, *, workline: object, route: object) -> object:
    """默认 ECS probe：当前阶段只提供可注入准入点。"""

    _ = db, workline, route
    return _ProbeResult()


@dataclass(frozen=True)
class _ProbeResult:
    available: bool = True
    reason_code: str | None = None


smt_inbound_handoff_route_service = SmtInboundHandoffRouteService()


__all__ = [
    "SmtInboundHandoffRouteResult",
    "SmtInboundHandoffRouteService",
    "smt_inbound_handoff_route_service",
]
