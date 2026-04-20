from __future__ import annotations

from datetime import datetime

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
