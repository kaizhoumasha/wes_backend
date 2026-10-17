"""当前实际架面的运动前求值事实；结束不代表物理 EMPTY 或已运动。"""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import UniqueConstraint
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class AutomaticPickingReturnMotion(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "automatic_picking_return_motions"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint(
            "workline_id", "arrival_evidence_id", "rack_id", "rack_face", name="ux_automatic_return_motion_face"
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
    closed_at: datetime | None = None
    archived_at: datetime | None = None
