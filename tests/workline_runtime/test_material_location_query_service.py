"""MaterialLocationQuery 只读聚合合同。"""

# pyright: reportArgumentType=false
# 测试 mock 使用 duck-typing，不继承真实 Repository/Service 类型。

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from src.app.runtime.orchestration.models.bin_cell_reservation import BinCellReservationStatus
from src.app.runtime.orchestration.services.query.material_location_query_service import (
    MaterialLocationConflictState,
    MaterialLocationEvidence,
    MaterialLocationQueryService,
)
from src.utils.timezone import timezone


class _LocationEvents:
    async def list_by_object(self, _db: Any, *, object_type: str, object_key: str) -> list[Any]:
        if object_type == "BIN" and object_key == "BIN-ACTIVE-CONFLICT":
            return [
                SimpleNamespace(
                    id=9001,
                    object_type="BIN",
                    object_key="BIN-ACTIVE-CONFLICT",
                    location_scope="WORK_POSITION",
                    location_code="WP-BIN",
                    source="ECS",
                    evidence_json={"source_event_id": "evt-bin-physical"},
                    correlation_id="corr-bin-conflict",
                    source_event_id="evt-bin-physical",
                    source_version="1",
                    external_reference_type=None,
                    external_reference_value=None,
                    provider_code="ECS",
                    occurred_at=timezone.now_for_db(),
                )
            ]
        if object_type == "PKG" and object_key == "PKG-MOVED":
            return [
                SimpleNamespace(
                    id=1001,
                    object_type="PKG",
                    object_key="PKG-MOVED",
                    location_scope="BIN_CELL",
                    location_code="BIN-OLD:C01",
                    source="ECS",
                    evidence_json={"source_event_id": "evt-old"},
                    correlation_id="corr-moved",
                    source_event_id="evt-old",
                    source_version="1",
                    external_reference_type=None,
                    external_reference_value=None,
                    provider_code="ECS",
                    occurred_at=timezone.to_db_datetime("2026-07-04T01:00:00Z"),
                ),
                SimpleNamespace(
                    id=1002,
                    object_type="PKG",
                    object_key="PKG-MOVED",
                    location_scope="WORK_POSITION",
                    location_code="WP-NEW",
                    source="ECS",
                    evidence_json={"source_event_id": "evt-new"},
                    correlation_id="corr-moved",
                    source_event_id="evt-new",
                    source_version="2",
                    external_reference_type=None,
                    external_reference_value=None,
                    provider_code="ECS",
                    occurred_at=timezone.to_db_datetime("2026-07-04T01:05:00Z"),
                ),
            ]
        if object_type == "PKG" and object_key == "PKG-CONFLICT":
            return [
                SimpleNamespace(
                    id=2001,
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
        if object_type == "PKG" and object_key == "PKG-SAME-TIME":
            occurred_at = timezone.to_db_datetime("2026-07-04T01:20:00Z")
            return [
                SimpleNamespace(
                    id=3001,
                    object_type="PKG",
                    object_key="PKG-SAME-TIME",
                    location_scope="BIN_CELL",
                    location_code="BIN-SAME-OLD:C01",
                    source="ECS",
                    evidence_json={"source_event_id": "evt-same-old"},
                    correlation_id="corr-same-time",
                    source_event_id="evt-same-old",
                    source_version="1",
                    external_reference_type=None,
                    external_reference_value=None,
                    provider_code="ECS",
                    occurred_at=occurred_at,
                ),
                SimpleNamespace(
                    id=3002,
                    object_type="PKG",
                    object_key="PKG-SAME-TIME",
                    location_scope="WORK_POSITION",
                    location_code="WP-SAME-NEW",
                    source="ECS",
                    evidence_json={"source_event_id": "evt-same-new"},
                    correlation_id="corr-same-time",
                    source_event_id="evt-same-new",
                    source_version="2",
                    external_reference_type=None,
                    external_reference_value=None,
                    provider_code="ECS",
                    occurred_at=occurred_at,
                ),
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
        if object_key == "BIN-ACTIVE-CONFLICT":
            return [
                SimpleNamespace(
                    object_type="BIN",
                    object_key="BIN-ACTIVE-CONFLICT",
                    location_scope="CONVEYOR_QUEUE",
                    location_code="Q-BIN",
                    source="ActiveObjectRegistry",
                    evidence_ref="queue:bin-conflict",
                    evidence_json={"owner_kind": "ON_CONVEYOR"},
                    observed_at=timezone.now_for_db(),
                )
            ]
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
                correlation_id="corr-rsv",
                evidence_json={"provider_code": "WMS", "source_version": "wms-rsv-v1"},
                metadata_json={"material_identity_key": "MAT-RSV"},
                reserved_at=timezone.now_for_db(),
            )
        ]

    async def list_active_or_frozen_by_bin_codes(self, _db: Any, bin_codes: Any) -> list[Any]:
        if "BIN-RESERVED-ONLY" in bin_codes:
            return [
                SimpleNamespace(
                    pkg_code="PKG-BIN-RESERVED",
                    bin_code="BIN-RESERVED-ONLY",
                    bin_cell_index="5",
                    bin_cell_code="C05",
                    reservation_status=BinCellReservationStatus.PLANNED,
                    source_event_id="evt-bin-rsv",
                    correlation_id="corr-bin-rsv",
                    evidence_json={"provider_code": "WMS", "source_version": "wms-bin-rsv-v1"},
                    metadata_json={"material_identity_key": "MAT-BIN-RSV"},
                    reserved_at=timezone.now_for_db(),
                )
            ]
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
                correlation_id="corr-rsv",
                evidence_json={"provider_code": "WMS", "source_version": "wms-rsv-v1"},
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


class _WmsSnapshots:
    async def list_evidence(self, _db: Any, *, query_entry: str, **criteria: Any) -> list[Any]:
        if criteria.get("package_id") == "PKG-WMS":
            return [
                {
                    "source": "WMS_RECONCILIATION_SNAPSHOT",
                    "priority": 1,
                    "object_type": "PKG",
                    "object_key": "PKG-WMS",
                    "location_scope": "WMS_BIN",
                    "location_code": "WMS-BIN-01",
                    "source_version": "wms.v1",
                    "evidence_json": {"query_entry": query_entry},
                    "observed_at": timezone.to_db_datetime("2026-07-04T01:10:00Z"),
                }
            ]
        if criteria.get("package_id") == "PKG-WMS-TIMEOUT":
            raise TimeoutError("wms snapshot timeout")
        if criteria.get("package_id") == "PKG-WMS-HTTPX-TIMEOUT":
            request = httpx.Request("GET", "http://wms.example/snapshot")
            raise httpx.ReadTimeout("read timeout", request=request)
        if criteria.get("package_id") == "PKG-WMS-DOMAIN-TIMEOUT":
            raise _WmsStyleTimeout("wms timeout")
        if criteria.get("package_id") == "PKG-WMS-MODEL":
            return [
                MaterialLocationEvidence(
                    source="WMS_RECONCILIATION_SNAPSHOT",
                    priority=1,
                    object_type="PKG",
                    object_key="PKG-WMS-MODEL",
                    location_scope="WMS_BIN",
                    location_code="WMS-BIN-MODEL",
                )
            ]
        return []


class _LegacyEvidence:
    async def list_evidence(self, _db: Any, *, query_entry: str, **criteria: Any) -> list[Any]:
        if criteria.get("package_id") != "PKG-LEGACY":
            if criteria.get("package_id") == "PKG-LEGACY-MODEL":
                return [
                    MaterialLocationEvidence(
                        source="LEGACY_CHARACTERIZATION",
                        priority=1,
                        object_type="PKG",
                        object_key="PKG-LEGACY-MODEL",
                        location_scope="LEGACY_CONTEXT",
                        location_code="legacy-model-context",
                    )
                ]
            return []
        return [
            SimpleNamespace(
                source="LEGACY_CHARACTERIZATION",
                priority=1,
                object_type="PKG",
                object_key="PKG-LEGACY",
                location_scope="LEGACY_CONTEXT",
                location_code="legacy-plugin-context",
                semantic_status="CHARACTERIZED",
                evidence_json={"query_entry": query_entry},
                observed_at=timezone.to_db_datetime("2026-07-04T01:15:00Z"),
            )
        ]


class _WmsStyleTimeout(Exception):
    reason_code = "WMS_TIMEOUT"


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
async def test_material_location_query_uses_latest_location_event_as_current_fact() -> None:
    """RuntimeLocationEvent 是 append-only 历史；当前位置只取最新事实参与冲突判断。"""

    service = MaterialLocationQueryService(
        location_event_service=_LocationEvents(),
        active_fact_provider=_ActiveFacts(),
        reservation_repository=_Reservations(),
        occupancy_repository=_Occupancy(),
    )

    result = await service.query_by_package_or_bin(None, package_id="PKG-MOVED")

    assert result.conflict_state == MaterialLocationConflictState.OK
    assert result.location_scope == "WORK_POSITION"
    assert result.location_code == "WP-NEW"
    assert [evidence.location_code for evidence in result.evidence] == ["WP-NEW", "BIN-OLD:C01"]


@pytest.mark.asyncio
async def test_material_location_query_uses_latest_event_id_when_location_event_time_ties() -> None:
    """同一 occurred_at 下，后写 RuntimeLocationEvent 必须覆盖旧位置事实。"""

    service = MaterialLocationQueryService(
        location_event_service=_LocationEvents(),
        active_fact_provider=_ActiveFacts(),
        reservation_repository=_Reservations(),
        occupancy_repository=_Occupancy(),
    )

    result = await service.query_by_package_or_bin(None, package_id="PKG-SAME-TIME")

    assert result.conflict_state == MaterialLocationConflictState.OK
    assert result.location_code == "WP-SAME-NEW"
    assert [evidence.location_code for evidence in result.evidence] == ["WP-SAME-NEW", "BIN-SAME-OLD:C01"]


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
    assert result.evidence[0].correlation_id == "corr-rsv"
    assert result.evidence[0].provider_code == "WMS"
    assert result.evidence[0].source_version == "wms-rsv-v1"
    assert result.evidence[0].evidence_json["material_identity_key"] == "MAT-RSV"


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
async def test_material_location_query_workline_active_object_aggregates_local_fact_conflicts() -> None:
    """第 6 入口也必须聚合本地事实与 active projection，并暴露冲突。"""

    service = MaterialLocationQueryService(
        location_event_service=_LocationEvents(),
        active_fact_provider=_ActiveFacts(),
        reservation_repository=_Reservations(),
        occupancy_repository=_Occupancy(),
    )

    result = await service.query_by_workline_active_object(
        None,
        workline_id=100,
        object_type="PKG",
        object_key="PKG-CONFLICT",
    )

    assert result.conflict_state == MaterialLocationConflictState.RECONCILING
    assert result.location_code is None
    assert [evidence.source for evidence in result.evidence] == ["LOCAL_PHYSICAL_FACT", "ACTIVE_OBJECT"]


@pytest.mark.asyncio
async def test_material_location_query_workline_active_object_reads_cell_reservation() -> None:
    """第 6 入口按 PKG 查询时不能漏掉 CellReservation 目标位置。"""

    service = MaterialLocationQueryService(
        location_event_service=_LocationEvents(),
        active_fact_provider=_ActiveFacts(),
        reservation_repository=_Reservations(),
        occupancy_repository=_Occupancy(),
    )

    result = await service.query_by_workline_active_object(
        None,
        workline_id=100,
        object_type="PKG",
        object_key="PKG-RSV",
    )

    assert result.conflict_state == MaterialLocationConflictState.OK
    assert result.location_code == "BIN-RSV:C02"
    assert result.evidence[0].source == "CELL_RESERVATION"


@pytest.mark.asyncio
async def test_material_location_query_workline_active_object_bin_conflict_reconciling() -> None:
    """第 6 入口按 BIN 查询时也必须聚合本地事实和 active projection 冲突。"""

    service = MaterialLocationQueryService(
        location_event_service=_LocationEvents(),
        active_fact_provider=_ActiveFacts(),
        reservation_repository=_Reservations(),
        occupancy_repository=_Occupancy(),
    )

    result = await service.query_by_workline_active_object(
        None,
        workline_id=100,
        object_type="BIN",
        object_key="BIN-ACTIVE-CONFLICT",
    )

    assert result.conflict_state == MaterialLocationConflictState.RECONCILING
    assert result.location_code is None
    assert [evidence.source for evidence in result.evidence] == ["LOCAL_PHYSICAL_FACT", "ACTIVE_OBJECT"]


@pytest.mark.asyncio
async def test_material_location_query_workline_active_object_bin_reads_cell_reservation() -> None:
    """第 6 入口按 BIN 查询时必须能读取 CellReservation 目标格位。"""

    service = MaterialLocationQueryService(
        location_event_service=_LocationEvents(),
        active_fact_provider=_ActiveFacts(),
        reservation_repository=_Reservations(),
        occupancy_repository=_Occupancy(),
    )

    result = await service.query_by_workline_active_object(
        None,
        workline_id=100,
        object_type="BIN",
        object_key="BIN-RESERVED-ONLY",
    )

    assert result.conflict_state == MaterialLocationConflictState.OK
    assert result.location_code == "BIN-RESERVED-ONLY:C05"
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


@pytest.mark.asyncio
async def test_material_location_query_reads_wms_and_legacy_evidence_providers() -> None:
    """WMS snapshot 与 legacy characterization provider 必须能进入 5 来源优先级。"""

    service = MaterialLocationQueryService(
        location_event_service=_LocationEvents(),
        active_fact_provider=_ActiveFacts(),
        reservation_repository=_Reservations(),
        occupancy_repository=_Occupancy(),
        wms_snapshot_provider=_WmsSnapshots(),
        legacy_evidence_provider=_LegacyEvidence(),
    )

    wms_result = await service.query_by_package_or_bin(None, package_id="PKG-WMS")
    legacy_result = await service.query_by_package_or_bin(None, package_id="PKG-LEGACY")
    timeout_result = await service.query_by_package_or_bin(None, package_id="PKG-WMS-TIMEOUT")

    assert wms_result.conflict_state == MaterialLocationConflictState.OK
    assert wms_result.location_code == "WMS-BIN-01"
    assert wms_result.evidence[0].source == "WMS_RECONCILIATION_SNAPSHOT"
    assert wms_result.evidence[0].priority == 4
    assert legacy_result.conflict_state == MaterialLocationConflictState.OK
    assert legacy_result.location_code == "legacy-plugin-context"
    assert legacy_result.evidence[0].source == "LEGACY_CHARACTERIZATION"
    assert legacy_result.evidence[0].priority == 5
    assert timeout_result.conflict_state == MaterialLocationConflictState.WMS_UNAVAILABLE
    assert timeout_result.evidence[0].source == "WMS_UNAVAILABLE"


@pytest.mark.asyncio
async def test_material_location_query_normalizes_wms_timeout_exceptions() -> None:
    """WMS provider timeout 不能冒泡到 API 查询层，统一返回 WMS_UNAVAILABLE evidence。"""

    service = MaterialLocationQueryService(
        location_event_service=_LocationEvents(),
        active_fact_provider=_ActiveFacts(),
        reservation_repository=_Reservations(),
        occupancy_repository=_Occupancy(),
        wms_snapshot_provider=_WmsSnapshots(),
    )

    httpx_timeout = await service.query_by_package_or_bin(None, package_id="PKG-WMS-HTTPX-TIMEOUT")
    domain_timeout = await service.query_by_package_or_bin(None, package_id="PKG-WMS-DOMAIN-TIMEOUT")

    assert httpx_timeout.conflict_state == MaterialLocationConflictState.WMS_UNAVAILABLE
    assert httpx_timeout.evidence[0].source == "WMS_UNAVAILABLE"
    assert domain_timeout.conflict_state == MaterialLocationConflictState.WMS_UNAVAILABLE
    assert domain_timeout.evidence[0].source == "WMS_UNAVAILABLE"


@pytest.mark.asyncio
async def test_material_location_query_clamps_model_provider_evidence_priority() -> None:
    """provider 直接返回 MaterialLocationEvidence 时也不能覆盖固定来源优先级。"""

    service = MaterialLocationQueryService(
        location_event_service=_LocationEvents(),
        active_fact_provider=_ActiveFacts(),
        reservation_repository=_Reservations(),
        occupancy_repository=_Occupancy(),
        wms_snapshot_provider=_WmsSnapshots(),
        legacy_evidence_provider=_LegacyEvidence(),
    )

    wms_result = await service.query_by_package_or_bin(None, package_id="PKG-WMS-MODEL")
    legacy_result = await service.query_by_package_or_bin(None, package_id="PKG-LEGACY-MODEL")

    assert wms_result.evidence[0].priority == 4
    assert legacy_result.evidence[0].priority == 5
