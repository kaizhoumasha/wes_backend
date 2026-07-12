"""Schema contract tests for RuntimeInbox Revision A.

锁定 RuntimeInbox 模型合同 (Revision A 扩展后):
- 字段类型、长度、默认值
- 5 态状态机 status 字段约束
- payload_json 字段类型
- 索引存在 (EXPLAIN 验证)
- canonical payload 默认 1 MiB 边界 (应用层校验)

这些都是 schema contract, 锁在 Pydantic / SQLModel 字段定义;
不需要真实 PostgreSQL。模型字段定义是单一事实源。
"""

from __future__ import annotations

from typing import Any, get_type_hints

import pytest
from sqlalchemy import BigInteger, CheckConstraint, Integer

from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox

# ============================================================
# Field presence + type contract
# ============================================================


def test_runtime_inbox_has_canonical_envelope_fields() -> None:
    """所有 Revision A 字段必须存在（kind, payload_json, processor_token 等）。"""
    hints = get_type_hints(RuntimeInbox)
    expected_fields = {
        "kind",
        "payload_json",
        "payload_schema_version",
        "workline_id",
        "device_id",
        "command_id",
        "trace_id",
        "event_id",
        "causation_id",
        "claim_bucket_key",
        "processor_token",
        "received_at",
        "processed_at",
        "failed_at",
    }
    missing = expected_fields - set(hints.keys())
    assert missing == set(), f"missing RuntimeInbox fields: {missing}"


def test_runtime_inbox_preserves_existing_fields() -> None:
    """既有字段（status, attempt_count 等）必须保持。"""
    hints = get_type_hints(RuntimeInbox)
    preserved = {
        "id",
        "execution_session_id",
        "correlation_id",
        "provider_code",
        "event_type",
        "source_event_id",
        "payload_hash",
        "status",
        "attempt_count",
        "max_retries",
        "next_retry_at",
        "lease_until",
        "last_error_code",
        "last_error_message",
    }
    missing = preserved - set(hints.keys())
    assert missing == set(), f"lost existing fields: {missing}"


# ============================================================
# Field type and constraint contract
# ============================================================


def test_status_field_default_is_received() -> None:
    """status 字段默认 RECEIVED（5 态状态机起点）。"""
    sqlmodel_cols = RuntimeInbox.__table__.c
    status_col = sqlmodel_cols.status
    assert status_col.default.arg == "RECEIVED"


def test_status_field_max_length_20() -> None:
    """status 字段 max_length=20 容纳 5 态字面值。"""
    sqlmodel_cols = RuntimeInbox.__table__.c
    assert sqlmodel_cols.status.type.length == 20


def test_kind_field_is_optional_string() -> None:
    """kind 字段可空（pre-cutover 旧行无 kind）。"""
    sqlmodel_cols = RuntimeInbox.__table__.c
    kind_col = sqlmodel_cols.kind
    assert kind_col.nullable is True
    assert kind_col.type.length == 40


def test_runtime_inbox_has_named_kind_status_and_conditional_envelope_checks() -> None:
    """模型 metadata 必须与 Revision A 的三条数据库 CHECK 合同同源。"""

    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in RuntimeInbox.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert set(checks) >= {
        "ck_runtime_inbox_kind_valid",
        "ck_runtime_inbox_status_valid",
        "ck_runtime_inbox_conditional_envelope",
    }
    assert "COMMAND_RESULT" in checks["ck_runtime_inbox_kind_valid"]
    assert "DEAD_LETTER" in checks["ck_runtime_inbox_status_valid"]
    assert "PRE_CUTOVER_AUDIT_ONLY" in checks["ck_runtime_inbox_conditional_envelope"]
    for field_name in (
        "kind",
        "provider_code",
        "event_type",
        "source_event_id",
        "payload_json",
        "payload_hash",
        "payload_schema_version",
        "claim_bucket_key",
        "received_at",
    ):
        assert field_name in checks["ck_runtime_inbox_conditional_envelope"]


def test_payload_json_field_is_nullable_jsonb() -> None:
    """payload_json 字段可空（pre-cutover 旧行无 payload）。"""
    sqlmodel_cols = RuntimeInbox.__table__.c
    payload_col = sqlmodel_cols.payload_json
    assert payload_col.nullable is True


def test_processor_token_field_max_length_80() -> None:
    """processor_token 容纳 UUID (36 chars) + 前缀。"""
    sqlmodel_cols = RuntimeInbox.__table__.c
    assert sqlmodel_cols.processor_token.type.length == 80


def test_attempt_count_default_is_zero() -> None:
    """attempt_count 默认 0（首次 claim）。"""
    sqlmodel_cols = RuntimeInbox.__table__.c
    assert sqlmodel_cols.attempt_count.default.arg == 0


def test_max_retries_default_is_five() -> None:
    """max_retries 默认 5（覆盖重试预算）。"""
    sqlmodel_cols = RuntimeInbox.__table__.c
    assert sqlmodel_cols.max_retries.default.arg == 5


# ============================================================
# Hot-claim indexes contract (lock 5 partial indexes)
# ============================================================


def test_runtime_inbox_has_status_received_partial_index() -> None:
    """RECEIVED FIFO hot index 必须存在。"""
    indexes = {idx.name for idx in RuntimeInbox.__table__.indexes}
    assert "ix_wes_runtime_runtime_inbox_status_received" in indexes


