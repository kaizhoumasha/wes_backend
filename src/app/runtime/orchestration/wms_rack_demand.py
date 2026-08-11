"""rack supply 货架需求根互斥投影。"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import BigInteger, CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint, text
from sqlmodel import Field

from src.app.runtime.orchestration.execution_session import RUNTIME_SCHEMA
from src.core.mixins.base import BaseMixin


class WmsRackDemand(BaseMixin, table=True):
    """同一工作线站点与货架类型只保留一个未关闭的 rack supply 需求根。"""

    __tablename__ = "wms_rack_demands"  # pyright: ignore[reportAssignmentType]
    __schema__ = RUNTIME_SCHEMA
    __table_args__: ClassVar[tuple[object, ...]] = (
        ForeignKeyConstraint(
            ["root_intent_id"],
            [f"{RUNTIME_SCHEMA}.runtime_intent_logs.id"],
            name="fk_wms_rack_demands_root_intent",
        ),
        ForeignKeyConstraint(
            ["reconciliation_case_id"],
            [f"{RUNTIME_SCHEMA}.reconciliation_cases.id"],
            name="fk_wms_rack_demands_reconciliation_case",
        ),
        UniqueConstraint("root_intent_id", name="uq_wms_rack_demands_root_intent"),
        UniqueConstraint(
            "workline_id",
            "station_code",
            "rack_type",
            "demand_generation",
            name="uq_wms_rack_demands_generation",
        ),
        CheckConstraint("demand_generation > 0", name="generation"),
        CheckConstraint(
            "("
            "lifecycle_state = 'PREPARING' AND root_intent_id IS NULL "
            "AND closed_at_ms IS NULL AND reconciliation_case_id IS NULL"
            ") OR ("
            "lifecycle_state = 'ACTIVE' AND root_intent_id IS NOT NULL "
            "AND closed_at_ms IS NULL AND reconciliation_case_id IS NULL"
            ") OR ("
            "lifecycle_state = 'CLOSED' AND root_intent_id IS NOT NULL "
            "AND closed_at_ms IS NOT NULL AND reconciliation_case_id IS NULL"
            ") OR ("
            "lifecycle_state = 'RECONCILING' AND root_intent_id IS NOT NULL "
            "AND closed_at_ms IS NULL AND reconciliation_case_id IS NOT NULL"
            ")",
            name="lifecycle",
        ),
        Index(
            "ux_wms_rack_demands_active_station_rack_type",
            "workline_id",
            "station_code",
            "rack_type",
            unique=True,
            postgresql_where=text("lifecycle_state IN ('PREPARING', 'ACTIVE', 'RECONCILING')"),
            sqlite_where=text("lifecycle_state IN ('PREPARING', 'ACTIVE', 'RECONCILING')"),
        ),
        {"schema": RUNTIME_SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)
    workline_id: int
    # station_code 的稳定作用域是单条工作线，因此所有唯一键都包含 workline_id。
    station_code: str = Field(min_length=1, max_length=100)
    rack_type: str = Field(min_length=1, max_length=80)
    demand_generation: int = Field(ge=1)
    # PREPARING 先占 demand mutex；同事务 claim RuntimeIntent 后再绑定 root 并转 ACTIVE。
    root_intent_id: int | None = None
    lifecycle_state: str = Field(default="ACTIVE", min_length=1, max_length=20)
    opened_at_ms: int = Field(sa_type=BigInteger)
    closed_at_ms: int | None = Field(default=None, sa_type=BigInteger)
    reconciliation_case_id: int | None = Field(default=None, index=True)


__all__ = ["WmsRackDemand"]
