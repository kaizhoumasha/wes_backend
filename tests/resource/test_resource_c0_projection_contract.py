from __future__ import annotations

from dataclasses import dataclass
from inspect import signature
from types import SimpleNamespace
from typing import Any, get_args, get_origin

import pytest
from sqlalchemy.dialects import postgresql

from src.app.resource.models import (
    Bin,
    BinCellOccupancy,
    BinCellOccupancyBase,
    BinContentSnapshot,
    BinContentSnapshotItem,
    BinMaterialMount,
    BinMaterialMountBase,
    BinPlacement,
    BinPlacementBase,
    BinSlotTemplate,
    BinType,
    Rack,
    RackBinMount,
    RackBinMountBase,
    RackPlacement,
    RackPlacementBase,
    RackSlotTemplate,
    RackType,
    ResourceStateEvent,
    ResourceStateEventBase,
    ResourceStateEventType,
)
from src.app.resource.services.projection_service import ResourceProjectionService
from src.app.resource.services.relation_service import (
    ResourceProjectionResult,
    ResourceProjectionStatus,
    ResourceRelationService,
)
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT

RESOURCE_SESSION_BASES = (
    ResourceStateEventBase,
    RackPlacementBase,
    RackBinMountBase,
    BinPlacementBase,
    BinMaterialMountBase,
    BinCellOccupancyBase,
)

RESOURCE_TABLE_MODELS = (
    RackType,
    RackSlotTemplate,
    Rack,
    BinType,
    BinSlotTemplate,
    Bin,
    ResourceStateEvent,
    RackPlacement,
    RackBinMount,
    BinPlacement,
    BinMaterialMount,
    BinCellOccupancy,
    BinContentSnapshot,
    BinContentSnapshotItem,
)


def _field_allows_int_none(annotation: Any) -> bool:
    if annotation is int:
        return True
    if get_origin(annotation) in {type(None) | int, int | type(None)}:
        return True
    return int in get_args(annotation) and type(None) in get_args(annotation)


def test_resource_session_contract_uses_int_workline_session_id_only() -> None:
    for model in RESOURCE_SESSION_BASES:
        assert "session_id" not in model.model_fields
        field = model.model_fields["workline_session_id"]
        assert _field_allows_int_none(field.annotation)


def test_resource_metadata_has_no_exact_duplicate_indexes() -> None:
    duplicates: list[str] = []
    for model in RESOURCE_TABLE_MODELS:
        indexes_by_definition: dict[tuple[tuple[str, ...], str | None], list[str]] = {}
        for index in model.__table__.indexes:
            key = (
                tuple(column.name for column in index.columns),
                str(index.dialect_options["postgresql"].get("where") or "") or None,
            )
            indexes_by_definition.setdefault(key, []).append(str(index.name))
        duplicates.extend(
            f"{model.__tablename__}:{','.join(sorted(names))}"
            for names in indexes_by_definition.values()
            if len(names) > 1
        )

    assert duplicates == []


def test_resource_metadata_has_no_unowned_single_column_indexes() -> None:
    unowned_columns = {
        BinContentSnapshot: {"bin_code", "captured_at", "source_event_id", "source_session_id"},
        BinContentSnapshotItem: {"material_code", "wms_inventory_id"},
        BinPlacement: {
            "bin_code",
            "ended_at",
            "placeholder_key",
            "position_code",
            "position_type",
            "source_event_id",
            "trace_id",
            "workline_code",
            "workline_id",
        },
        BinSlotTemplate: {"bin_type_code"},
        RackBinMount: {"bin_code", "ended_at", "rack_code", "rack_slot_code", "source_event_id", "trace_id"},
        RackPlacement: {"ended_at", "location_code", "rack_code", "source_event_id", "trace_id"},
        RackSlotTemplate: {"rack_type_code"},
        ResourceStateEvent: {"resource_code", "source_event_id"},
    }
    offenders = {
        f"{model.__tablename__}.{column_name}"
        for model, column_names in unowned_columns.items()
        for column_name in column_names
        if any(
            str(index.name).startswith("ix_wes_") and tuple(column.name for column in index.columns) == (column_name,)
            for index in model.__table__.indexes
        )
    }

    assert offenders == set()


