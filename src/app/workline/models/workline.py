"""
作业线相关模型

包含 WorkLine 数据库表模型和相关的 Pydantic Schemas
"""

from enum import Enum
from typing import Literal

from sqlmodel import Field

from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, SoftDeleteMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class LineType(str, Enum):
    """作业线类型枚举"""

    AUTO = "AUTO"  # 自动线
    MANUAL = "MANUAL"  # 人工线
    HYBRID = "HYBRID"  # 混合线


class WorkLineBase(BaseMixin):
    """作业线基础字段 - 用于 Schema 复用"""

    line_code: str = Field(
        min_length=1,
        max_length=50,
        index=True,
        description="作业线编码（业务主键）",
    )
    line_name: str = Field(min_length=1, max_length=100, description="作业线名称")
    line_type: str = Field(max_length=50, description="作业线类型")
    zone_name: str | None = Field(
        default=None,
        max_length=100,
        description="区域名称",
    )
    description: str | None = Field(default=None, max_length=500, description="作业线描述")
    is_active: bool = Field(default=True, description="是否启用")
    capacity: int | None = Field(default=None, description="产能（件/小时）")
    sort_order: int = Field(default=0, description="排序顺序")


class WorkLine(
    WorkLineBase,
    EnterpriseMixin,
    SoftDeleteMixin,
    DataTableMixin,
    table=True,
):
    """
    作业线数据库表模型

    作业线是生产线或工作站的抽象，用于组织和管理设备
    """

    __tablename__: Literal["work_lines"] = "work_lines"
    __schema__ = SchemaType.BIZ.value  # 业务数据表


class WorkLineCreate(ModelFactory(WorkLineBase).for_create()):
    """作业线创建 Schema - 接收客户端输入"""


class WorkLineUpdate(ModelFactory(WorkLineBase).for_optimistic_update()):
    """作业线更新 Schema - 所有字段可选"""


class WorkLineResponse(WorkLineBase):
    """作业线响应 Schema - 返回给客户端"""

    id: int
    version: int
