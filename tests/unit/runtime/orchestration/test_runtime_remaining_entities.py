"""Runtime 剩余实体 + H5 idempotency_keys contract test。

ExecutionWorkItem (对象级执行令牌) + RuntimeTimeline (append-only 轨迹) +
RuntimeHold (运行时闸门) + IdempotencyKey (H5 幂等键表)。
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.exc import NoReferencedTableError
from sqlmodel import SQLModel

from src.app.runtime.orchestration.conveyor_queue_membership import ConveyorQueueMembership
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.idempotency_key import IdempotencyKey
from src.app.runtime.orchestration.runtime_hold import RuntimeHold
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.runtime_timeline import RuntimeTimeline
from src.database.schema_conf import get_all_schemas, validate_schema
from src.database.sqlite_schema import configure_sqlite_schemas

REMAINING_RUNTIME_MODELS = (
    ExecutionWorkItem,
    RuntimeInbox,
    RuntimeIntentLog,
    RuntimeTimeline,
    RuntimeHold,
    ConveyorQueueMembership,
    IdempotencyKey,
)
REMAINING_RUNTIME_TABLES = {
    "execution_work_items",
    "runtime_inbox",
    "runtime_intent_logs",
    "runtime_timelines",
    "runtime_holds",
    "conveyor_queue_memberships",
    "idempotency_keys",
}

# ---- ExecutionWorkItem ----


def test_execution_work_item_table_name():
    assert ExecutionWorkItem.__tablename__ == "execution_work_items"


def test_remaining_runtime_tables_use_runtime_schema():
    """剩余 runtime/orchestration 表必须注册到 wes_runtime schema。"""
    for model in REMAINING_RUNTIME_MODELS:
        assert model.__table__.schema == "wes_runtime"


def _foreign_key_targets(model: type, column_name: str) -> set[str]:
    return {fk.target_fullname for fk in model.__table__.c[column_name].foreign_keys}


def test_remaining_runtime_tables_use_runtime_foreign_keys():
    """剩余 runtime/orchestration 表必须用 wes_runtime 域内 FK 串联聚合根和 correlation。"""
    assert _foreign_key_targets(ExecutionWorkItem, "execution_session_id") == {"wes_runtime.execution_sessions.id"}
    assert _foreign_key_targets(ExecutionWorkItem, "correlation_id") == {
        "wes_runtime.execution_correlations.correlation_id"
    }
    assert _foreign_key_targets(ExecutionWorkItem, "parent_correlation_id") == {
        "wes_runtime.execution_work_items.correlation_id"
    }
    assert _foreign_key_targets(RuntimeInbox, "execution_session_id") == {"wes_runtime.execution_sessions.id"}
    assert _foreign_key_targets(RuntimeInbox, "correlation_id") == {"wes_runtime.execution_correlations.correlation_id"}
    assert _foreign_key_targets(RuntimeIntentLog, "execution_session_id") == {"wes_runtime.execution_sessions.id"}
    assert _foreign_key_targets(RuntimeIntentLog, "correlation_id") == {
        "wes_runtime.execution_correlations.correlation_id"
    }
    assert _foreign_key_targets(RuntimeTimeline, "execution_session_id") == {"wes_runtime.execution_sessions.id"}
    assert _foreign_key_targets(RuntimeTimeline, "correlation_id") == {
        "wes_runtime.execution_correlations.correlation_id"
    }
    assert _foreign_key_targets(RuntimeHold, "execution_session_id") == {"wes_runtime.execution_sessions.id"}
    assert _foreign_key_targets(RuntimeHold, "correlation_id") == {"wes_runtime.execution_correlations.correlation_id"}
    assert _foreign_key_targets(ConveyorQueueMembership, "correlation_id") == {
        "wes_runtime.execution_correlations.correlation_id"
    }
    assert _foreign_key_targets(IdempotencyKey, "execution_correlation_id") == {
        "wes_runtime.execution_correlations.correlation_id"
    }


def test_execution_work_item_correlation_id_is_table_unique_constraint():
    """parent_correlation_id 自引用 FK 需要 correlation_id 在 CREATE TABLE 阶段已唯一。"""
    constraints = {
        constraint.name: [column.name for column in constraint.columns]
        for constraint in ExecutionWorkItem.__table__.constraints
        if constraint.name
    }
    assert constraints["uq_wes_runtime_execution_work_items_correlation_id"] == ["correlation_id"]


def test_remaining_runtime_models_can_create_all_after_single_model_import():
    """单独导入 remaining runtime 模型时也必须带入 FK 目标表元数据。"""
    engine = create_engine("sqlite:///:memory:")
    configure_sqlite_schemas(engine)
    try:
        SQLModel.metadata.create_all(engine)
    except NoReferencedTableError as exc:  # pragma: no cover - assertion path includes original error
        pytest.fail(f"runtime model FK target was not registered: {exc}")
    finally:
        SQLModel.metadata.drop_all(engine)
        engine.dispose()


def test_runtime_models_keep_shared_metadata_when_imported_before_base_mixin():
    """runtime 表即使先于 BaseMixin 导入, 也必须注册到项目共享 SQLModel.metadata。"""
    script = """
