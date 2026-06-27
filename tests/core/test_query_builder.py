from __future__ import annotations

from datetime import UTC, datetime

from src.app.device.models.command import DeviceCommand
from src.app.sys.models.audit_log import AuditLog
from src.core.query_builder import QueryBuilder
from src.core.query_models import FilterCondition, FilterGroup, FilterOperator


def test_query_builder_coerces_datetime_filter_value_for_model_datetime_field() -> None:
    filter_group = FilterGroup(
        couple="and",
        conditions=[
            FilterCondition(
                field="opera_time",
                op=FilterOperator.GE,
                value="2026-04-13T02:50:25.502",
            )
        ],
    )

    clause = QueryBuilder(AuditLog).build_filters(filter_group)

    assert clause is not None
    bind_param = clause.right
    assert isinstance(bind_param.value, datetime)
    assert bind_param.value == datetime.fromisoformat("2026-04-13T02:50:25.502")


def test_query_builder_normalizes_iso_z_for_naive_datetime_field() -> None:
    filter_group = FilterGroup(
        couple="and",
        conditions=[
            FilterCondition(
                field="created_at",
                op=FilterOperator.GE,
                value="2026-04-13T02:50:25.502Z",
            )
        ],
    )

    clause = QueryBuilder(DeviceCommand).build_filters(filter_group)

    assert clause is not None
    bind_param = clause.right
    assert isinstance(bind_param.value, datetime)
    assert bind_param.value == datetime(2026, 4, 13, 2, 50, 25, 502000)
    assert bind_param.value.tzinfo is None


def test_query_builder_normalizes_offset_for_timezone_aware_field() -> None:
    filter_group = FilterGroup(
        couple="and",
        conditions=[
            FilterCondition(
                field="opera_time",
                op=FilterOperator.GE,
                value="2026-04-13T10:50:25.502+08:00",
            )
        ],
    )

    clause = QueryBuilder(AuditLog).build_filters(filter_group)

    assert clause is not None
    bind_param = clause.right
    assert isinstance(bind_param.value, datetime)
    assert bind_param.value == datetime(2026, 4, 13, 2, 50, 25, 502000, tzinfo=UTC)
