"""SMT 入库 handoff 目标 WorkLine 路由服务。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from src.app.resource.models import RackKind
from src.app.runtime.capability_catalog import get_workline_capability_definition
from src.app.runtime.orchestration.services.workline_runtime_status_projection_service import (
    workline_runtime_status_projection_service,
)
from src.app.workline.domain.contracts.smt_sorting_inbound import (
    COMMAND_SOURCE_PICK,
    SMT_SORTING_INBOUND_PLUGIN_KEY,
)
from src.app.workline.domain.services.smt_inbound_handoff_reason import (
    SMT_INBOUND_HANDOFF_REASON_CATALOG,
    SmtInboundHandoffReasonCatalog,
    SmtInboundHandoffReasonCode,
)
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession


class EcsStatusProbe(Protocol):
    """ECS 状态探测依赖。"""

    async def __call__(self, db: AsyncSession, /, *, workline: object, route: object) -> object: ...


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


@dataclass(frozen=True)
class _BoundaryResolution:
    contract_version: str | None
    source_boundary: object
    target_boundary: object

    @property
    def source_rack_position_code(self) -> str:
        return str(self.source_boundary.rack_position_code)

    @property
    def target_rack_position_code(self) -> str:
        return str(self.target_boundary.rack_position_code)

    @property
    def source_station_code(self) -> str:
        return self.source_rack_position_code


class SmtInboundHandoffRouteService:
    """选择并准入可承接 SMT inbound handoff 的分拣 WorkLine。"""

    def __init__(
        self,
        *,
        station_lease_service: object | None = None,
        session_repository: object | None = None,
        ecs_status_probe: EcsStatusProbe | None = None,
        reason_catalog: SmtInboundHandoffReasonCatalog = SMT_INBOUND_HANDOFF_REASON_CATALOG,
        retry_delay_seconds: int = 30,
    ) -> None:
        self.station_lease_service = station_lease_service
        self.session_repository = session_repository
        self.ecs_status_probe = ecs_status_probe or _real_ecs_status_probe
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

        _ = demand, source_item
        ordered_candidates = self._ordered_config_candidates(candidate_worklines)
        candidate_order = [self._candidate_order_item(workline) for workline in ordered_candidates]
        if not ordered_candidates:
            return self._manual_hold(SmtInboundHandoffReasonCode.ROUTE_NOT_FOUND, {"candidate_order": candidate_order})

        workline = ordered_candidates[0]
        route_config = self._route_config(workline)
        boundary_resolution = _resolve_boundary_selection(workline, route_config)
        route_evidence = {
            "candidate_order": candidate_order,
            "route_priority": self._route_priority(workline),
            **boundary_resolution.evidence,
        }
        if boundary_resolution.failure_code is not None:
            return self._manual_hold(boundary_resolution.failure_code, route_evidence)

        if not workline_runtime_status_projection_service.is_ready(workline):
            return self._retry(SmtInboundHandoffReasonCode.TARGET_WORKLINE_NOT_READY, route_evidence)

        lease_result = await self._station_lease_service().get_station_lease_status(
            db,
            workline_id=getattr(workline, "id", None),
            workline_code=getattr(workline, "line_code", None),
            position_code=route_evidence["source_rack_position_code"],
            rack_kind=RackKind.SINGLE_LAYER,
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
            from src.app.runtime.capabilities.phase4.station_lease_service import workline_station_lease_service

            self.station_lease_service = workline_station_lease_service
        return self.station_lease_service

    def _session_repository(self) -> object:
        if self.session_repository is None:
            from src.app.runtime.orchestration.repositories.session_repository import workline_session_repository

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
        value = (
            route_config.get("source_rack_position_code")
            or route_config.get("source_position_code")
            or route_config.get("position_code")
        )
        if value is not None and str(value).strip():
            return str(value).strip()
        return cls._source_station_code(route_config)


@dataclass(frozen=True)
class _BoundaryResolutionResult:
    failure_code: SmtInboundHandoffReasonCode | None
    evidence: dict[str, Any]


def _boundary_to_evidence(boundary: object) -> dict[str, Any]:
    return {
        "rack_position_code": getattr(boundary, "rack_position_code", None),
        "rack_kind": getattr(boundary, "rack_kind", None),
        "business_demand_type": getattr(boundary, "business_demand_type", None),
        "wms_operation_type": getattr(boundary, "wms_operation_type", None),
        "snapshot_kind": getattr(boundary, "snapshot_kind", None),
        "lease_scope": getattr(boundary, "lease_scope", None),
    }


def _matching_boundaries(manifest: object, *, business_demand_type: str, rack_kind: str) -> list[object]:
    return [
        boundary
        for boundary in getattr(manifest, "resource_boundaries", ()) or ()
        if getattr(boundary, "business_demand_type", None) == business_demand_type
        and getattr(boundary, "rack_kind", None) == rack_kind
        and isinstance(getattr(boundary, "rack_position_code", None), str)
        and bool(str(boundary.rack_position_code).strip())
    ]


def _manifest_boundary_evidence(
    *,
    contract_version: str | None,
    source_boundaries: list[object],
    target_boundaries: list[object],
) -> dict[str, Any]:
    return {
        "manifest_contract_version": contract_version,
        "manifest_source_boundaries": [_boundary_to_evidence(boundary) for boundary in source_boundaries],
        "manifest_target_boundaries": [_boundary_to_evidence(boundary) for boundary in target_boundaries],
    }


def _selected_boundary_evidence(resolution: _BoundaryResolution) -> dict[str, Any]:
    return {
        "manifest_contract_version": resolution.contract_version,
        "source_rack_position_code": resolution.source_rack_position_code,
        "source_station_code": resolution.source_station_code,
        "source_position_code": resolution.source_rack_position_code,
        "target_rack_position_code": resolution.target_rack_position_code,
        "source_boundary": _boundary_to_evidence(resolution.source_boundary),
    }


def _resolve_configured_source_boundary(route_config: dict[str, Any]) -> str | None:
    return SmtInboundHandoffRouteService._source_position_code(route_config)


def _source_boundary_by_position(boundaries: list[object]) -> dict[str, object]:
    return {str(boundary.rack_position_code).strip(): boundary for boundary in boundaries}


def _resolve_boundary_selection(workline: object, route_config: dict[str, Any]) -> _BoundaryResolutionResult:
    definition = get_workline_capability_definition(getattr(workline, "plugin_key", None))
    manifest = getattr(definition, "manifest", None)
    if manifest is None:
        return _BoundaryResolutionResult(
            failure_code=SmtInboundHandoffReasonCode.PLUGIN_CONTRACT_INVALID,
            evidence={"manifest_contract_version": None},
        )

    contract_version = getattr(manifest, "contract_version", None)
    source_boundaries = _matching_boundaries(
        manifest,
        business_demand_type="SORTING_INBOUND_SOURCE",
        rack_kind="SINGLE_LAYER",
    )
    target_boundaries = _matching_boundaries(
        manifest,
        business_demand_type="SORTING_INBOUND_TARGET",
        rack_kind="FIVE_LAYER",
    )
    boundary_evidence = _manifest_boundary_evidence(
        contract_version=contract_version,
        source_boundaries=source_boundaries,
        target_boundaries=target_boundaries,
    )
    if not source_boundaries or len(target_boundaries) != 1:
        return _BoundaryResolutionResult(
            failure_code=SmtInboundHandoffReasonCode.PLUGIN_CONTRACT_INVALID,
            evidence=boundary_evidence,
        )

    configured_source = _resolve_configured_source_boundary(route_config)
    source_by_position = _source_boundary_by_position(source_boundaries)
    if configured_source is not None:
        source_boundary = source_by_position.get(configured_source)
        if source_boundary is None:
            return _BoundaryResolutionResult(
                failure_code=SmtInboundHandoffReasonCode.SOURCE_BOUNDARY_INVALID,
                evidence={
                    **boundary_evidence,
                    "configured_source_rack_position_code": configured_source,
                },
            )
    elif len(source_boundaries) == 1:
        source_boundary = source_boundaries[0]
    else:
        return _BoundaryResolutionResult(
            failure_code=SmtInboundHandoffReasonCode.SOURCE_BOUNDARY_AMBIGUOUS,
            evidence=boundary_evidence,
        )

    selected = _BoundaryResolution(
        contract_version=str(contract_version) if contract_version is not None else None,
        source_boundary=source_boundary,
        target_boundary=target_boundaries[0],
    )
    return _BoundaryResolutionResult(failure_code=None, evidence=_selected_boundary_evidence(selected))


async def _real_ecs_status_probe(db: AsyncSession, *, workline: object, route: object) -> object:
    """默认 ECS probe：复用设备命令派发前的实时状态预检，缺设备/配置时失败关闭。"""

    workline_id = getattr(workline, "id", None)
    if not isinstance(workline_id, int):
        return _ProbeResult(available=False, reason_code="WORKLINE_ID_MISSING")

    from src.app.device.repositories.device_repository import device_repository
    from src.app.runtime.orchestration.services.device_command_gateway import _ensure_realtime_device_status_ready

    try:
        devices = await device_repository.get_by_work_line_id(db, workline_id)
    except Exception as exc:
        return _ProbeResult(available=False, reason_code=getattr(exc, "code", type(exc).__name__))
    source_pick_role = _source_pick_device_role(workline)
    source_devices = [
        device
        for device in devices
        if source_pick_role is not None and getattr(device, "device_role", None) == source_pick_role
    ]
    if len(source_devices) != 1:
        return _ProbeResult(available=False, reason_code="ECS_SOURCE_DEVICE_UNAVAILABLE")

    device = source_devices[0]
    device_code = getattr(device, "device_code", None)
    if not device_code or not getattr(device, "host", None) or not getattr(device, "port", None):
        return _ProbeResult(available=False, reason_code="ECS_SOURCE_DEVICE_CONFIG_INVALID")

    try:
        import httpx

        async with httpx.AsyncClient() as client:
            await _ensure_realtime_device_status_ready(client=client, device=device, device_code=str(device_code))
    except Exception as exc:
        return _ProbeResult(available=False, reason_code=getattr(exc, "code", type(exc).__name__))
    return _ProbeResult()


def _source_pick_device_role(workline: object) -> str | None:
    definition = get_workline_capability_definition(getattr(workline, "plugin_key", None))
    manifest = getattr(definition, "manifest", None)
    for command in getattr(manifest, "commands", ()) or ():
        if getattr(command, "command", None) == COMMAND_SOURCE_PICK:
            target_role = getattr(command, "target_device_role", None)
            return str(target_role) if target_role else None
    return None


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
