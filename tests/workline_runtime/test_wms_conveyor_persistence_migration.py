"""WMS 履约领域关系迁移合同。"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PARENT_REVISION = "36aa187238cc"
REVISION = "f9ffbef8992a"


def _revision_module():
    config = Config(PROJECT_ROOT / "alembic.ini")
    script_directory = ScriptDirectory.from_config(config)
    script = script_directory.get_revision(REVISION)
    assert script is not None
    assert script.down_revision == PARENT_REVISION
    return script.module


def _offline_sql(revision_module, operation: str) -> str:
    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )
    revision_module.op = Operations(context)
    getattr(revision_module, operation)()
    return output.getvalue()


def test_upgrade_creates_four_relations_and_extends_queue_membership() -> None:
    revision_module = _revision_module()

    sql = _offline_sql(revision_module, "upgrade")

    for table_name in (
        "wms_rack_demands",
        "material_flow_owners",
        "bin_route_instances",
        "wms_conveyor_batch_members",
    ):
        assert f"CREATE TABLE wes_runtime.{table_name}" in sql
    assert "demand_key" not in sql
    assert "current_queue_membership_id" not in sql
    assert "route_instance_id IS NOT NULL AND membership_status IN ('ACTIVE', 'RECONCILING')" in sql
    assert "ix_bin_route_instances_workline_node" not in sql
    assert "ix_wms_conveyor_batch_members_intent_state" not in sql
    for constraint_name in (
        "ck_wms_rack_demands_root_shape",
        "ck_material_flow_owners_object_type",
        "ck_bin_route_instances_location_shape",
        "ck_conveyor_queue_memberships_return_shape",
        "ck_conveyor_queue_memberships_claim_shape",
        "ck_wms_conveyor_batch_members_direction_shape",
    ):
        assert f"CONSTRAINT {constraint_name} " in sql
    for column_name in (
        "route_instance_id",
        "scan3_enqueued_at",
        "queue_position",
        "e13_claim_intent_id",
        "e13_claim_token",
        "e13_claim_until",
    ):
        assert f"ALTER TABLE wes_runtime.conveyor_queue_memberships ADD COLUMN {column_name}" in sql
    assert "UPDATE wes_runtime." not in sql
    assert "INSERT INTO wes_runtime." not in sql


def test_downgrade_reverses_queue_extension_and_drops_four_relations() -> None:
    revision_module = _revision_module()

    sql = _offline_sql(revision_module, "downgrade")

    for column_name in (
        "route_instance_id",
        "scan3_enqueued_at",
        "queue_position",
        "e13_claim_intent_id",
        "e13_claim_token",
        "e13_claim_until",
    ):
        assert f"ALTER TABLE wes_runtime.conveyor_queue_memberships DROP COLUMN {column_name}" in sql
    for table_name in (
        "wms_conveyor_batch_members",
        "bin_route_instances",
        "material_flow_owners",
        "wms_rack_demands",
    ):
        assert f"DROP TABLE wes_runtime.{table_name}" in sql