def test_resource_projection_write_surfaces_do_not_accept_retired_plugin_identity() -> None:
    methods = (
        ResourceRelationService.record_rack_arrived,
        ResourceRelationService.record_empty_rack_verified,
        ResourceProjectionService.record_rack_arrived_at_workline_position,
        ResourceProjectionService.record_bin_mounted_to_rack,
        ResourceProjectionService.record_material_mounted_to_bin_cell,
        ResourceProjectionService.record_material_unmounted_from_bin_cell,
        ResourceProjectionService.record_bin_arrived_at_position,
        ResourceProjectionService.record_bin_departed_from_position,
    )

    for method in methods:
        parameters = signature(method).parameters
        assert "plugin_key" not in parameters
        assert "contract_version" not in parameters


def test_resource_reconciliation_result_has_no_generic_runtime_hold_payload() -> None:
    """资源冲突只返回领域 RECONCILING 事实，不创建或回传全线 RuntimeHold。"""

    result = ResourceProjectionResult(
        status=ResourceProjectionStatus.RECONCILING,
        reason_code="RACK_PLACEMENT_CONFLICT",
        message="conflict",
    )

    assert set(ResourceProjectionResult.model_fields) == {
        "status",
        "event",
        "projection",
        "reason_code",
        "message",
    }
    assert result.model_dump(mode="json", exclude_none=True) == {
        "status": "RECONCILING",
        "reason_code": "RACK_PLACEMENT_CONFLICT",
        "message": "conflict",
    }


def test_resource_projection_models_have_required_foreign_keys() -> None:
    mount_fks = BinMaterialMount.__table__.c.bin_cell_occupancy_id.foreign_keys
    assert {fk.target_fullname for fk in mount_fks} == {"wes_biz.resource_bin_cell_occupancies.id"}

    placement_workline_fks = BinPlacement.__table__.c.workline_id.foreign_keys
    assert {fk.target_fullname for fk in placement_workline_fks} == {"wes_biz.work_lines.id"}

    for model in (BinCellOccupancy, BinMaterialMount):
        fks = model.__table__.c.workline_session_id.foreign_keys
        assert {fk.target_fullname for fk in fks} == {"wes_biz.workline_sessions.id"}


def test_resource_fk_tracking_columns_use_sql_compat_bigint() -> None:
    expected_pg_type = SQL_COMPAT_BIGINT.compile(dialect=postgresql.dialect()).upper()
    columns = (
        ResourceStateEvent.__table__.c.workline_session_id,
        RackPlacement.__table__.c.workline_session_id,
        RackBinMount.__table__.c.workline_session_id,
        BinPlacement.__table__.c.workline_session_id,
        BinMaterialMount.__table__.c.workline_session_id,
        BinMaterialMount.__table__.c.bin_cell_occupancy_id,
        BinCellOccupancy.__table__.c.workline_session_id,
    )

    assert expected_pg_type == "BIGINT"
    for column in columns:
        assert column.type.compile(dialect=postgresql.dialect()).upper() == expected_pg_type


