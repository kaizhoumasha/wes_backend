"""E12/E13 RuntimeIntent 批次成员投影。"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import BigInteger, CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint, text
from sqlmodel import Field

from src.app.runtime.orchestration.execution_session import RUNTIME_SCHEMA
from src.core.mixins.base import BaseMixin


class WmsConveyorBatchMember(BaseMixin, table=True):
    """以 RuntimeIntentLog 为唯一批次根，不复制 ACK 或 lease。"""

    __tablename__ = "wms_conveyor_batch_members"  # pyright: ignore[reportAssignmentType]
    __schema__ = RUNTIME_SCHEMA
    __table_args__: ClassVar[tuple[object, ...]] = (
        ForeignKeyConstraint(
            ["runtime_intent_log_id"],
            [f"{RUNTIME_SCHEMA}.runtime_intent_logs.id"],
            name="fk_wms_conveyor_batch_members_intent",
        ),
        ForeignKeyConstraint(
            ["route_instance_id"],
            [f"{RUNTIME_SCHEMA}.bin_route_instances.route_instance_id"],
            name="fk_wms_conveyor_batch_members_route",
        ),
        ForeignKeyConstraint(
            ["source_queue_membership_id"],
            [f"{RUNTIME_SCHEMA}.conveyor_queue_memberships.id"],
            name="fk_wms_conveyor_batch_members_source_membership",
        ),
        UniqueConstraint(
            "runtime_intent_log_id",
            "sequence_no",
            name="uq_wms_conveyor_batch_members_intent_sequence",
        ),
        UniqueConstraint(
            "runtime_intent_log_id",
            "route_instance_id",
            name="uq_wms_conveyor_batch_members_intent_route",
        ),
        CheckConstraint(
            "sequence_no > 0 AND (reserved_queue_position IS NULL OR reserved_queue_position > 0)",
            name="sequence",
        ),
        CheckConstraint(
            "("
            "direction = 'INBOUND' AND source_queue_membership_id IS NULL "
            "AND reserved_queue_position IS NOT NULL"
            ") OR ("
            "direction = 'RETURN' AND source_queue_membership_id IS NOT NULL "
            "AND reserved_queue_position IS NULL"
            ")",
            name="direction_shape",
        ),
        CheckConstraint(
            "("
            "member_state = 'CANDIDATE' AND accepted_at_ms IS NULL AND released_at_ms IS NULL "
            "AND terminal_at_ms IS NULL AND terminal_outcome IS NULL"
            ") OR ("
            "member_state = 'ACCEPTED' AND accepted_at_ms IS NOT NULL AND released_at_ms IS NULL "
            "AND terminal_at_ms IS NULL AND terminal_outcome IS NULL"
            ") OR ("
            "member_state = 'RELEASED' AND accepted_at_ms IS NULL AND released_at_ms IS NOT NULL "
            "AND terminal_at_ms IS NULL AND terminal_outcome IS NULL"
            ") OR ("
            "member_state = 'TERMINAL' AND accepted_at_ms IS NOT NULL AND released_at_ms IS NULL "
            "AND terminal_at_ms IS NOT NULL AND terminal_outcome IS NOT NULL"
            ")",
            name="lifecycle",
        ),
        Index(
            "ux_wms_conveyor_batch_members_active_inbound_position",
            "workline_id",
            "queue_code",
            "reserved_queue_position",
            unique=True,
            postgresql_where=text("direction = 'INBOUND' AND member_state IN ('CANDIDATE', 'ACCEPTED')"),
            sqlite_where=text("direction = 'INBOUND' AND member_state IN ('CANDIDATE', 'ACCEPTED')"),
        ),
        Index(
            "ux_wms_conveyor_batch_members_active_source_membership",
            "source_queue_membership_id",
            unique=True,
            postgresql_where=text(
                "source_queue_membership_id IS NOT NULL AND member_state IN ('CANDIDATE', 'ACCEPTED')"
            ),
            sqlite_where=text("source_queue_membership_id IS NOT NULL AND member_state IN ('CANDIDATE', 'ACCEPTED')"),
        ),
        {"schema": RUNTIME_SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)
    runtime_intent_log_id: int
    route_instance_id: str = Field(min_length=1, max_length=160, index=True)
    source_queue_membership_id: int | None = Field(default=None)
    workline_id: int
    queue_code: str = Field(min_length=1, max_length=80)
    direction: str = Field(min_length=1, max_length=20)
    sequence_no: int = Field(ge=1)
    bin_code: str = Field(min_length=1, max_length=100)
    reserved_queue_position: int | None = Field(default=None, ge=1)
    member_state: str = Field(default="CANDIDATE", min_length=1, max_length=20)
    staged_at_ms: int = Field(sa_type=BigInteger)
    accepted_at_ms: int | None = Field(default=None, sa_type=BigInteger)
    released_at_ms: int | None = Field(default=None, sa_type=BigInteger)
    terminal_at_ms: int | None = Field(default=None, sa_type=BigInteger)
    terminal_outcome: str | None = Field(default=None, max_length=80)


__all__ = ["WmsConveyorBatchMember"]
