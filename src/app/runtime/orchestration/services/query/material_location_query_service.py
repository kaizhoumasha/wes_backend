"""MaterialLocationQuery Phase4 read model service."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from src.app.resource.repositories import (
    BinCellOccupancyRepository,
    RackBinMountRepository,
    bin_cell_occupancy_repository,
    rack_bin_mount_repository,
)
from src.app.runtime.orchestration.models.bin_cell_reservation import (
    BinCellReservationStatus,
    WorklineBinCellReservation,
)
from src.app.runtime.orchestration.repositories.bin_cell_reservation_repository import (
    WorklineBinCellReservationRepository,
    workline_bin_cell_reservation_repository,
)
from src.app.runtime.orchestration.services.query.active_object_fact_provider import (
    runtime_active_object_fact_provider,
)
from src.app.runtime.orchestration.services.runtime_location_event_service import (
    RuntimeLocationEventService,
    runtime_location_event_service,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class MaterialLocationConflictState(str, Enum):
    """MaterialLocationQuery 冲突状态。"""

    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    RECONCILING = "RECONCILING"
    WMS_UNAVAILABLE = "WMS_UNAVAILABLE"


class MaterialLocationEvidence(BaseModel):
    """单个位置来源 evidence。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: str
    priority: int
    object_type: str
    object_key: str
    location_scope: str | None = None
    location_code: str | None = None
    semantic_status: str | None = None
    evidence_ref: str | None = None
    evidence_json: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None
    provider_code: str | None = None
    source_event_id: str | None = None
    source_version: str | None = None
    external_reference: str | None = None
    observed_at: datetime | None = None