from sqlmodel import SQLModel

from src.app.runtime.orchestration.conveyor_queue_membership import ConveyorQueueMembership
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.idempotency_key import IdempotencyKey
from src.app.runtime.orchestration.runtime_hold import RuntimeHold
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.runtime_timeline import RuntimeTimeline
from src.core.mixins.base import BaseMixin

models = (
    ExecutionSession,
    ExecutionCorrelation,
    ExecutionWorkItem,
    RuntimeInbox,
    RuntimeIntentLog,
    RuntimeTimeline,
    RuntimeHold,
    ConveyorQueueMembership,
    IdempotencyKey,
)
for model in models:
    assert model.__table__.metadata is SQLModel.metadata, model.__name__
assert BaseMixin.metadata is SQLModel.metadata
expected_tables = {
    "wes_runtime.execution_sessions",
    "wes_runtime.execution_correlations",
    "wes_runtime.execution_work_items",
    "wes_runtime.runtime_inbox",
    "wes_runtime.runtime_intent_logs",
    "wes_runtime.runtime_timelines",
    "wes_runtime.runtime_holds",
    "wes_runtime.conveyor_queue_memberships",
    "wes_runtime.idempotency_keys",
}
assert expected_tables <= set(SQLModel.metadata.tables)
"""
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_runtime_model_aggregate_registers_workline_for_mapper_configuration():
    """新 runtime model 聚合入口必须可独立完成 WorklineSession mapper 配置。"""
    script = """
from sqlalchemy.orm import configure_mappers

import src.app.runtime.orchestration.models as runtime_models

