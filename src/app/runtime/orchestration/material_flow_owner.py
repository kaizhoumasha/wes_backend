"""货架或对象级 material-flow active owner 投影。"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import BigInteger, CheckConstraint, ForeignKeyConstraint, Index, text
from sqlmodel import Field

from src.app.runtime.orchestration.execution_session import RUNTIME_SCHEMA
from src.core.mixins.base import BaseMixin


class MaterialFlowOwner(BaseMixin, table=True):
    """以统一 typed-object identity 建立互斥 active owner，不引入 flow-root 状态源。"""

    __tablename__ = "material_flow_owners"  # pyright: ignore[reportAssignmentType]
    __schema__ = RUNTIME_SCHEMA
    __table_args__: ClassVar[tuple[object, ...]] = (
        ForeignKeyConstraint(
            ["owner_intent_id"],
            [f"{RUNTIME_SCHEMA}.runtime_intent_logs.id"],
            name="fk_material_flow_owners_intent",
        ),
        ForeignKeyConstraint(
            ["reconciliation_case_id"],
            [f"{RUNTIME_SCHEMA}.reconciliation_cases.id"],
            name="fk_material_flow_owners_reconciliation_case",
        ),
        CheckConstraint(
            "object_type IN ('RACK', 'RACK_FACE', 'BIN', 'OCCUPANCY')",
            name="object_type",
        ),
        CheckConstraint(
            "owner_type IN ('FULL_BOX_EXCHANGE', 'PIECE_SORTING')",
            name="owner_type",
        ),
        CheckConstraint(
            "("
            "lifecycle_state = 'ACTIVE' AND released_at_ms IS NULL AND reconciliation_case_id IS NULL"
            ") OR ("
            "lifecycle_state = 'RELEASED' AND released_at_ms IS NOT NULL AND reconciliation_case_id IS NULL"
            ") OR ("
            "lifecycle_state = 'RECONCILING' AND released_at_ms IS NULL AND reconciliation_case_id IS NOT NULL"
            ")",
            name="lifecycle",
        ),
        Index(
            "ux_material_flow_owners_active_object",
            "object_type",
            "object_key",
            unique=True,
            postgresql_where=text("lifecycle_state IN ('ACTIVE', 'RECONCILING')"),
            sqlite_where=text("lifecycle_state IN ('ACTIVE', 'RECONCILING')"),
        ),
        {"schema": RUNTIME_SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)
    workline_id: int
    object_type: str = Field(min_length=1, max_length=40)
    object_key: str = Field(min_length=1, max_length=300)
    owner_type: str = Field(min_length=1, max_length=40)
    owner_key: str = Field(min_length=1, max_length=300)
    owner_intent_id: int | None = Field(default=None, index=True)
    lifecycle_state: str = Field(default="ACTIVE", min_length=1, max_length=20)
    source_event_id: str = Field(min_length=1, max_length=240)
    acquired_at_ms: int = Field(sa_type=BigInteger)
    released_at_ms: int | None = Field(default=None, sa_type=BigInteger)
    reconciliation_case_id: int | None = Field(default=None, index=True)


__all__ = ["MaterialFlowOwner"]