class MaterialLocationResult(BaseModel):
    """统一位置查询结果。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    query_entry: str
    conflict_state: MaterialLocationConflictState
    object_type: str | None = None
    object_key: str | None = None
    location_scope: str | None = None
    location_code: str | None = None
    source: str | None = None
    correlation_id: str | None = None
    evidence: list[MaterialLocationEvidence] = Field(default_factory=list)


class MaterialLocationQueryService:
    """作业期位置查询只读聚合服务。"""

    def __init__(
        self,
        *,
        location_event_service: RuntimeLocationEventService | Any = runtime_location_event_service,
        active_fact_provider: Any = runtime_active_object_fact_provider,
        reservation_repository: WorklineBinCellReservationRepository = workline_bin_cell_reservation_repository,
        occupancy_repository: BinCellOccupancyRepository = bin_cell_occupancy_repository,
        rack_bin_mount_repository: RackBinMountRepository = rack_bin_mount_repository,
        wms_snapshot_provider: Any | None = None,
        legacy_evidence_provider: Any | None = None,
    ) -> None:
        self.location_event_service = location_event_service
        self.active_fact_provider = active_fact_provider
        self.reservation_repository = reservation_repository
        self.occupancy_repository = occupancy_repository
        self.rack_bin_mount_repository = rack_bin_mount_repository
        self.wms_snapshot_provider = wms_snapshot_provider
        self.legacy_evidence_provider = legacy_evidence_provider

    async def query_by_material_identity(
        self,
        db: AsyncSession,
        *,
        material_identity_key: str,
    ) -> MaterialLocationResult:
        """按 material identity 查询物料位置。"""

        evidence: list[MaterialLocationEvidence] = []
        occupancies = await self.occupancy_repository.list_active_by_material_identity(db, material_identity_key)
        evidence.extend(_evidence_from_occupancy(occupancy) for occupancy in occupancies)
        return self._resolve("by material identity", evidence)

    async def query_by_package_or_bin(
        self,
        db: AsyncSession,
        *,
        package_id: str | None = None,
        bin_code: str | None = None,
    ) -> MaterialLocationResult:
        """按 package_id 或 bin_code 查询位置。"""

        evidence: list[MaterialLocationEvidence] = []
        if package_id:
            events = await self.location_event_service.list_by_object(db, object_type="PKG", object_key=package_id)
            evidence.extend(_evidence_from_location_event(event) for event in events)
            reservations = await self.reservation_repository.list_active_or_frozen_by_pkg_codes(db, [package_id])
            evidence.extend(_evidence_from_reservation(reservation) for reservation in reservations)
            active_facts = await self.active_fact_provider.list_material_location_facts(
                db,
                object_type="PKG",
                object_key=package_id,
                workline_id=None,
            )
            evidence.extend(
                _evidence_from_active_fact(fact, object_type="PKG", object_key=package_id) for fact in active_facts
            )
        if bin_code:
            occupancies = await self.occupancy_repository.list_active_by_bin_codes(db, [bin_code])
            reservations = await self.reservation_repository.list_active_or_frozen_by_bin_codes(db, [bin_code])
            evidence.extend(_evidence_from_occupancy(occupancy) for occupancy in occupancies)
            evidence.extend(_evidence_from_reservation(reservation) for reservation in reservations)
        return self._resolve("by package or bin", evidence)

    async def query_by_rack_and_side(
        self,
        db: AsyncSession,
        *,
        rack_code: str,
        rack_side: str,
    ) -> MaterialLocationResult:
        """按 rack/side 查询当前货架面的本地挂载位置 evidence。"""

        normalized_side = _normalize_rack_side(rack_side)
        mounts = await self.rack_bin_mount_repository.list_active_by_rack_code(db, rack_code)
        evidence = [
            _evidence_from_rack_bin_mount(mount, rack_side=normalized_side)
            for mount in mounts
            if _rack_slot_side(getattr(mount, "rack_slot_code", None)) == normalized_side
        ]
        return self._resolve(f"by rack and side:{rack_code}:{normalized_side}", evidence)

    async def query_by_workline_active_object(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        object_type: str,
        object_key: str,
    ) -> MaterialLocationResult:
        """按 WorkLine active object 查询位置。"""

        active_facts = await self.active_fact_provider.list_material_location_facts(
            db,
            object_type=object_type,
            object_key=object_key,
            workline_id=workline_id,
        )
        evidence = [
            _evidence_from_active_fact(fact, object_type=object_type, object_key=object_key) for fact in active_facts
        ]
        return self._resolve("by workline active object", evidence)

    async def query_by_external_reference(
        self,
        db: AsyncSession,
        *,
        external_reference_type: str,
        external_reference_value: str,
        provider_code: str | None = None,
    ) -> MaterialLocationResult:
        """按 ExternalReference 反查本地位置 evidence。"""

        events = await self.location_event_service.list_by_external_reference(
            db,
            external_reference_type=external_reference_type,
            external_reference_value=external_reference_value,
            provider_code=provider_code,
        )
        return self._resolve("by ExternalReference", [_evidence_from_location_event(event) for event in events])

    async def query_by_correlation_id(
        self,
        db: AsyncSession,
        *,
        correlation_id: str,
    ) -> MaterialLocationResult:
        """按 correlation_id 查询位置。"""

        events = await self.location_event_service.list_by_correlation_id(db, correlation_id=correlation_id)
        return self._resolve("by correlation_id", [_evidence_from_location_event(event) for event in events])

    def _resolve(self, query_entry: str, evidence: list[MaterialLocationEvidence]) -> MaterialLocationResult:
        sorted_evidence = sorted(evidence, key=lambda item: (item.priority, item.observed_at is None, item.observed_at))
        if not sorted_evidence:
            return MaterialLocationResult(
                query_entry=query_entry,
                conflict_state=MaterialLocationConflictState.NOT_FOUND,
                evidence=[],
            )

        authoritative = [item for item in sorted_evidence if item.priority <= 3 and item.location_code]
        reconciling = [item for item in authoritative if item.semantic_status == "RECONCILING"]
        if reconciling:
            first = reconciling[0]
            return MaterialLocationResult(
                query_entry=query_entry,
                conflict_state=MaterialLocationConflictState.RECONCILING,
                object_type=first.object_type,
                object_key=first.object_key,
                correlation_id=first.correlation_id,
                evidence=sorted_evidence,
            )
        authoritative_locations = {(item.location_scope, item.location_code) for item in authoritative}
        if len(authoritative_locations) > 1:
            first = sorted_evidence[0]
            return MaterialLocationResult(
                query_entry=query_entry,
                conflict_state=MaterialLocationConflictState.RECONCILING,
                object_type=first.object_type,
                object_key=first.object_key,
                correlation_id=first.correlation_id,
                evidence=sorted_evidence,
            )

        primary = sorted_evidence[0]
        return MaterialLocationResult(
            query_entry=query_entry,
            conflict_state=MaterialLocationConflictState.OK,
            object_type=primary.object_type,
            object_key=primary.object_key,
            location_scope=primary.location_scope,
            location_code=primary.location_code,
            source=primary.source,
            correlation_id=primary.correlation_id,
            evidence=sorted_evidence,
        )


def _evidence_from_location_event(event: Any) -> MaterialLocationEvidence:
    return MaterialLocationEvidence(
        source="LOCAL_PHYSICAL_FACT",
        priority=1,
        object_type=str(event.object_type),
        object_key=str(event.object_key),
        location_scope=event.location_scope,
        location_code=event.location_code,
        evidence_json=dict(event.evidence_json or {}),
        correlation_id=event.correlation_id,
        provider_code=event.provider_code,
        source_event_id=event.source_event_id,
        source_version=event.source_version,
        external_reference=_external_reference(event.external_reference_type, event.external_reference_value),
        observed_at=event.occurred_at,
    )


def _evidence_from_active_fact(fact: Any, *, object_type: str, object_key: str) -> MaterialLocationEvidence:
    return MaterialLocationEvidence(
        source="ACTIVE_OBJECT",
        priority=2,
        object_type=str(_read_attr(fact, "object_type", object_type)),
        object_key=str(_read_attr(fact, "object_key", object_key)),
        location_scope=_read_attr(fact, "location_scope", None),
        location_code=_read_attr(fact, "location_code", None),
        evidence_ref=_read_attr(fact, "evidence_ref", None),
        evidence_json=dict(_read_attr(fact, "evidence_json", {}) or {}),
        observed_at=_read_attr(fact, "observed_at", None),
    )


def _evidence_from_reservation(reservation: WorklineBinCellReservation | Any) -> MaterialLocationEvidence:
    bin_cell_code = getattr(reservation, "bin_cell_code", None) or reservation.bin_cell_index
    return MaterialLocationEvidence(
        source="CELL_RESERVATION",
        priority=3,
        object_type="PKG",
        object_key=str(reservation.pkg_code),
        location_scope="BIN_CELL",
        location_code=f"{reservation.bin_code}:{bin_cell_code}",
        semantic_status=_reservation_semantic_status(reservation),
        evidence_ref=f"cell_reservation:{getattr(reservation, 'id', None)}",
        evidence_json=dict(getattr(reservation, "metadata_json", None) or {}),
        source_event_id=getattr(reservation, "source_event_id", None),
        observed_at=getattr(reservation, "reserved_at", None),
    )


def _evidence_from_occupancy(occupancy: Any) -> MaterialLocationEvidence:
    return MaterialLocationEvidence(
        source="LOCAL_PHYSICAL_FACT",
        priority=1,
        object_type="MATERIAL",
        object_key=str(getattr(occupancy, "material_identity_key", "")),
        location_scope="BIN_CELL",
        location_code=f"{occupancy.bin_code}:{occupancy.bin_cell_index}",
        semantic_status="OCCUPIED",
        evidence_ref=f"bin_cell_occupancy:{getattr(occupancy, 'id', None)}",
        evidence_json=dict(getattr(occupancy, "metadata_json", None) or {}),
        provider_code=getattr(occupancy, "source_system", None),
        source_event_id=getattr(occupancy, "source_event_id", None),
        source_version=getattr(occupancy, "source_version", None),
        observed_at=getattr(occupancy, "started_at", None),
    )


def _evidence_from_rack_bin_mount(mount: Any, *, rack_side: str) -> MaterialLocationEvidence:
    rack_code = str(getattr(mount, "rack_code", ""))
    rack_slot_code = str(getattr(mount, "rack_slot_code", ""))
    return MaterialLocationEvidence(
        source="LOCAL_PHYSICAL_FACT",
        priority=1,
        object_type="BIN",
        object_key=str(getattr(mount, "bin_code", "")),
        location_scope="RACK_SIDE",
        location_code=f"{rack_code}:{rack_side}",
        semantic_status="MOUNTED",
        evidence_ref=f"rack_bin_mount:{getattr(mount, 'id', None)}",
        evidence_json={
            "rack_code": rack_code,
            "rack_side": rack_side,
            "rack_slot_code": rack_slot_code,
        },
        provider_code=_enum_value(getattr(mount, "source_system", None)),
        source_event_id=getattr(mount, "source_event_id", None),
        source_version=getattr(mount, "source_version", None),
        observed_at=getattr(mount, "started_at", None),
    )


def _reservation_semantic_status(reservation: Any) -> str:
    status = getattr(reservation, "reservation_status", None)
    if status == BinCellReservationStatus.PLANNED:
        return "RESERVED"
    if status == BinCellReservationStatus.CONSUMED:
        return "OCCUPIED"
    if status == BinCellReservationStatus.RECONCILING:
        return "RECONCILING"
    return "RELEASED"


_RACK_SIDE_ALIASES = {
    "0": "0",
    "A": "0",
    "B": "0",
    "L": "0",
    "LEFT": "0",
    "FRONT": "0",
    "1": "1",
    "C": "1",
    "D": "1",
    "R": "1",
    "RIGHT": "1",
    "BACK": "1",
}

_RACK_SLOT_SIDE_BY_CODE = {"A": "0", "B": "0", "C": "1", "D": "1"}


def _normalize_rack_side(value: str) -> str:
    normalized = value.strip().upper()
    return _RACK_SIDE_ALIASES.get(normalized, normalized)


def _rack_slot_side(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().upper()
    if not normalized:
        return None
    return _RACK_SLOT_SIDE_BY_CODE.get(normalized) or _RACK_SLOT_SIDE_BY_CODE.get(normalized[:1])


def _external_reference(reference_type: str | None, reference_value: str | None) -> str | None:
    if not reference_type or not reference_value:
        return None
    return f"{reference_type}:{reference_value}"


def _read_attr(value: Any, name: str, default: Any) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


material_location_query_service = MaterialLocationQueryService()


__all__ = [
    "MaterialLocationConflictState",
    "MaterialLocationEvidence",
    "MaterialLocationQueryService",
    "MaterialLocationResult",
    "material_location_query_service",
]
