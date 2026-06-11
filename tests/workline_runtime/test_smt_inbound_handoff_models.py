"""SMT 入库 handoff 基础模型合同测试。"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import JSON, UniqueConstraint


def _handoff_models() -> Any:
    try:
        return importlib.import_module("src.app.workline.models.smt_inbound_handoff")
    except ModuleNotFoundError as exc:
        pytest.fail(f"缺少 SMT 入库 handoff 模型模块: {exc}")


def _model_attr(module: Any, name: str) -> Any:
    value = getattr(module, name, None)
    if value is None:
        pytest.fail(f"缺少模型导出: {name}")
    return value


def _handoff_migration_source() -> str:
    migration_path = (
        Path(__file__).parents[2] / "migrations/versions/20260611_0731_fb02178f9772_add_smt_inbound_handoff.py"
    )
    return migration_path.read_text()


def test_smt_inbound_handoff_status_enums_match_spec() -> None:
    models = _handoff_models()

    demand_status = _model_attr(models, "SmtInboundHandoffDemandStatus")
    source_item_status = _model_attr(models, "SmtInboundHandoffSourceItemStatus")

    assert [item.value for item in demand_status] == [
        "CREATED",
        "EVALUATING",
        "WAITING_FULL_BOX_EXCHANGE",
        "RECONCILING",
        "FULL_BOX_EXCHANGED",
        "READY_FOR_SORTING",
        "CLAIMED_BY_SORTING",
        "SORTING_IN_PROGRESS",
        "COMPLETED",
        "MANUAL_HOLD",
        "CANCELLED",
    ]
    assert [item.value for item in source_item_status] == [
        "READY",
        "PICK_REQUESTED",
        "CLAIMED_BY_SORTING",
        "PICKED",
        "SORTING",
        "SORTED",
        "EXCHANGED",
        "SKIPPED",
        "MANUAL_HOLD",
    ]


def test_smt_inbound_handoff_source_item_contains_claim_and_command_evidence() -> None:
    models = _handoff_models()
    source_item = _model_attr(models, "SmtInboundHandoffSourceItem")
    columns = source_item.__table__.c

    assert "claim_attempt_no" in columns
    assert columns.claim_attempt_no.default is not None
    assert "source_pick_inbox_id" in columns
    assert "source_pick_command_id" in columns
    assert "source_pick_command_code" in columns
    assert "source_pick_dispatch_key" in columns


def test_smt_inbound_handoff_metadata_declares_idempotency_and_hot_path_indexes() -> None:
    models = _handoff_models()
    demand = _model_attr(models, "SmtInboundHandoffDemand")
    source_item = _model_attr(models, "SmtInboundHandoffSourceItem")

    demand_constraints = {
        constraint.name
        for constraint in demand.__table__.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.name
    }
    source_constraints = {
        constraint.name
        for constraint in source_item.__table__.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.name
    }
    demand_indexes = {index.name: index for index in demand.__table__.indexes if index.name}
    source_indexes = {index.name: index for index in source_item.__table__.indexes if index.name}

    assert "uq_smt_inbound_handoff_demands_demand_key" in demand_constraints
    assert "uq_smt_inbound_handoff_demands_rack_release_id" in demand_constraints
    assert "uq_smt_inbound_handoff_source_items_demand_item_key" in source_constraints

    demand_scan_index = demand_indexes.get("ix_smt_inbound_handoff_demands_due_scan")
    ready_claim_index = source_indexes.get("ix_smt_inbound_handoff_source_items_ready_claim")
    recovery_index = source_indexes.get("ix_smt_inbound_handoff_source_items_post_claim_recovery")

    assert demand_scan_index is not None
    assert [column.name for column in demand_scan_index.columns] == ["next_attempt_at", "updated_at", "id"]
    assert "READY_FOR_SORTING" in str(demand_scan_index.dialect_options["postgresql"]["where"])

    assert ready_claim_index is not None
    assert [column.name for column in ready_claim_index.columns] == ["next_attempt_at", "handoff_demand_id", "id"]
    assert "status = 'READY'" in str(ready_claim_index.dialect_options["postgresql"]["where"])

    assert recovery_index is not None
    assert [column.name for column in recovery_index.columns] == ["source_pick_inbox_id", "updated_at", "id"]
    recovery_where = str(recovery_index.dialect_options["postgresql"]["where"])
    assert "PICK_REQUESTED" in recovery_where
    assert "CLAIMED_BY_SORTING" in recovery_where
    recovery_statuses = set(re.findall(r"'([^']+)'", recovery_where))
    assert recovery_statuses <= {item.value for item in _model_attr(models, "SmtInboundHandoffSourceItemStatus")}


def test_smt_inbound_handoff_release_snapshot_is_json_evidence_not_claim_path() -> None:
    models = _handoff_models()
    demand = _model_attr(models, "SmtInboundHandoffDemand")

    assert isinstance(demand.__table__.c.bin_snapshots_json.type, JSON)
    assert demand.__table__.c.bin_snapshots_json.nullable is False
    assert demand.__table__.c.bin_snapshots_json.server_default is not None
    indexed_columns = {column.name for index in demand.__table__.indexes for column in index.columns}
    assert "bin_snapshots_json" not in indexed_columns


def test_smt_inbound_handoff_migration_source_item_check_constraint_covers_recovery_states() -> None:
    enum_blocks = re.findall(
        r"sa\.Column\(\s*\"status\",\s*sa\.Enum\((.*?)name=\"([^\"]+)\"",
        _handoff_migration_source(),
        flags=re.S,
    )
    source_item_enum = next(body for body, enum_name in enum_blocks if enum_name == "smtinboundhandoffsourceitemstatus")

    assert '"CLAIMED_BY_SORTING"' in source_item_enum


def test_smt_inbound_handoff_migration_downgrade_guards_internal_event_rows() -> None:
    migration_source = _handoff_migration_source()
    guard_source = migration_source.split("def _guard_no_internal_event_rows_for_downgrade() -> None:", maxsplit=1)[
        1
    ].split("\ndef ", maxsplit=1)[0]
    downgrade_source = migration_source.split("def downgrade() -> None:", maxsplit=1)[1]

    assert "INTERNAL_EVENT" in guard_source
    assert "RAISE EXCEPTION" in guard_source
    assert "_guard_no_internal_event_rows_for_downgrade()" in downgrade_source
    assert downgrade_source.index("_guard_no_internal_event_rows_for_downgrade()") < downgrade_source.index(
        "_recreate_inbox_kind_constraint(include_internal_event=False)"
    )
