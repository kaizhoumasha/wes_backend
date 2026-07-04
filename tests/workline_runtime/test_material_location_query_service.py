"""MaterialLocationQuery 只读聚合合同。"""

# pyright: reportArgumentType=false
# 测试 mock 使用 duck-typing，不继承真实 Repository/Service 类型。

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.models.bin_cell_reservation import BinCellReservationStatus
from src.app.runtime.orchestration.services.query.material_location_query_service import (
    MaterialLocationConflictState,
    MaterialLocationQueryService,
)
from src.utils.timezone import timezone


class _LocationEvents:
    async def list_by_object(self, _db: Any, *, object_type: str, object_key: str) -> list[Any]:
        if object_type == "PKG" and object_key == "PKG-CONFLICT":
            return [
                SimpleNamespace(
                    object_type="PKG",
                    object_key="PKG-CONFLICT",
                    location_scope="BIN_CELL",
                    location_code="BIN-A:C01",
                    source="ECS",
                    evidence_json={"source_event_id": "evt-physical"},
                    correlation_id="corr-conflict",
                    source_event_id="evt-physical",
                    source_version="1",
                    external_reference_type=None,
                    external_reference_value=None,
                    provider_code="ECS",
                    occurred_at=timezone.now_for_db(),
                )
            ]
        return []

    async def list_by_correlation_id(self, _db: Any, *, correlation_id: str) -> list[Any]:
        if correlation_id == "corr-ext":
            return [
                SimpleNamespace(
                    object_type="PKG",
                    object_key="PKG-EXT",
                    location_scope="WORK_POSITION",
                    location_code="WP-01",
                    source="ECS",
                    evidence_json={"source_event_id": "evt-ext"},
                    correlation_id=correlation_id,
                    source_event_id="evt-ext",
                    source_version="1",
                    external_reference_type="WMS_DOCUMENT",
                    external_reference_value="doc-001",
                    provider_code="WMS",
                    occurred_at=timezone.now_for_db(),
                )
            ]
        return []

    async def list_by_external_reference(
        self, _db: Any, *, external_reference_type: Any, external_reference_value: Any, provider_code: Any
    ) -> list[Any]:
        if (external_reference_type, external_reference_value, provider_code) == ("WMS_DOCUMENT", "doc-001", "WMS"):
            return await self.list_by_correlation_id(_db, correlation_id="corr-ext")
        return []


class _ActiveFacts:
    async def list_material_location_facts(
        self, _db: Any, *, object_type: Any = None, object_key: Any = None, workline_id: Any = None
    ) -> list[Any]:
        if object_key == "PKG-CONFLICT":
            return [
                SimpleNamespace(
                    object_type="PKG",
                    object_key="PKG-CONFLICT",
                    location_scope="CONVEYOR_QUEUE",
                    location_code="Q-IN",
                    source="ActiveObjectRegistry",
                    evidence_ref="queue:1",
                    evidence_json={"owner_kind": "ON_CONVEYOR"},
                    observed_at=timezone.now_for_db(),
                )
            ]
        return []


class _Reservations:
    async def list_active_or_frozen_by_pkg_codes(self, _db: Any, pkg_codes: Any) -> list[Any]:
        if "PKG-FROZEN" in pkg_codes:
            return [
                SimpleNamespace(
                    pkg_code="PKG-FROZEN",
                    bin_code="BIN-FROZEN",
                    bin_cell_index="3",
                    bin_cell_code="C03",
                    reservation_status=BinCellReservationStatus.RECONCILING,
                    source_event_id="evt-frozen",
                    metadata_json={"material_identity_key": "MAT-FROZEN"},
                    reserved_at=timezone.now_for_db(),
                )
            ]
        if "PKG-RSV" not in pkg_codes:
            return []
        return [
            SimpleNamespace(
                pkg_code="PKG-RSV",
                bin_code="BIN-RSV",
                bin_cell_index="2",
                bin_cell_code="C02",
                reservation_status=BinCellReservationStatus.PLANNED,
                source_event_id="evt-rsv",
                metadata_json={"material_identity_key": "MAT-RSV"},
                reserved_at=timezone.now_for_db(),
            )
        ]

    async def list_active_or_frozen_by_bin_codes(self, _db: Any, bin_codes: Any) -> list[Any]:
        if "BIN-RSV" not in bin_codes:
            return []
        return [
            SimpleNamespace(
                pkg_code="PKG-RSV",
                bin_code="BIN-RSV",
                bin_cell_index="2",
                bin_cell_code="C02",
                reservation_status=BinCellReservationStatus.PLANNED,
                source_event_id="evt-rsv",
                metadata_json={"material_identity_key": "MAT-RSV"},
                reserved_at=timezone.now_for_db(),
            )
        ]


class _RackBinMounts:
    async def list_active_by_rack_code(self, _db: Any, rack_code: str) -> list[Any]:
        if rack_code != "RACK-RSV":
            return []
        return [
            SimpleNamespace(
                rack_code="RACK-RSV",
                rack_slot_code="A",
                bin_code="BIN-RSV",
                source_system="ECS",
                source_event_id="evt-rack-mount",
                source_version="1",
                started_at=timezone.now_for_db(),
            ),
            SimpleNamespace(
                rack_code="RACK-RSV",
                rack_slot_code="C",
                bin_code="BIN-OTHER-SIDE",
                source_system="ECS",
                source_event_id="evt-rack-mount-other-side",
                source_version="1",
                started_at=timezone.now_for_db(),
            ),
        ]


