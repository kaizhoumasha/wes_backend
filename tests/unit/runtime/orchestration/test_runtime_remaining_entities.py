"""Phase 1 CEO-007 剩余 3 实体 + H5 idempotency_keys contract test。

ExecutionWorkItem (对象级执行令牌) + RuntimeTimeline (append-only 轨迹) +
RuntimeHold (运行时闸门) + IdempotencyKey (H5 幂等键表)。
"""

from __future__ import annotations

from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.idempotency_key import IdempotencyKey
from src.app.runtime.orchestration.runtime_hold import RuntimeHold
from src.app.runtime.orchestration.runtime_timeline import RuntimeTimeline

# ---- ExecutionWorkItem ----


def test_execution_work_item_table_name():
    assert ExecutionWorkItem.__tablename__ == "execution_work_items"


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
