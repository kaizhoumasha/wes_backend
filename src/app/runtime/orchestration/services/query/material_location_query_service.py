"""MaterialLocationQuery Phase4 read model service."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import Enum
from inspect import isawaitable
from typing import TYPE_CHECKING, Any

import httpx
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
        evidence.extend(
            await self._list_provider_evidence(
                db,
                query_entry="by material identity",
                material_identity_key=material_identity_key,
            )
        )
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
        evidence.extend(
            await self._list_provider_evidence(
                db,
                query_entry="by package or bin",
                package_id=package_id,
                bin_code=bin_code,
            )
        )
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
        evidence.extend(
            await self._list_provider_evidence(
                db,
                query_entry=f"by rack and side:{rack_code}:{normalized_side}",
                rack_code=rack_code,
                rack_side=normalized_side,
            )
        )
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

        normalized_object_type = object_type.strip().upper()
        normalized_object_key = object_key.strip()
        evidence: list[MaterialLocationEvidence] = []
        if normalized_object_type == "PKG":
            events = await self.location_event_service.list_by_object(
                db,
                object_type=normalized_object_type,
                object_key=normalized_object_key,
            )
            reservations = await self.reservation_repository.list_active_or_frozen_by_pkg_codes(
                db,
                [normalized_object_key],
            )
            evidence.extend(_evidence_from_location_event(event) for event in events)
            evidence.extend(_evidence_from_reservation(reservation) for reservation in reservations)
        elif normalized_object_type == "BIN":
            events = await self.location_event_service.list_by_object(
                db,
                object_type=normalized_object_type,
                object_key=normalized_object_key,
            )
            occupancies = await self.occupancy_repository.list_active_by_bin_codes(db, [normalized_object_key])
            reservations = await self.reservation_repository.list_active_or_frozen_by_bin_codes(
                db,
                [normalized_object_key],
            )
            evidence.extend(_evidence_from_location_event(event) for event in events)
            evidence.extend(_evidence_from_occupancy(occupancy) for occupancy in occupancies)
            evidence.extend(_evidence_from_reservation(reservation) for reservation in reservations)

        active_facts = await self.active_fact_provider.list_material_location_facts(
            db,
            object_type=normalized_object_type,
            object_key=normalized_object_key,
            workline_id=workline_id,
        )
        evidence.extend(
            _evidence_from_active_fact(fact, object_type=normalized_object_type, object_key=normalized_object_key)
            for fact in active_facts
        )
        provider_criteria: dict[str, Any] = {
            "workline_id": workline_id,
            "object_type": normalized_object_type,
            "object_key": normalized_object_key,
        }
        if normalized_object_type == "PKG":
            provider_criteria["package_id"] = normalized_object_key
        elif normalized_object_type == "BIN":
            provider_criteria["bin_code"] = normalized_object_key
        evidence.extend(
            await self._list_provider_evidence(
                db,
                query_entry="by workline active object",
                **provider_criteria,
            )
        )
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
        evidence = [_evidence_from_location_event(event) for event in events]
        evidence.extend(
            await self._list_provider_evidence(
                db,
                query_entry="by ExternalReference",
                external_reference_type=external_reference_type,
                external_reference_value=external_reference_value,
                provider_code=provider_code,
            )
        )
        return self._resolve("by ExternalReference", evidence)

    async def query_by_correlation_id(
        self,
        db: AsyncSession,
        *,
        correlation_id: str,
    ) -> MaterialLocationResult:
        """按 correlation_id 查询位置。"""

        events = await self.location_event_service.list_by_correlation_id(db, correlation_id=correlation_id)
        evidence = [_evidence_from_location_event(event) for event in events]
        evidence.extend(
            await self._list_provider_evidence(
                db,
                query_entry="by correlation_id",
                correlation_id=correlation_id,
            )
        )
        return self._resolve("by correlation_id", evidence)

    async def _list_provider_evidence(
        self,
        db: AsyncSession,
        *,
        query_entry: str,
        **criteria: Any,
    ) -> list[MaterialLocationEvidence]:
        """读取 Phase4 #4/#5 外部只读 evidence；provider 缺省时保持本地查询纯只读。"""

        evidence: list[MaterialLocationEvidence] = []
        evidence.extend(
            await _list_optional_provider_evidence(
                self.wms_snapshot_provider,
                db,
                query_entry=query_entry,
                default_source="WMS_RECONCILIATION_SNAPSHOT",
                default_priority=4,
                timeout_source="WMS_UNAVAILABLE",
                **criteria,
            )
        )
        evidence.extend(
            await _list_optional_provider_evidence(
                self.legacy_evidence_provider,
                db,
                query_entry=query_entry,
                default_source="LEGACY_CHARACTERIZATION",
                default_priority=5,
                timeout_source=None,
                **criteria,
            )
        )
        return evidence

    def _resolve(self, query_entry: str, evidence: list[MaterialLocationEvidence]) -> MaterialLocationResult:
        sorted_evidence = _sort_evidence(evidence)
        if not sorted_evidence:
            return MaterialLocationResult(
                query_entry=query_entry,
                conflict_state=MaterialLocationConflictState.NOT_FOUND,
                evidence=[],
            )

        location_evidence = [item for item in sorted_evidence if item.location_code]
        if not location_evidence and any(item.source == "WMS_UNAVAILABLE" for item in sorted_evidence):
            first = sorted_evidence[0]
            return MaterialLocationResult(
                query_entry=query_entry,
                conflict_state=MaterialLocationConflictState.WMS_UNAVAILABLE,
                object_type=first.object_type,
                object_key=first.object_key,
                evidence=sorted_evidence,
            )

        current_evidence = _current_authoritative_evidence(sorted_evidence)
        authoritative = [item for item in current_evidence if item.priority <= 3 and item.location_code]
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

        primary = location_evidence[0] if location_evidence else sorted_evidence[0]
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
    event_ref = _runtime_location_event_ref(event)
    return MaterialLocationEvidence(
        source="LOCAL_PHYSICAL_FACT",
        priority=1,
        object_type=str(event.object_type),
        object_key=str(event.object_key),
        location_scope=event.location_scope,
        location_code=event.location_code,
        evidence_json=dict(event.evidence_json or {}),
        evidence_ref=event_ref,
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
    evidence_json = dict(getattr(reservation, "metadata_json", None) or {})
    evidence_json.update(dict(getattr(reservation, "evidence_json", None) or {}))
    return MaterialLocationEvidence(
        source="CELL_RESERVATION",
        priority=3,
        object_type="PKG",
        object_key=str(reservation.pkg_code),
        location_scope="BIN_CELL",
        location_code=f"{reservation.bin_code}:{bin_cell_code}",
        semantic_status=_reservation_semantic_status(reservation),
        evidence_ref=f"cell_reservation:{getattr(reservation, 'id', None)}",
        evidence_json=evidence_json,
        correlation_id=getattr(reservation, "correlation_id", None),
        provider_code=evidence_json.get("provider_code"),
        source_event_id=getattr(reservation, "source_event_id", None),
        source_version=evidence_json.get("source_version"),
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


def _runtime_location_event_ref(event: Any) -> str | None:
    event_id = getattr(event, "id", None)
    if event_id is not None:
        return f"runtime_location_event:{event_id}"
    source_event_id = getattr(event, "source_event_id", None)
    if source_event_id:
        return f"runtime_location_event:{source_event_id}"
    return None


def _sort_evidence(evidence: list[MaterialLocationEvidence]) -> list[MaterialLocationEvidence]:
    latest_first = sorted(evidence, key=_evidence_recency_sort_key, reverse=True)
    return sorted(latest_first, key=lambda item: item.priority)


def _evidence_recency_sort_key(item: MaterialLocationEvidence) -> tuple[str, int]:
    return (_observed_at_sort_value(item.observed_at), _runtime_location_event_sort_sequence(item))


def _observed_at_sort_value(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.isoformat()


def _runtime_location_event_sort_sequence(item: MaterialLocationEvidence) -> int:
    if not item.evidence_ref or not item.evidence_ref.startswith("runtime_location_event:"):
        return 0
    raw_sequence = item.evidence_ref.removeprefix("runtime_location_event:")
    try:
        return int(raw_sequence)
    except ValueError:
        return 0


def _current_authoritative_evidence(
    evidence: list[MaterialLocationEvidence],
) -> list[MaterialLocationEvidence]:
    current: list[MaterialLocationEvidence] = []
    seen_runtime_location_objects: set[tuple[int, str, str, str]] = set()
    for item in evidence:
        if _is_runtime_location_event_evidence(item):
            key = (item.priority, item.source, item.object_type, item.object_key)
            if key in seen_runtime_location_objects:
                continue
            seen_runtime_location_objects.add(key)
        current.append(item)
    return current


def _is_runtime_location_event_evidence(item: MaterialLocationEvidence) -> bool:
    return bool(item.evidence_ref and item.evidence_ref.startswith("runtime_location_event:"))


async def _list_optional_provider_evidence(
    provider: Any | None,
    db: Any,
    *,
    query_entry: str,
    default_source: str,
    default_priority: int,
    timeout_source: str | None,
    **criteria: Any,
) -> list[MaterialLocationEvidence]:
    if provider is None:
        return []
    list_evidence = getattr(provider, "list_evidence", None)
    if list_evidence is None:
        return []
    try:
        raw_items = list_evidence(db, query_entry=query_entry, **criteria)
        if isawaitable(raw_items):
            raw_items = await raw_items
    except Exception as exc:
        if not _is_timeout_exception(exc):
            raise
        if timeout_source is None:
            raise
        object_type, object_key = _object_identity_from_criteria(criteria)
        return [
            MaterialLocationEvidence(
                source=timeout_source,
                priority=default_priority,
                object_type=object_type,
                object_key=object_key,
                semantic_status="UNAVAILABLE",
                evidence_json={"reason_code": timeout_source, "query_entry": query_entry},
            )
        ]
    return [
        _coerce_provider_evidence(
            item,
            default_source=default_source,
            default_priority=default_priority,
            criteria=criteria,
        )
        for item in list(raw_items or [])
        if item is not None
    ]


def _coerce_provider_evidence(
    item: Any,
    *,
    default_source: str,
    default_priority: int,
    criteria: dict[str, Any],
) -> MaterialLocationEvidence:
    if isinstance(item, MaterialLocationEvidence):
        return item.model_copy(update={"priority": default_priority})
    object_type, object_key = _object_identity_from_criteria(criteria)
    return MaterialLocationEvidence(
        source=str(_read_attr(item, "source", default_source)),
        priority=default_priority,
        object_type=str(_read_attr(item, "object_type", object_type)),
        object_key=str(_read_attr(item, "object_key", object_key)),
        location_scope=_read_attr(item, "location_scope", None),
        location_code=_read_attr(item, "location_code", None),
        semantic_status=_read_attr(item, "semantic_status", None),
        evidence_ref=_read_attr(item, "evidence_ref", None),
        evidence_json=dict(_read_attr(item, "evidence_json", {}) or {}),
        correlation_id=_read_attr(item, "correlation_id", None),
        provider_code=_read_attr(item, "provider_code", None),
        source_event_id=_read_attr(item, "source_event_id", None),
        source_version=_read_attr(item, "source_version", None),
        external_reference=_read_attr(item, "external_reference", None),
        observed_at=_read_attr(item, "observed_at", None),
    )


def _is_timeout_exception(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError | httpx.TimeoutException):
        return True
    reason_code = str(getattr(exc, "reason_code", "") or "").upper()
    if reason_code in {"WMS_TIMEOUT", "WMS_UNAVAILABLE"}:
        return True
    return exc.__class__.__name__.lower().endswith("timeouterror")


def _object_identity_from_criteria(criteria: dict[str, Any]) -> tuple[str, str]:
    for key, object_type in (
        ("package_id", "PKG"),
        ("bin_code", "BIN"),
        ("material_identity_key", "MATERIAL"),
        ("object_key", str(criteria.get("object_type") or "OBJECT")),
        ("rack_code", "RACK"),
        ("correlation_id", "CORRELATION"),
        ("external_reference_value", "EXTERNAL_REFERENCE"),
    ):
        value = criteria.get(key)
        if value:
            return object_type, str(value)
    return "UNKNOWN", "UNKNOWN"


material_location_query_service = MaterialLocationQueryService()


__all__ = [
    "MaterialLocationConflictState",
    "MaterialLocationEvidence",
    "MaterialLocationQueryService",
    "MaterialLocationResult",
    "material_location_query_service",
]