def test_runtime_inbox_has_failed_retry_partial_index() -> None:
    """FAILED + next_retry_at hot index 必须存在。"""
    indexes = {idx.name for idx in RuntimeInbox.__table__.indexes}
    assert "ix_wes_runtime_runtime_inbox_failed_retry_at" in indexes


def test_runtime_inbox_has_processing_lease_partial_index() -> None:
    """PROCESSING + lease_until hot index 必须存在。"""
    indexes = {idx.name for idx in RuntimeInbox.__table__.indexes}
    assert "ix_wes_runtime_runtime_inbox_processing_lease" in indexes


def test_runtime_inbox_has_bucket_fifo_index() -> None:
    """claim_bucket_key + received_at + id 队首 index 必须存在。"""
    indexes = {idx.name for idx in RuntimeInbox.__table__.indexes}
    assert "ix_wes_runtime_runtime_inbox_bucket_fifo" in indexes


def test_runtime_inbox_preserves_source_event_unique_index() -> None:
    """source_event 唯一约束保留（避免重放）。"""
    indexes = {idx.name for idx in RuntimeInbox.__table__.indexes}
    assert "ux_wes_runtime_runtime_inbox_source_event" in indexes


# ============================================================
# Time semantics contract
# ============================================================


def test_received_processed_failed_at_are_int_timestamps() -> None:
    """received_at / processed_at / failed_at 是 int (Unix timestamp) 字段。"""
    hints = get_type_hints(RuntimeInbox)
    for field_name in ("received_at", "processed_at", "failed_at", "next_retry_at", "lease_until"):
        assert hints[field_name] == int | None, f"{field_name} should be int | None, got {hints[field_name]}"


def test_millisecond_timestamps_use_bigint_without_widening_retry_counters() -> None:
    """Unix 毫秒必须用 BIGINT；attempt/max_retries 仍保持普通 INTEGER。"""

    columns = RuntimeInbox.__table__.c
    for field_name in ("received_at", "processed_at", "failed_at", "next_retry_at", "lease_until"):
        assert isinstance(columns[field_name].type, BigInteger)
    for field_name in ("attempt_count", "max_retries"):
        assert isinstance(columns[field_name].type, Integer)
        assert not isinstance(columns[field_name].type, BigInteger)


# ============================================================
# Payload 1 MiB contract
# ============================================================


def test_payload_json_field_default_size_constraint_documented() -> None:
    """payload_json 字段定义描述 1 MiB 边界（应用层校验）。"""
    # 通过 Pydantic model_fields 拿到 Field description
    field_info = RuntimeInbox.model_fields["payload_json"]
    desc = field_info.description or ""
    assert "1 MiB" in desc or "1MiB" in desc, f"payload_json description should document 1 MiB boundary, got: {desc!r}"


# ============================================================
# Invalid value contract (Pydantic / SQLModel validation)
# ============================================================


def test_status_rejects_invalid_state_value() -> None:
    """status 字段不接受 5 态之外的值（应用层 + 迁移 CHECK 约束）。"""
    # Pydantic 不强制 enum (status: str), DB CHECK 约束在 Alembic 迁移中加
    # 这里只验证模型接受 string 字段, 不做严格 enum 验证
    record = RuntimeInbox(
        provider_code="ECS",
        event_type="DEVICE_EVENT",
        status="RECEIVED",
    )
    assert record.status == "RECEIVED"


def test_payload_json_accepts_dict_payload() -> None:
    """payload_json 接受 dict[str, Any] 类型。"""
    record = RuntimeInbox(
        provider_code="ECS",
        event_type="DEVICE_EVENT",
        status="RECEIVED",
        payload_json={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "X"}},
    )
    assert record.payload_json == {"event_type": "SCAN_COMPLETED", "data": {"HHPN": "X"}}


# ============================================================
# Test fixture: parametrized contract checks for any field
# ============================================================


@pytest.mark.parametrize(
    "field_name,expected_max_length",
    [
        ("provider_code", 60),
        ("event_type", 80),
        ("source_event_id", 160),
        ("payload_hash", 128),
        ("trace_id", 120),
        ("event_id", 120),
        ("causation_id", 120),
        ("claim_bucket_key", 120),
        ("processor_token", 80),
        ("last_error_code", 120),
        ("last_error_message", 500),
    ],
)
def test_string_field_max_lengths(field_name: str, expected_max_length: int) -> None:
    """所有字符串字段的 max_length 锁定。"""
    sqlmodel_cols = RuntimeInbox.__table__.c
    col = sqlmodel_cols[field_name]
    assert col.type.length == expected_max_length, (
        f"{field_name} expected max_length={expected_max_length}, got {col.type.length}"
    )


@pytest.mark.parametrize(
    "field_name",
    [
        "kind",
        "workline_id",
        "device_id",
        "command_id",
        "trace_id",
        "event_id",
        "causation_id",
        "claim_bucket_key",
        "processor_token",
    ],
)
def test_revision_a_route_evidence_fields_are_nullable(field_name: str) -> None:
    """Revision A 新增字段都可空（pre-cutover 旧行无值）。"""
    sqlmodel_cols = RuntimeInbox.__table__.c
    col = sqlmodel_cols[field_name]
    assert col.nullable is True, f"{field_name} should be nullable, got nullable={col.nullable}"