class _Occupancy:
    async def list_active_by_material_identity(self, _db: Any, material_identity_key: str) -> list[Any]:
        return []

    async def list_active_by_bin_codes(self, _db: Any, bin_codes: Any) -> list[Any]:
        return []


@pytest.mark.asyncio
async def test_material_location_query_marks_priority_conflict_reconciling() -> None:
    """本地物理事实与 active projection 冲突时不得静默选任一位置。"""

    service = MaterialLocationQueryService(
        location_event_service=_LocationEvents(),
        active_fact_provider=_ActiveFacts(),
        reservation_repository=_Reservations(),
        occupancy_repository=_Occupancy(),
    )

    result = await service.query_by_package_or_bin(None, package_id="PKG-CONFLICT")

    assert result.conflict_state == MaterialLocationConflictState.RECONCILING
    assert result.location_code is None
    assert [evidence.source for evidence in result.evidence] == ["LOCAL_PHYSICAL_FACT", "ACTIVE_OBJECT"]


@pytest.mark.asyncio
async def test_material_location_query_returns_reservation_target_semantics() -> None:
    """CellReservation RESERVED 读取为目标格位预约位置，而不是物理完成事实。"""

    service = MaterialLocationQueryService(
        location_event_service=_LocationEvents(),
        active_fact_provider=_ActiveFacts(),
        reservation_repository=_Reservations(),
        occupancy_repository=_Occupancy(),
    )

    result = await service.query_by_package_or_bin(None, bin_code="BIN-RSV")

    assert result.conflict_state == MaterialLocationConflictState.OK
    assert result.location_scope == "BIN_CELL"
    assert result.location_code == "BIN-RSV:C02"
    assert result.evidence[0].source == "CELL_RESERVATION"
    assert result.evidence[0].semantic_status == "RESERVED"


@pytest.mark.asyncio
async def test_material_location_query_marks_single_reconciling_reservation_conflict() -> None:
    """CellReservation RECONCILING 本身就是冲突证据，不能降级为 OK。"""

    service = MaterialLocationQueryService(
        location_event_service=_LocationEvents(),
        active_fact_provider=_ActiveFacts(),
        reservation_repository=_Reservations(),
        occupancy_repository=_Occupancy(),
    )

    result = await service.query_by_package_or_bin(None, package_id="PKG-FROZEN")

    assert result.conflict_state == MaterialLocationConflictState.RECONCILING
    assert result.location_code is None
    assert result.evidence[0].source == "CELL_RESERVATION"
    assert result.evidence[0].semantic_status == "RECONCILING"


@pytest.mark.asyncio
async def test_material_location_query_finds_cell_reservation_by_package_id() -> None:
    """by package or bin 入口只给 package_id 时也必须返回 CellReservation evidence。"""

    service = MaterialLocationQueryService(
        location_event_service=_LocationEvents(),
        active_fact_provider=_ActiveFacts(),
        reservation_repository=_Reservations(),
        occupancy_repository=_Occupancy(),
    )

    result = await service.query_by_package_or_bin(None, package_id="PKG-RSV")

    assert result.conflict_state == MaterialLocationConflictState.OK
    assert result.location_code == "BIN-RSV:C02"
    assert result.evidence[0].source == "CELL_RESERVATION"


@pytest.mark.asyncio
async def test_material_location_query_finds_rack_side_mounts() -> None:
    """by rack and side 入口必须返回当前货架面的本地挂载 evidence。"""

    service = MaterialLocationQueryService(
        location_event_service=_LocationEvents(),
        active_fact_provider=_ActiveFacts(),
        reservation_repository=_Reservations(),
        occupancy_repository=_Occupancy(),
        rack_bin_mount_repository=_RackBinMounts(),
    )

    result = await service.query_by_rack_and_side(None, rack_code="RACK-RSV", rack_side="0")

    assert result.conflict_state == MaterialLocationConflictState.OK
    assert result.location_scope == "RACK_SIDE"
    assert result.location_code == "RACK-RSV:0"
    assert result.evidence[0].source == "LOCAL_PHYSICAL_FACT"
    assert result.evidence[0].object_type == "BIN"
    assert result.evidence[0].object_key == "BIN-RSV"
    assert result.evidence[0].evidence_json["rack_slot_code"] == "A"


@pytest.mark.asyncio
async def test_material_location_query_resolves_external_reference_to_evidence() -> None:
    """ExternalReference 入口必须能反查 correlation_id 和位置 evidence。"""

    service = MaterialLocationQueryService(
        location_event_service=_LocationEvents(),
        active_fact_provider=_ActiveFacts(),
        reservation_repository=_Reservations(),
        occupancy_repository=_Occupancy(),
    )

    result = await service.query_by_external_reference(
        None,
        external_reference_type="WMS_DOCUMENT",
        external_reference_value="doc-001",
        provider_code="WMS",
    )

    assert result.conflict_state == MaterialLocationConflictState.OK
    assert result.correlation_id == "corr-ext"
    assert result.location_code == "WP-01"
    assert result.evidence[0].external_reference == "WMS_DOCUMENT:doc-001"