@pytest.mark.asyncio
async def test_record_resource_fact_passes_only_workline_session_id_to_new_write_surface(monkeypatch) -> None:
    service = ResourceProjectionService()
    captured: dict[str, Any] = {}

    async def fake_record_rack_arrived_at_workline_position(*_args: Any, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(status="PROJECTED")

    monkeypatch.setattr(
        service,
        "record_rack_arrived_at_workline_position",
        fake_record_rack_arrived_at_workline_position,
    )

    await service.record_resource_fact(
        db=SimpleNamespace(),
        session=SimpleNamespace(id=2001),
        workline=SimpleNamespace(id=1001, line_code="SMT_SORTER_01"),
        fact_type=ResourceStateEventType.RACK_ARRIVED.value,
        payload_json={
            "rack_code": "RACK-001",
            "rack_kind": "SINGLE_LAYER",
            "position_code": "SINGLE_LAYER_A",
            "source_event_id": "evt-001",
        },
        idempotency_key="idem-001",
        trace_id="trace-001",
    )

    assert "session_id" not in captured
    assert captured["workline_session_id"] == 2001
    assert "plugin_key" not in captured
    assert "contract_version" not in captured


@dataclass(slots=True)
class _Row:
    id: int | None = None
    bin_code: str | None = None
    bin_cell_index: str | None = None
    bin_cell_occupancy_id: int | None = None
    pkg_code: str | None = None
    current_location: str | None = None
    ended_at: object | None = None
    workline_session_id: int | None = None
    material_identity_key: str | None = None
    material_code: str | None = None
    lot_code: str | None = None
    date_code: str | None = None
    status: str | None = None
    rack_code: str | None = None
    rack_slot_code: str | None = None
    position_code: str | None = None
    placeholder_key: str | None = None


def test_projection_integrity_service_reports_detail_rows() -> None:
    from src.app.resource.services.projection_integrity_service import ResourceProjectionIntegrityService

    service = ResourceProjectionIntegrityService()
    report = service.diagnose(
        mounts=[
            _Row(id=1, bin_cell_occupancy_id=99, pkg_code="PKG-ORPHAN", bin_code="BIN-1", bin_cell_index="1"),
            _Row(id=2, bin_cell_occupancy_id=10, pkg_code="PKG-A", bin_code="BIN-1", bin_cell_index="1"),
        ],
        occupancies=[
            _Row(id=10, bin_code="BIN-1", bin_cell_index="1", material_identity_key="MAT-A"),
            _Row(id=11, bin_code="BIN-1", bin_cell_index="1", material_identity_key="MAT-A"),
        ],
        sessions=[2001],
        active_session_rows=[
            ("resource_bin_material_mounts", _Row(id=3, workline_session_id=404, pkg_code="PKG-MISSING")),
        ],
    )

    assert [item.reason_code for item in report.issues] == [
        "ORPHAN_MOUNT_OCCUPANCY",
        "ORPHAN_WORKLINE_SESSION",
        "ACTIVE_DUPLICATE",
    ]
    assert report.issues[0].details["mount_id"] == 1
    assert report.issues[1].details["workline_session_id"] == 404
    assert report.issues[2].details["active_ids"] == [10, 11]


def test_projection_integrity_service_reports_duplicates_for_all_active_projection_types() -> None:
    from src.app.resource.services.projection_integrity_service import ResourceProjectionIntegrityService

    service = ResourceProjectionIntegrityService()
    report = service.diagnose(
        rack_placements=[
            _Row(id=1, rack_code="RACK-1"),
            _Row(id=2, rack_code="RACK-1"),
        ],
        rack_bin_mounts=[
            _Row(id=3, rack_code="RACK-1", rack_slot_code="A1", bin_code="BIN-1"),
            _Row(id=4, rack_code="RACK-1", rack_slot_code="A1", bin_code="BIN-2"),
            _Row(id=5, rack_code="RACK-2", rack_slot_code="B1", bin_code="BIN-3"),
            _Row(id=6, rack_code="RACK-3", rack_slot_code="C01", bin_code="BIN-3"),
        ],
        bin_placements=[
            _Row(id=7, bin_code="BIN-4"),
            _Row(id=8, bin_code="BIN-4"),
            _Row(id=9, placeholder_key="PH-1"),
            _Row(id=10, placeholder_key="PH-1"),
        ],
        mounts=[
            _Row(id=11, pkg_code="PKG-1", bin_code="BIN-5", bin_cell_index="1"),
            _Row(id=12, pkg_code="PKG-1", bin_code="BIN-6", bin_cell_index="2"),
        ],
        occupancies=[
            _Row(id=13, bin_code="BIN-7", bin_cell_index="1"),
            _Row(id=14, bin_code="BIN-7", bin_cell_index="1"),
        ],
    )

    duplicate_pairs = [(issue.projection_type, issue.object_key) for issue in report.by_reason("ACTIVE_DUPLICATE")]
    assert duplicate_pairs == [
        ("RACK_PLACEMENT", "RACK-1"),
        ("RACK_BIN_MOUNT_SLOT", "RACK-1:A1"),
        ("RACK_BIN_MOUNT_BIN", "BIN-3"),
        ("BIN_PLACEMENT_BIN", "BIN-4"),
        ("BIN_PLACEMENT_PLACEHOLDER", "PH-1"),
        ("BIN_MATERIAL_MOUNT_PKG", "PKG-1"),
        ("BIN_CELL_OCCUPANCY", "BIN-7:1"),
    ]


def test_projection_integrity_service_accepts_generators_without_losing_diagnostics() -> None:
    from src.app.resource.services.projection_integrity_service import ResourceProjectionIntegrityService

    service = ResourceProjectionIntegrityService()
    report = service.diagnose(
        mounts=(
            row
            for row in [
                _Row(id=1, bin_cell_occupancy_id=404, pkg_code="PKG-ORPHAN", bin_code="BIN-X", bin_cell_index="1"),
                _Row(id=2, pkg_code="PKG-DUP", bin_code="BIN-A", bin_cell_index="1"),
                _Row(id=3, pkg_code="PKG-DUP", bin_code="BIN-B", bin_cell_index="2"),
                _Row(id=4, pkg_code="PKG-DRIFT", bin_code="BIN-C", bin_cell_index="3"),
            ]
        ),
        occupancies=(_Row(id=10, bin_code="BIN-OCC", bin_cell_index="1") for _ in [None]),
        material_units=(_Row(id=11, pkg_code="PKG-DRIFT", current_location="OLD") for _ in [None]),
    )

    assert [issue.reason_code for issue in report.issues] == [
        "ORPHAN_MOUNT_OCCUPANCY",
        "ACTIVE_DUPLICATE",
        "MATERIAL_LOCATION_DRIFT",
    ]
    assert report.by_reason("ORPHAN_MOUNT_OCCUPANCY")[0].details["mount_id"] == 1
    assert report.by_reason("ACTIVE_DUPLICATE")[0].details["active_ids"] == [2, 3]
    assert report.by_reason("MATERIAL_LOCATION_DRIFT")[0].details["projection_location"] == "BIN-C:3"


def test_material_mount_documents_event_snapshot_not_authoritative_material_source() -> None:
    for field_name in ("material_identity_key", "material_code", "lot_code", "date_code"):
        description = BinMaterialMountBase.model_fields[field_name].description or ""
        assert "事件证据快照" in description
        assert "material_units" in description


class RecordingMaterialLocationPersistence:
    def __init__(self) -> None:
        self.updated: list[tuple[str, str]] = []
        self.reconciling: list[tuple[str, str]] = []

    def update_current_location(self, material_unit: _Row, current_location: str) -> None:
        self.updated.append((str(material_unit.pkg_code), current_location))
        material_unit.current_location = current_location

    def mark_reconciling(self, material_unit: _Row, reason_code: str) -> None:
        self.reconciling.append((str(material_unit.pkg_code), reason_code))
        material_unit.status = "RECONCILING"


class RecordingMaterialLocationHold:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []

    def create_hold(self, issue: Any) -> None:
        self.created.append({"pkg_code": issue.pkg_code, "reason_code": issue.reason_code})


def test_material_location_consistency_dry_run_and_confirm_paths() -> None:
    from src.app.resource.services.material_location_consistency_service import (
        MaterialLocationConsistencyService,
    )

    unit_ok = _Row(id=1, pkg_code="PKG-OK", current_location="BIN-1:1")
    unit_drift = _Row(id=2, pkg_code="PKG-DRIFT", current_location="OLD")
    unit_conflict = _Row(id=3, pkg_code="PKG-CONFLICT", current_location="BIN-X:9")
    persistence = RecordingMaterialLocationPersistence()
    hold = RecordingMaterialLocationHold()
    service = MaterialLocationConsistencyService(persistence=persistence, hold_creator=hold)

    issues = service.diagnose(
        material_units=[unit_ok, unit_drift, unit_conflict],
        active_mounts=[
            _Row(id=11, pkg_code="PKG-OK", bin_code="BIN-1", bin_cell_index="1", bin_cell_occupancy_id=101),
            _Row(id=12, pkg_code="PKG-DRIFT", bin_code="BIN-2", bin_cell_index="2", bin_cell_occupancy_id=102),
            _Row(id=13, pkg_code="PKG-CONFLICT", bin_code="BIN-3", bin_cell_index="3", bin_cell_occupancy_id=103),
            _Row(id=14, pkg_code="PKG-CONFLICT", bin_code="BIN-4", bin_cell_index="4", bin_cell_occupancy_id=104),
        ],
        active_occupancies=[
            _Row(id=101, bin_code="BIN-1", bin_cell_index="1"),
            _Row(id=102, bin_code="BIN-2", bin_cell_index="2"),
            _Row(id=103, bin_code="BIN-3", bin_cell_index="3"),
            _Row(id=104, bin_code="BIN-4", bin_cell_index="4"),
        ],
    )

    assert [issue.reason_code for issue in issues] == ["LOCATION_MISMATCH", "MULTIPLE_ACTIVE_MOUNTS"]
    dry_run = service.repair(issues, confirm=False)
    assert dry_run.updated == []
    assert unit_drift.current_location == "OLD"
    assert persistence.updated == []
    assert persistence.reconciling == []
    assert hold.created == []

    confirmed = service.repair(issues, confirm=True)
    assert confirmed.updated == [{"pkg_code": "PKG-DRIFT", "from": "OLD", "to": "BIN-2:2"}]
    assert confirmed.reconciling == [{"pkg_code": "PKG-CONFLICT", "reason_code": "MULTIPLE_ACTIVE_MOUNTS"}]
    assert unit_drift.current_location == "BIN-2:2"
    assert unit_conflict.status == "RECONCILING"
    assert persistence.updated == [("PKG-DRIFT", "BIN-2:2")]
    assert persistence.reconciling == [("PKG-CONFLICT", "MULTIPLE_ACTIVE_MOUNTS")]
    assert hold.created == [{"pkg_code": "PKG-CONFLICT", "reason_code": "MULTIPLE_ACTIVE_MOUNTS"}]


class RecordingDb:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, row: object) -> None:
        self.added.append(row)


