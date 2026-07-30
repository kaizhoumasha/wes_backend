"""WMS 输送线持久化模型合同。"""

from __future__ import annotations

from importlib import import_module
from typing import Any

import pytest
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

from src.app.runtime.orchestration.conveyor_queue_membership import ConveyorQueueMembership
from src.app.runtime.orchestration.execution_session import RUNTIME_SCHEMA


def _runtime_model(module_name: str, model_name: str) -> type[Any]:
    try:
        module = import_module(f"src.app.runtime.orchestration.{module_name}")
    except ModuleNotFoundError:
        pytest.fail(f"WMS persistence model module is missing: {module_name}")
    model = getattr(module, model_name, None)
    assert model is not None, f"WMS persistence model is missing: {model_name}"
    return model


def _check_sql(model: type[Any]) -> dict[str, str]:
    return {
        constraint.name or "": str(constraint.sqltext)
        for constraint in model.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }


def _indexes(model: type[Any]) -> dict[str, Index]:
    return {index.name: index for index in model.__table__.indexes}


def _unique_constraints(model: type[Any]) -> dict[str, UniqueConstraint]:
    return {
        constraint.name or "": constraint
        for constraint in model.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _foreign_key_targets(model: type[Any], column_name: str) -> set[str]:
    targets: set[str] = set()
    for constraint in model.__table__.constraints:
        if not isinstance(constraint, ForeignKeyConstraint):
            continue
        local_columns = tuple(element.parent.name for element in constraint.elements)
        if column_name not in local_columns:
            continue
        targets.update(element.target_fullname for element in constraint.elements)
    return targets


def _index_columns(index: Index) -> tuple[str, ...]:
    return tuple(expression.name for expression in index.expressions)


def _postgresql_where(index: Index) -> str:
    where = index.dialect_options["postgresql"].get("where")
    return "" if where is None else str(where)


def test_runtime_orchestration_exports_the_four_g41_models() -> None:
    runtime = import_module("src.app.runtime.orchestration")

    assert {
        "WmsRackDemand",
        "MaterialFlowOwner",
        "BinRouteInstance",
        "WmsConveyorBatchMember",
    }.issubset(set(runtime.__all__))
    for model_name in (
        "WmsRackDemand",
        "MaterialFlowOwner",
        "BinRouteInstance",
        "WmsConveyorBatchMember",
    ):
        assert getattr(runtime, model_name, None) is not None


def test_wms_rack_demand_freezes_one_active_e08_or_e09_root() -> None:
    model = _runtime_model("wms_rack_demand", "WmsRackDemand")

    assert model.__tablename__ == "wms_rack_demands"
    assert model.__table__.schema == RUNTIME_SCHEMA
    assert {
        "id",
        "workline_id",
        "station_code",
        "rack_type",
        "demand_generation",
        "required_rack_code",
        "root_operation_identity",
        "root_intent_id",
        "handoff_from_owner_id",
        "lifecycle_state",
        "opened_at_ms",
        "closed_at_ms",
        "reconciliation_case_id",
    }.issubset(model.model_fields)
    assert _foreign_key_targets(model, "root_intent_id") == {
        f"{RUNTIME_SCHEMA}.runtime_intent_logs.id",
    }
    assert _foreign_key_targets(model, "reconciliation_case_id") == {
        f"{RUNTIME_SCHEMA}.reconciliation_cases.id",
    }
    assert _foreign_key_targets(model, "handoff_from_owner_id") == {
        f"{RUNTIME_SCHEMA}.material_flow_owners.id",
    }
    assert model.__table__.c.handoff_from_owner_id.nullable is True
    checks = _check_sql(model)
    assert "wms.fulfillment.request_rack_supply@v1" in checks["ck_wms_rack_demands_root_shape"]
    assert "wms.fulfillment.request_rack_transport@v1" in checks["ck_wms_rack_demands_root_shape"]
    assert "ACTIVE" in checks["ck_wms_rack_demands_lifecycle"]
    assert "RECONCILING" in checks["ck_wms_rack_demands_lifecycle"]
    indexes = _indexes(model)
    assert set(indexes) == {
        "ix_wes_runtime_wms_rack_demands_reconciliation_case_id",
        "ux_wms_rack_demands_active_station_rack_type",
    }
    active = indexes["ux_wms_rack_demands_active_station_rack_type"]
    assert active.unique is True
    # station_code 仅在工作线内稳定，故 workline_id 是需求键的一部分。
    assert _index_columns(active) == ("workline_id", "station_code", "rack_type")
    assert "lifecycle_state IN ('PREPARING', 'ACTIVE', 'RECONCILING')" in _postgresql_where(active)
    assert "uq_wms_rack_demands_root_intent" in _unique_constraints(model)


def test_material_flow_owner_uses_one_typed_object_scope_and_one_owner_identity() -> None:
    model = _runtime_model("material_flow_owner", "MaterialFlowOwner")

    assert model.__tablename__ == "material_flow_owners"
    assert model.__table__.schema == RUNTIME_SCHEMA
    assert {
        "id",
        "workline_id",
        "object_type",
        "object_key",
        "owner_type",
        "owner_key",
        "owner_intent_id",
        "lifecycle_state",
        "source_event_id",
        "acquired_at_ms",
        "released_at_ms",
        "reconciliation_case_id",
    }.issubset(model.model_fields)
    assert "flow_root_id" not in model.model_fields
    assert _foreign_key_targets(model, "owner_intent_id") == {
        f"{RUNTIME_SCHEMA}.runtime_intent_logs.id",
    }
    checks = _check_sql(model)
    assert "RACK" in checks["ck_material_flow_owners_object_type"]
    assert "RACK_FACE" in checks["ck_material_flow_owners_object_type"]
    assert "BIN" in checks["ck_material_flow_owners_object_type"]
    assert "OCCUPANCY" in checks["ck_material_flow_owners_object_type"]
    assert "FULL_BOX_EXCHANGE" in checks["ck_material_flow_owners_owner_type"]
    assert "STATION_TRANSPORT" in checks["ck_material_flow_owners_owner_type"]
    assert "PIECE_SORTING" in checks["ck_material_flow_owners_owner_type"]
    assert "RELEASED" in checks["ck_material_flow_owners_lifecycle"]
    indexes = _indexes(model)
    assert set(indexes) == {
        "ix_wes_runtime_material_flow_owners_owner_intent_id",
        "ix_wes_runtime_material_flow_owners_reconciliation_case_id",
        "ux_material_flow_owners_active_object",
    }
    active_object = indexes["ux_material_flow_owners_active_object"]
    assert active_object.unique is True
    assert _index_columns(active_object) == ("object_type", "object_key")
    assert "lifecycle_state IN ('ACTIVE', 'RECONCILING')" in _postgresql_where(active_object)


def test_bin_route_instance_is_the_minimal_monotonic_route_authority() -> None:
    model = _runtime_model("bin_route_instance", "BinRouteInstance")

    assert model.__tablename__ == "bin_route_instances"
    assert model.__table__.schema == RUNTIME_SCHEMA
    assert {
        "route_instance_id",
        "bin_code",
        "workline_id",
        "created_by_e12_intent_id",
        "current_node",
        "route_version",
        "lifecycle_state",
        "current_rack_code",
        "current_slot_code",
        "last_transition_source",
        "last_transition_source_event_id",
        "closed_at_ms",
        "reconciliation_case_id",
    } == set(model.model_fields)
    assert {
        "ng",
        "scan2",
        "work_item_id",
        "command_id",
        "transition_history_json",
    }.isdisjoint(model.model_fields)
    assert model.__table__.primary_key.columns.keys() == ["route_instance_id"]
    assert _foreign_key_targets(model, "created_by_e12_intent_id") == {
        f"{RUNTIME_SCHEMA}.runtime_intent_logs.id",
    }
    assert "current_queue_membership_id" not in model.model_fields
    assert _foreign_key_targets(model, "reconciliation_case_id") == {
        f"{RUNTIME_SCHEMA}.reconciliation_cases.id",
    }
    checks = _check_sql(model)
    assert "route_version > 0" in checks["ck_bin_route_instances_version"]
    assert "CTU_INBOUND_IN_FLIGHT" in checks["ck_bin_route_instances_node"]
    assert "RETURN_QUEUE" in checks["ck_bin_route_instances_node"]
    assert "CTU_RETURN_IN_FLIGHT" in checks["ck_bin_route_instances_node"]
    assert "NG_LINE" in checks["ck_bin_route_instances_lifecycle"]
    assert "RECONCILING" in checks["ck_bin_route_instances_lifecycle"]
    assert "current_rack_code IS NULL" in checks["ck_bin_route_instances_location_shape"]
    indexes = _indexes(model)
    assert set(indexes) == {
        "ix_wes_runtime_bin_route_instances_created_by_e12_intent_id",
        "ix_wes_runtime_bin_route_instances_reconciliation_case_id",
        "ux_bin_route_instances_active_bin",
    }
    active_bin = indexes["ux_bin_route_instances_active_bin"]
    assert active_bin.unique is True
    assert _index_columns(active_bin) == ("bin_code",)
    assert "lifecycle_state IN ('ACTIVE', 'RECONCILING')" in _postgresql_where(active_bin)


def test_wms_conveyor_batch_member_uses_runtime_intent_as_the_only_batch_root() -> None:
    model = _runtime_model("wms_conveyor_batch_member", "WmsConveyorBatchMember")

    assert model.__tablename__ == "wms_conveyor_batch_members"
    assert model.__table__.schema == RUNTIME_SCHEMA
    assert {
        "id",
        "runtime_intent_log_id",
        "route_instance_id",
        "source_queue_membership_id",
        "workline_id",
        "queue_code",
        "direction",
        "sequence_no",
        "bin_code",
        "reserved_queue_position",
        "member_state",
        "staged_at_ms",
        "accepted_at_ms",
        "reservation_released_at_ms",
        "terminal_at_ms",
        "terminal_outcome",
    }.issubset(model.model_fields)
    assert {
        "batch_id",
        "submission_id",
        "ack_json",
        "accepted_scope_json",
        "lease_token",
        "lease_until",
    }.isdisjoint(model.model_fields)
    assert _foreign_key_targets(model, "runtime_intent_log_id") == {
        f"{RUNTIME_SCHEMA}.runtime_intent_logs.id",
    }
    assert _foreign_key_targets(model, "route_instance_id") == {
        f"{RUNTIME_SCHEMA}.bin_route_instances.route_instance_id",
    }
    assert _foreign_key_targets(model, "source_queue_membership_id") == {
        f"{RUNTIME_SCHEMA}.conveyor_queue_memberships.id",
    }
    checks = _check_sql(model)
    assert "INBOUND" in checks["ck_wms_conveyor_batch_members_direction_shape"]
    assert "RETURN" in checks["ck_wms_conveyor_batch_members_direction_shape"]
    assert "CANDIDATE" in checks["ck_wms_conveyor_batch_members_lifecycle"]
    assert "ACCEPTED" in checks["ck_wms_conveyor_batch_members_lifecycle"]
    assert "RELEASED" in checks["ck_wms_conveyor_batch_members_lifecycle"]
    assert "TERMINAL" in checks["ck_wms_conveyor_batch_members_lifecycle"]
    assert "sequence_no > 0" in checks["ck_wms_conveyor_batch_members_sequence"]
    uniques = _unique_constraints(model)
    assert "uq_wms_conveyor_batch_members_intent_sequence" in uniques
    assert "uq_wms_conveyor_batch_members_intent_route" in uniques
    indexes = _indexes(model)
    assert set(indexes) == {
        "ix_wes_runtime_wms_conveyor_batch_members_route_instance_id",
        "ux_wms_conveyor_batch_members_active_inbound_position",
        "ux_wms_conveyor_batch_members_active_source_membership",
    }
    inbound_position = indexes["ux_wms_conveyor_batch_members_active_inbound_position"]
    source_membership = indexes["ux_wms_conveyor_batch_members_active_source_membership"]
    assert inbound_position.unique is True
    assert source_membership.unique is True
    assert _index_columns(inbound_position) == (
        "workline_id",
        "queue_code",
        "reserved_queue_position",
    )
    assert _index_columns(source_membership) == ("source_queue_membership_id",)
    inbound_position_where = _postgresql_where(inbound_position)
    assert "member_state IN ('CANDIDATE', 'ACCEPTED')" in inbound_position_where
    assert "member_state = 'TERMINAL' AND terminal_outcome = 'UNKNOWN'" in inbound_position_where
    assert "reservation_released_at_ms IS NULL" in inbound_position_where
    assert "member_state IN ('CANDIDATE', 'ACCEPTED')" in _postgresql_where(source_membership)


def test_conveyor_queue_membership_freezes_return_fifo_and_claim_as_one_shape() -> None:
    assert {
        "route_instance_id",
        "scan3_enqueued_at",
        "queue_position",
        "e13_claim_intent_id",
        "e13_claim_token",
        "e13_claim_until",
    }.issubset(ConveyorQueueMembership.model_fields)
    assert _foreign_key_targets(ConveyorQueueMembership, "route_instance_id") == {
        f"{RUNTIME_SCHEMA}.bin_route_instances.route_instance_id",
    }
    assert _foreign_key_targets(ConveyorQueueMembership, "e13_claim_intent_id") == {
        f"{RUNTIME_SCHEMA}.runtime_intent_logs.id",
    }
    checks = _check_sql(ConveyorQueueMembership)
    return_shape = checks["ck_conveyor_queue_memberships_return_shape"]
    assert "membership_status IN ('ACTIVE', 'RECONCILING')" in return_shape
    assert "queue_role = 'RETURN_QUEUE'" in return_shape
    assert "route_instance_id IS NOT NULL" in return_shape
    assert "scan3_enqueued_at IS NOT NULL" in return_shape
    assert "queue_position IS NOT NULL" in return_shape
    assert "bin_code IS NOT NULL" in return_shape
    entry_shape = checks["ck_conveyor_queue_memberships_entry_shape"]
    assert "membership_status IN ('ACTIVE', 'RECONCILING')" in entry_shape
    assert "queue_role = 'ENTRY'" in entry_shape
    assert "route_instance_id IS NOT NULL" in entry_shape
    assert "queue_position IS NOT NULL" in entry_shape
    assert "bin_code IS NOT NULL" in entry_shape
    claim_shape = checks["ck_conveyor_queue_memberships_claim_shape"]
    assert "e13_claim_intent_id IS NULL" in claim_shape
    assert "e13_claim_token IS NULL" in claim_shape
    assert "e13_claim_until IS NULL" in claim_shape
    assert "e13_claim_intent_id IS NOT NULL" in claim_shape
    assert "e13_claim_token IS NOT NULL" in claim_shape
    assert "e13_claim_until IS NOT NULL" in claim_shape
    assert "membership_status IN ('ACTIVE', 'RECONCILING')" in claim_shape
    indexes = _indexes(ConveyorQueueMembership)
    assert "ix_wes_runtime_conveyor_queue_memberships_scan3_enqueued_at" not in indexes
    active_route = indexes["ux_wes_runtime_conveyor_queue_memberships_active_route"]
    fifo = indexes["ix_wes_runtime_conveyor_queue_memberships_return_fifo_unclaimed"]
    active_entry_position = indexes["ux_wes_runtime_conveyor_queue_memberships_active_entry_position"]
    assert active_route.unique is True
    assert _index_columns(active_route) == ("route_instance_id",)
    assert "membership_status IN ('ACTIVE', 'RECONCILING')" in _postgresql_where(active_route)
    assert active_entry_position.unique is True
    assert _index_columns(active_entry_position) == ("workline_id", "queue_code", "queue_position")
    assert "membership_status IN ('ACTIVE', 'RECONCILING')" in _postgresql_where(active_entry_position)
    assert "queue_role = 'ENTRY'" in _postgresql_where(active_entry_position)
    assert _index_columns(fifo) == (
        "workline_id",
        "queue_code",
        "scan3_enqueued_at",
        "queue_position",
        "bin_code",
    )
    assert "membership_status = 'ACTIVE'" in _postgresql_where(fifo)
    assert "queue_role = 'RETURN_QUEUE'" in _postgresql_where(fifo)
    assert "e13_claim_intent_id IS NULL" in _postgresql_where(fifo)
