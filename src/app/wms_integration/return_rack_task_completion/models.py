"""已应用的整架业务完成事实；不依赖可清理的 Evidence 历史查询。"""

from typing import ClassVar

from sqlalchemy import BigInteger, CheckConstraint, Index
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT


class ReturnRackTaskCompletion(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "return_rack_task_completions"
    __schema__ = "wes_biz"
    __table_args__ = (
        Index("ux_return_rack_task_completion", "picking_task_id", "rack_id", unique=True),
        CheckConstraint("through_plan_revision > 0", name="return_rack_completion_revision_positive"),
        {"schema": "wes_biz"},
    )
    picking_task_id: int = Field(foreign_key="wes_biz.picking_tasks.id", sa_type=SQL_COMPAT_BIGINT)
    operation_id: str = Field(max_length=36)
    rack_id: str = Field(max_length=100)
    through_plan_revision: int = Field(sa_type=BigInteger)
    completed_at_ms: int = Field(sa_type=BigInteger)
    source_evidence_id: int = Field(sa_type=SQL_COMPAT_BIGINT)