def test_material_location_default_persistence_adapter_persists_and_marks_reconciling() -> None:
    from src.app.resource.services.material_location_consistency_service import (
        MaterialLocationRepositoryPersistence,
        material_location_consistency_service,
    )

    assert isinstance(material_location_consistency_service.persistence, MaterialLocationRepositoryPersistence)

    db = RecordingDb()
    service = material_location_consistency_service.with_db(db)
    unit_drift = _Row(pkg_code="PKG-DB-DRIFT", current_location="OLD", status="STORED")
    unit_conflict = _Row(pkg_code="PKG-DB-CONFLICT", current_location="BIN-X:9", status="STORED")

    issues = service.diagnose(
        material_units=[unit_drift, unit_conflict],
        active_mounts=[
            _Row(id=21, pkg_code="PKG-DB-DRIFT", bin_code="BIN-9", bin_cell_index="1", bin_cell_occupancy_id=901),
            _Row(id=22, pkg_code="PKG-DB-CONFLICT", bin_code="BIN-8", bin_cell_index="1", bin_cell_occupancy_id=801),
            _Row(id=23, pkg_code="PKG-DB-CONFLICT", bin_code="BIN-7", bin_cell_index="1", bin_cell_occupancy_id=701),
        ],
        active_occupancies=[
            _Row(id=901, bin_code="BIN-9", bin_cell_index="1"),
            _Row(id=801, bin_code="BIN-8", bin_cell_index="1"),
            _Row(id=701, bin_code="BIN-7", bin_cell_index="1"),
        ],
    )
    result = service.repair(issues, confirm=True)

    assert result.updated == [{"pkg_code": "PKG-DB-DRIFT", "from": "OLD", "to": "BIN-9:1"}]
    assert result.reconciling == [{"pkg_code": "PKG-DB-CONFLICT", "reason_code": "MULTIPLE_ACTIVE_MOUNTS"}]
    assert unit_drift.current_location == "BIN-9:1"
    assert unit_conflict.status == "RECONCILING"
    assert db.added == [unit_drift, unit_conflict]
