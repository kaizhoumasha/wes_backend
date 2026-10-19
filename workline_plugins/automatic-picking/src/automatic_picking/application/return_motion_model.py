"""当前实际架面的运动前求值事实；结束不代表物理 EMPTY 或已运动。"""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import JSON, CheckConstraint, UniqueConstraint
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class AutomaticPickingReturnMotion(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "automatic_picking_return_motions"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint("departure_operation_id", name="ux_auto_motion_departure_operation"),
        UniqueConstraint("departure_transport_task_id", name="ux_auto_motion_departure_transport"),
        CheckConstraint(
            "(departure_operation_id IS NULL AND departure_transport_task_id IS NULL AND departure_result_evidence_id IS NULL AND departure_retry_at IS NULL) OR (departure_operation_id IS NOT NULL AND ((departure_transport_task_id IS NULL AND departure_result_evidence_id IS NULL) OR (departure_transport_task_id IS NOT NULL AND departure_retry_at IS NULL)))",
            name="automatic_motion_departure_binding",
        ),
        UniqueConstraint("rotation_transport_task_id", name="ux_auto_motion_rotation_transport"),
        UniqueConstraint(
            "workline_id", "arrival_evidence_id", "rack_id", "rack_face", name="ux_automatic_return_motion_face"
        ),
        CheckConstraint(
            "(inbound_client_request_id IS NULL AND round_started_at IS NULL AND round_causal_token IS NULL "
            "AND source_feed_id IS NULL AND service_end_evidence_id IS NULL) OR "
            "(inbound_client_request_id IS NOT NULL AND round_started_at IS NOT NULL AND round_causal_token IS NOT NULL AND round_causal_token > 0 "
            "AND source_feed_id IS NOT NULL AND service_end_evidence_id IS NOT NULL)",
            name="automatic_return_motion_source_binding",
        ),
        {"schema": SchemaType.BIZ.value},
    )
    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", sa_type=SQL_COMPAT_BIGINT)
    arrival_evidence_id: int = Field(foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT)
    source_evidence_id: int = Field(foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT)
    rack_id: str = Field(max_length=100)
    rack_face: str = Field(max_length=10)
    # 已结束阶段只需保留 execution 身份，不以已关闭分配的 FK 阻止历史清理。
    allocation_id: int | None = Field(default=None, sa_type=SQL_COMPAT_BIGINT)
    # 当前恢复锚点，标量引用不把已闭合 Feed/原进场历史变成运行依赖。
    inbound_client_request_id: str | None = Field(default=None, max_length=120)
    round_started_at: datetime | None = None
    round_causal_token: int | None = Field(default=None, sa_type=SQL_COMPAT_BIGINT)
    source_feed_id: int | None = Field(default=None, sa_type=SQL_COMPAT_BIGINT)
    completed_source_member_ids: list[int] = Field(default_factory=list, sa_type=JSON)
    service_end_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
    rotation_transport_task_id: str | None = Field(default=None, max_length=80)
    rotation_result_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
    departure_retry_at: datetime | None = None
    departure_operation_id: str | None = Field(default=None, max_length=120)
    departure_transport_task_id: str | None = Field(default=None, max_length=80)
    departure_result_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
    closed_at: datetime | None = None
    archived_at: datetime | None = None
