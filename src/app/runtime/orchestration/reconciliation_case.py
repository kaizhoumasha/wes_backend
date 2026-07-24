"""Capability EFFECT 独立对账 case。"""

from __future__ import annotations

from enum import Enum
from typing import Any, ClassVar, cast

from sqlalchemy import JSON, BigInteger, CheckConstraint, Column, ForeignKeyConstraint, Index, text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.app.runtime.orchestration.execution_session import RUNTIME_SCHEMA
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog  # noqa: F401
from src.core.mixins.base import BaseMixin


class ReconciliationCaseStatus(str, Enum):
    """对账 case 只允许单调地从 OPEN 进入 RESOLVED。"""

    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class ReconciliationCase(BaseMixin, table=True):
    """EFFECT evidence 冲突/未知的独立裁决对象。"""

    __tablename__ = "reconciliation_cases"  # pyright: ignore[reportAssignmentType]
    __schema__ = RUNTIME_SCHEMA
    __table_args__: ClassVar[tuple[object, ...]] = (
        ForeignKeyConstraint(
            ["runtime_intent_log_id"],
            [f"{RUNTIME_SCHEMA}.runtime_intent_logs.id"],
            name="fk_reconciliation_case_intent",
        ),
        CheckConstraint(
            "(status = 'OPEN' AND resolved_at_ms IS NULL) OR (status = 'RESOLVED' AND resolved_at_ms IS NOT NULL)",
            name="resolution_state",
        ),
        Index(
            "ux_reconciliation_cases_open_dispatch_key",
            "dispatch_key",
            unique=True,
            postgresql_where=text("status = 'OPEN'"),
            sqlite_where=text("status = 'OPEN'"),
        ),
        {"schema": RUNTIME_SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)
    runtime_intent_log_id: int = Field(index=True)
    dispatch_key: str = Field(min_length=1, max_length=240, index=True)
    status: ReconciliationCaseStatus = Field(
        default=ReconciliationCaseStatus.OPEN,
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(
                ReconciliationCaseStatus,
                name="reconciliation_case_status",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
        ),
    )
    reason_code: str = Field(min_length=1, max_length=120)
    evidence_history_json: list[dict[str, object]] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    decision_json: dict[str, object] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    opened_at_ms: int = Field(sa_type=BigInteger)
    resolved_at_ms: int | None = Field(default=None, sa_type=BigInteger)


__all__ = ["ReconciliationCase", "ReconciliationCaseStatus"]
