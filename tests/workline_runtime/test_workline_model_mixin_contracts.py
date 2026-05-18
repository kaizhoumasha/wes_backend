"""WorkLine 表模型 Mixin 分层约束测试。"""

from typing import Any, cast

ENTERPRISE_STATE_COLUMNS = {"version", "created_by", "updated_by"}
SOFT_DELETE_COLUMNS = {"deleted_by", "deleted_at", "is_deleted"}


def _table_columns(model: type[Any]) -> set[str]:
    return set(cast("Any", model).__table__.c.keys())


def test_workline_table_models_follow_strict_mixin_categories() -> None:
    """WorkLine 表模型必须按主数据、运行时状态机、恢复生命周期分层选择 Mixin。"""

    from src.app.workline.models import (
        NgReturnItem,
        RuntimeHold,
        WorkLine,
        WorklineDiagnostic,
        WorklineDispatchAttempt,
        WorklineInbox,
        WorklineOutbox,
        WorklineSafetyIncident,
        WorklineSession,
        WorklineTimeline,
    )

    runtime_state_models = (
        WorklineInbox,
        WorklineOutbox,
        WorklineSession,
        WorklineTimeline,
        WorklineDispatchAttempt,
        WorklineDiagnostic,
    )
    lifecycle_models = (RuntimeHold, NgReturnItem, WorklineSafetyIncident)

    workline_columns = _table_columns(WorkLine)
    assert ENTERPRISE_STATE_COLUMNS.issubset(workline_columns)
    assert SOFT_DELETE_COLUMNS.issubset(workline_columns)

    for model in runtime_state_models:
        columns = _table_columns(model)
        assert ENTERPRISE_STATE_COLUMNS.isdisjoint(columns), model.__name__
        assert SOFT_DELETE_COLUMNS.isdisjoint(columns), model.__name__

    for model in lifecycle_models:
        columns = _table_columns(model)
        assert ENTERPRISE_STATE_COLUMNS.issubset(columns), model.__name__
        assert SOFT_DELETE_COLUMNS.isdisjoint(columns), model.__name__


def test_outbox_base_does_not_redeclare_mixin_timestamps() -> None:
    """Base 字段层不应重新声明 DataTableMixin 已提供的时间戳字段。"""

    from src.app.workline.models import WorklineOutbox, WorklineOutboxBase, WorklineOutboxCreate

    base_annotations = WorklineOutboxBase.__annotations__

    assert "created_at" not in base_annotations
    assert "updated_at" not in base_annotations
    assert "created_at" in _table_columns(WorklineOutbox)
    assert "updated_at" in _table_columns(WorklineOutbox)
    assert "created_at" not in WorklineOutboxCreate.model_fields
    assert "updated_at" not in WorklineOutboxCreate.model_fields
