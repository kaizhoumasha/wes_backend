"""ExecutionCorrelation + ExecutionSession 模型 contract test。

主计划 §9.2 + target-state schema 对齐:
- correlation_id 唯一 (跨域 stable correlation key)
- execution_session_id 可空 (NULL 允许 inbound callback 未解析前 ACK)
- trace_id + source_event_id + business_owner_key 跨域 trace/审计
- ExecutionSession workline_id + manifest_version pin (CEO-011)
- state lifecycle (CREATED/RUNNING/HOLD/CLOSED/RECONCILING)
- ExecutionCorrelation FK ExecutionSession (域内强 FK, 跨域无)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.app.runtime.orchestration.execution_correlation import (
    ExecutionCorrelation,
)
from src.app.runtime.orchestration.execution_session import (
    ExecutionSession,
)

# ---- ExecutionSession (聚合根) ----


def test_execution_session_table_name():
    """表名 execution_sessions (主计划 §9.2 SQLModel 路径)。"""
    assert ExecutionSession.__tablename__ == "execution_sessions"


def test_execution_session_uses_runtime_schema():
    """ExecutionSession 必须落在 wes_runtime schema, 避免注册到默认 schema。"""
    assert ExecutionSession.__table__.schema == "wes_runtime"


def test_execution_session_required_fields():
    """必填字段: workline_id + manifest_version + state。"""
    session = ExecutionSession(
        workline_id=1,
        manifest_version="v1.0.0",
        state="CREATED",
    )
    assert session.workline_id == 1
    assert session.manifest_version == "v1.0.0"
    assert session.state == "CREATED"


def test_execution_session_extra_forbid():
    """验证路径 extra='forbid' 阻断未声明字段 (H4 一致)。"""
    with pytest.raises(ValidationError):
        ExecutionSession.model_validate(
            {
                "workline_id": 1,
                "manifest_version": "v1.0.0",
                "state": "CREATED",
                "unknown_field": "x",
            }
        )


def test_execution_session_orm_constructor_ignores_unknown_fields():
    """table=True ORM 构造器不负责 extra forbid; 契约验证走 model_validate。"""
    session = ExecutionSession(
        workline_id=1,
        manifest_version="v1.0.0",
        state="CREATED",
        unknown_field="x",  # type: ignore[call-arg]
    )
    assert not hasattr(session, "unknown_field")


def test_execution_session_model_validate_accepts_declared_fields():
    """model_validate 对声明字段正常通过, 证明 extra 检查没有阻断合法输入。"""
    session = ExecutionSession.model_validate(
        {
            "workline_id": 1,
            "manifest_version": "v1.0.0",
            "state": "CREATED",
        }
    )
    assert session.workline_id == 1


# ---- ExecutionCorrelation (跨域 stable key) ----


def test_execution_correlation_table_name():
    """表名 execution_correlations。"""
    assert ExecutionCorrelation.__tablename__ == "execution_correlations"


def test_execution_correlation_uses_runtime_schema():
    """ExecutionCorrelation 必须落在 wes_runtime schema, FK 也应在该 schema 内解析。"""
    assert ExecutionCorrelation.__table__.schema == "wes_runtime"


def test_execution_correlation_required_fields():
    """必填: correlation_id + trace_id (session_id 可空)。"""
    corr = ExecutionCorrelation(
        correlation_id="corr-2026-06-26-001",
        trace_id="trace-001",
    )
    assert corr.correlation_id == "corr-2026-06-26-001"
    assert corr.trace_id == "trace-001"
    assert corr.execution_session_id is None  # 跨域未解析前可空


def test_execution_correlation_with_session_id():
    """execution_session_id 可赋值, runtime 域内强 FK。"""
    corr = ExecutionCorrelation(
        correlation_id="corr-002",
        trace_id="trace-002",
        execution_session_id=42,
        source_event_id="WMS_GRN_RECEIVED_evt-123",
        business_owner_key="workline_WL-A",
    )
    assert corr.execution_session_id == 42
    assert corr.source_event_id == "WMS_GRN_RECEIVED_evt-123"
    assert corr.business_owner_key == "workline_WL-A"


def test_execution_correlation_unique_correlation_id():
    """correlation_id 是唯一键 (主计划 §9.2 跨域 stable correlation key)。"""
    field = ExecutionCorrelation.model_fields["correlation_id"]
    assert field.unique is True, "correlation_id 必须 unique=True (跨域 stable key)"


def test_execution_correlation_extra_forbid():
    """验证路径 extra='forbid' 阻断未声明字段 (H4 一致, 防止 PII/owner_id 注入)。"""
    with pytest.raises(ValidationError):
        ExecutionCorrelation.model_validate(
            {
                "correlation_id": "corr-x",
                "trace_id": "trace-x",
                "tenant_user_id": "u-internal",
            }
        )


def test_execution_correlation_orm_constructor_ignores_unknown_fields():
    """table=True ORM 构造器不负责 extra forbid; 契约验证走 model_validate。"""
    corr = ExecutionCorrelation(
        correlation_id="corr-x",
        trace_id="trace-x",
        tenant_user_id="u-internal",  # type: ignore[call-arg]
    )
    assert not hasattr(corr, "tenant_user_id")


# ---- Session + Correlation 关系 ----


def test_execution_correlation_can_reference_session_id():
    """execution_correlation.execution_session_id 引用 ExecutionSession.id (域内强 FK)。"""
    foreign_keys = list(ExecutionCorrelation.__table__.c.execution_session_id.foreign_keys)
    assert len(foreign_keys) == 1
    assert foreign_keys[0].target_fullname == "wes_runtime.execution_sessions.id"


def test_execution_schema_migration_creates_execution_tables():
    """Alembic revision 必须覆盖 wes_runtime schema + 两张 runtime/orchestration 表。"""
    migration_files = list(Path("migrations/versions").glob("*_add_execution_session_correlation.py"))
    assert len(migration_files) == 1

    migration = migration_files[0].read_text(encoding="utf-8")
    assert 'SCHEMA = "wes_runtime"' in migration
    assert "CREATE SCHEMA IF NOT EXISTS" in migration
    assert "op.create_table(" in migration
    assert '"execution_sessions"' in migration
    assert '"execution_correlations"' in migration
    assert 'EXECUTION_CORRELATION_SESSION_FK = "fk_exec_corr_session"' in migration
    assert len("fk_exec_corr_session") <= 63
    assert "wes_runtime.execution_sessions.id" in migration
