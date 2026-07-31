"""料箱在本地输送线中的单调 route authority。"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import BigInteger, CheckConstraint, ForeignKeyConstraint, Index, text
from sqlmodel import Field

from src.app.runtime.orchestration.execution_session import RUNTIME_SCHEMA
from src.core.mixins.base import BaseMixin


class BinRouteInstance(BaseMixin, table=True):
    """保存当前 route 节点；append-only 历史继续复用 RuntimeLocationEvent。"""

    __tablename__ = "bin_route_instances"  # pyright: ignore[reportAssignmentType]
    __schema__ = RUNTIME_SCHEMA
    __table_args__: ClassVar[tuple[object, ...]] = (
        ForeignKeyConstraint(
            ["created_by_e12_intent_id"],
            [f"{RUNTIME_SCHEMA}.runtime_intent_logs.id"],
            name="fk_bin_route_instances_e12_intent",
        ),
        ForeignKeyConstraint(
            ["reconciliation_case_id"],
            [f"{RUNTIME_SCHEMA}.reconciliation_cases.id"],
            name="fk_bin_route_instances_reconciliation_case",
        ),
        CheckConstraint("route_version > 0", name="version"),
        CheckConstraint(
            "current_node IN ("
            "'FIVE_RACK', "
            "'CTU_INBOUND_IN_FLIGHT', "
            "'CONVEYOR_ENTRY', "
            "'SCAN1', "
            "'SCAN2_WORK', "
            "'SCAN3', "
            "'NG_LINE', "
            "'RETURN_QUEUE', "
            "'CTU_RETURN_IN_FLIGHT'"
            ")",
            name="node",
        ),
        CheckConstraint(
            "("
            "lifecycle_state = 'ACTIVE' AND closed_at_ms IS NULL AND reconciliation_case_id IS NULL"
            ") OR ("
            "lifecycle_state = 'CLOSED' AND closed_at_ms IS NOT NULL "
            "AND reconciliation_case_id IS NULL AND current_node IN ('NG_LINE', 'FIVE_RACK')"
            ") OR ("
            "lifecycle_state = 'RECONCILING' AND closed_at_ms IS NULL AND reconciliation_case_id IS NOT NULL"
            ")",
            name="lifecycle",
        ),
        CheckConstraint(
            "(current_rack_code IS NULL AND current_slot_code IS NULL) "
            "OR (current_rack_code IS NOT NULL AND current_slot_code IS NOT NULL)",
            name="location_shape",
        ),
        Index(
            "ux_bin_route_instances_active_bin",
            "bin_code",
            unique=True,
            postgresql_where=text("lifecycle_state IN ('ACTIVE', 'RECONCILING')"),
            sqlite_where=text("lifecycle_state IN ('ACTIVE', 'RECONCILING')"),
        ),
        {"schema": RUNTIME_SCHEMA},
    )

    route_instance_id: str = Field(primary_key=True, min_length=1, max_length=160)
    bin_code: str = Field(min_length=1, max_length=100)
    workline_id: int
    created_by_e12_intent_id: int = Field(index=True)
    current_node: str = Field(min_length=1, max_length=40)
    route_version: int = Field(default=1, ge=1)
    lifecycle_state: str = Field(default="ACTIVE", min_length=1, max_length=20)
    current_rack_code: str | None = Field(default=None, max_length=100)
    current_slot_code: str | None = Field(default=None, max_length=100)
    last_transition_source: str = Field(min_length=1, max_length=80)
    last_transition_source_event_id: str = Field(min_length=1, max_length=240)
    closed_at_ms: int | None = Field(default=None, sa_type=BigInteger)
    reconciliation_case_id: int | None = Field(default=None, index=True)


__all__ = ["BinRouteInstance"]