assert runtime_models.WorklineSession.__name__ == "WorklineSession"
configure_mappers()
"""
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_runtime_inbox_source_event_identity_is_unique_when_present():
    """provider_code + event_type + source_event_id 是入站事件幂等身份。"""
    index = next(
        index for index in RuntimeInbox.__table__.indexes if index.name == "ux_wes_runtime_runtime_inbox_source_event"
    )
    assert index.unique is True
    assert [column.name for column in index.columns] == ["provider_code", "event_type", "source_event_id"]
    assert str(index.dialect_options["postgresql"]["where"]) == "source_event_id IS NOT NULL"


def test_runtime_schema_registered_for_database_initialization():
    """wes_runtime 必须进入 central schema 列表, 供 PostgreSQL 初始化和 SQLite attach 使用。"""
    assert "wes_runtime" in get_all_schemas()
    assert validate_schema("wes_runtime")


def test_remaining_runtime_migration_creates_all_tables():
    """Alembic revision 必须覆盖剩余 runtime/orchestration 表和 wes_runtime schema。"""
    migration_files = list(Path("migrations/versions").glob("*_add_remaining_runtime_orchestration_.py"))
    assert len(migration_files) == 1

    migration = migration_files[0].read_text(encoding="utf-8")
    assert 'SCHEMA = "wes_runtime"' in migration
    assert "CREATE SCHEMA IF NOT EXISTS" in migration
    for table_name in REMAINING_RUNTIME_TABLES:
        assert f'"{table_name}"' in migration

    assert 'sa.PrimaryKeyConstraint("provider_code", "operation_kind", "idempotency_key")' in migration
    assert 'f"ix_wes_runtime_runtime_inbox_{column_name}"' in migration
    assert '"status"' in migration
    assert "ux_wes_runtime_runtime_inbox_source_event" in migration
    assert "source_event_id IS NOT NULL" in migration
    assert "ux_wes_runtime_conveyor_queue_memberships_active_bin" in migration
    assert "ux_wes_runtime_conveyor_queue_memberships_active_placeholder" in migration
    assert "membership_status = 'ACTIVE'" in migration
    assert 'f"ix_wes_runtime_runtime_intent_logs_{column_name}"' in migration
    assert '"idempotency_key"' in migration
    assert "ix_wes_runtime_idempotency_keys_execution_correlation_id" in migration
    assert (
        'sa.UniqueConstraint("correlation_id", name="uq_wes_runtime_execution_work_items_correlation_id")' in migration
    )
    assert "sa.ForeignKeyConstraint" in migration
    assert "{SCHEMA}.execution_sessions.id" in migration
    assert "{SCHEMA}.execution_correlations.correlation_id" in migration


def test_millisecond_epoch_columns_use_bigint():
    """毫秒 epoch 字段必须用 BIGINT, 避免 PostgreSQL 32-bit INTEGER 溢出。"""
    expected = {
        RuntimeTimeline: ("occurred_at",),
        RuntimeHold: ("resolved_at",),
        ConveyorQueueMembership: ("entered_at", "left_at"),
        IdempotencyKey: ("created_at",),
    }
    for model, column_names in expected.items():
        for column_name in column_names:
            assert isinstance(model.__table__.c[column_name].type, BigInteger)

    migration_files = list(Path("migrations/versions").glob("*_add_remaining_runtime_orchestration_.py"))
    migration = migration_files[0].read_text(encoding="utf-8")
    for column_name in ("occurred_at", "resolved_at", "entered_at", "left_at", "created_at"):
        assert f'sa.Column("{column_name}", sa.BigInteger()' in migration
        assert f'sa.Column("{column_name}", sa.Integer()' not in migration


def test_execution_work_item_required_fields():
    """必填: execution_session_id / correlation_id / object_type / object_key / current_step。"""
    item = ExecutionWorkItem(
        execution_session_id=1,
        correlation_id="corr-wi-001",
        object_type="bin",
        object_key="BIN-01",
        current_step="SCAN_BARCODE",
    )
    assert item.step_status == "PENDING"
    assert item.parent_correlation_id is None
    assert item.lease_expires_at is None


def test_execution_work_item_step_status_5_states():
    """step_status: PENDING / IN_PROGRESS / COMPLETED / FAILED / SKIPPED。"""
    valid = {"PENDING", "IN_PROGRESS", "COMPLETED", "FAILED", "SKIPPED"}
    for state in valid:
        item = ExecutionWorkItem(
            execution_session_id=1,
            correlation_id="c",
            object_type="material",
            object_key="M001",
            current_step="step",
            step_status=state,
        )
        assert item.step_status == state


# ---- RuntimeTimeline ----


def test_runtime_timeline_table_name():
    assert RuntimeTimeline.__tablename__ == "runtime_timelines"


def test_runtime_timeline_required_fields():
    """必填: execution_session_id / trace_id / event_type / occurred_at。"""
    entry = RuntimeTimeline(
        execution_session_id=1,
        trace_id="trace-001",
        event_type="INBOX_RECEIVED",
        occurred_at=1700000000000,
    )
    assert entry.correlation_id is None  # 可空


# ---- RuntimeHold ----


def test_runtime_hold_table_name():
    assert RuntimeHold.__tablename__ == "runtime_holds"


def test_runtime_hold_required_fields():
    """必填: execution_session_id / reason / hold_type / scope_type / scope_key。"""
    hold = RuntimeHold(
        execution_session_id=1,
        reason="RESOURCE_WAIT",
        hold_type="RESOURCE_WAIT",
        scope_type="WORK_ITEM",
        scope_key="wi-001",
    )
    assert hold.resolved_at is None
    assert hold.allowed_next_effect_scope is None


def test_runtime_hold_scope_type_7_values():
    """scope_type: WORK_ITEM / OBJECT / DEVICE / RESOURCE / QUEUE / SESSION / WORKLINE。"""
    valid = {"WORK_ITEM", "OBJECT", "DEVICE", "RESOURCE", "QUEUE", "SESSION", "WORKLINE"}
    for scope in valid:
        hold = RuntimeHold(
            execution_session_id=1,
            reason="r",
            hold_type="t",
            scope_type=scope,
            scope_key="k",
        )
        assert hold.scope_type == scope


# ---- IdempotencyKey (H5) ----


def test_idempotency_key_table_name():
    assert IdempotencyKey.__tablename__ == "idempotency_keys"


def test_idempotency_key_composite_primary_key():
    """复合主键: (provider_code, operation_kind, idempotency_key) (主计划 §5.4)。"""
    key = IdempotencyKey(
        provider_code="WMS",
        operation_kind="fulfillment",
        idempotency_key="WES-FULFILLMENT-abc123",
        execution_correlation_id="corr-001",
        request_hash="sha256-hash",
        created_at=1700000000000,
    )
    assert key.provider_code == "WMS"
    assert key.operation_kind == "fulfillment"
    assert key.idempotency_key == "WES-FULFILLMENT-abc123"
    assert key.business_owner_key is None


def test_idempotency_key_request_hash_immutable():
    """request_hash 必填 (主计划 §5.4 immutable payload hash)。"""
    key = IdempotencyKey(
        provider_code="ECS",
        operation_kind="device_dispatch",
        idempotency_key="WES-DEVICE-xyz",
        execution_correlation_id="corr-002",
        request_hash="sha256-xyz",
        created_at=1700000000000,
    )
    assert key.request_hash == "sha256-xyz"


# ---- ConveyorQueueMembership (CEO-008) ----


def test_conveyor_queue_membership_table_name():
    assert ConveyorQueueMembership.__tablename__ == "conveyor_queue_memberships"


def test_conveyor_queue_membership_required_fields():
    membership = ConveyorQueueMembership(
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="ENTRY_SCAN_QUEUE",
        queue_role="SCAN",
        entered_at=1700000000000,
        bin_code="BIN-01",
    )
    assert membership.membership_status == "ACTIVE"
    assert membership.left_at is None
    assert membership.evidence_json == {}


def test_conveyor_queue_membership_placeholder_identity_supported():
    membership = ConveyorQueueMembership(
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="ENTRY_SCAN_QUEUE",
        queue_role="SCAN",
        entered_at=1700000000000,
        placeholder_key="placeholder:scan:1",
    )
    assert membership.placeholder_key == "placeholder:scan:1"
    assert membership.bin_code is None


def test_conveyor_queue_membership_active_bin_unique_per_workline():
    index = next(
        index
        for index in ConveyorQueueMembership.__table__.indexes
        if index.name == "ux_wes_runtime_conveyor_queue_memberships_active_bin"
    )
    assert index.unique is True
    assert [column.name for column in index.columns] == ["workline_id", "bin_code"]
    assert str(index.dialect_options["postgresql"]["where"]) == "bin_code IS NOT NULL AND membership_status = 'ACTIVE'"


def test_conveyor_queue_membership_active_placeholder_unique_per_workline():
    index = next(
        index
        for index in ConveyorQueueMembership.__table__.indexes
        if index.name == "ux_wes_runtime_conveyor_queue_memberships_active_placeholder"
    )
    assert index.unique is True
    assert [column.name for column in index.columns] == ["workline_id", "placeholder_key"]
    assert (
        str(index.dialect_options["postgresql"]["where"])
        == "placeholder_key IS NOT NULL AND membership_status = 'ACTIVE'"
    )


def test_conveyor_queue_membership_manifest_queue_code_is_string_not_enum():
    field = ConveyorQueueMembership.model_fields["queue_code"]
    assert field.annotation is str
