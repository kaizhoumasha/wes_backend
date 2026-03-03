"""
设备相关模型

包含 Device 数据库表模型和相关的 Pydantic Schemas
"""

from enum import Enum
from typing import Literal

from sqlmodel import Field, Relationship

from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, SoftDeleteMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class DeviceType(str, Enum):
    """设备类型枚举"""

    PDA = "PDA"  # PDA
    INDUSTRIAL_PC = "INDUSTRIAL_PC"  # 工业电脑
    PRINTER = "PRINTER"  # 打印机
    COMPUTER = "COMPUTER"  # 电脑
    LCR_TESTER = "LCR_TESTER"  # LCR测试仪
    ROBOTIC_ARM = "ROBOTIC_ARM"  # 机械臂
    VISION_CAMERA = "VISION_CAMERA"  # 视觉相机
    CONVEYOR = "CONVEYOR"  # 输送线
    LABELER = "LABELER"  # 贴标机
    XRAY = "XRAY"  # X-Ray
    SCANNER = "SCANNER"  # 扫码器


class DeviceBase(BaseMixin):
    """设备基础字段 - 用于 Schema 复用"""

    device_code: str = Field(
        min_length=1,
        max_length=50,
        index=True,
        description="设备编码（业务主键）",
    )
    device_name: str = Field(min_length=1, max_length=100, description="设备名称")
    device_type: str = Field(max_length=50, description="设备类型")
    work_line_id: int | None = Field(
        default=None,
        foreign_key="wes_biz.work_lines.id",
        description="所属作业线 ID",
    )
    description: str | None = Field(default=None, max_length=500, description="设备用途说明")
    is_active: bool = Field(default=True, description="是否启用")
    sort_order: int = Field(default=0, description="排序顺序")


class Device(
    DeviceBase,
    EnterpriseMixin,
    SoftDeleteMixin,
    DataTableMixin,
    table=True,
):
    """
    设备数据库表模型

    设备是作业线上的具体设备实例，用于执行具体的作业任务
    """

    __tablename__: Literal["devices"] = "devices"
    __schema__ = SchemaType.BIZ.value  # 业务数据表

    # 关系定义
    work_line: "WorkLine" = Relationship(  # noqa: F821
        sa_relationship_kwargs={"lazy": "selectin"}
    )


class DeviceCreate(ModelFactory(DeviceBase).for_create()):
    """设备创建 Schema - 接收客户端输入"""


class DeviceUpdate(ModelFactory(DeviceBase).for_update()):
    """设备更新 Schema - 所有字段可选"""


class DeviceResponse(DeviceBase):
    """设备响应 Schema - 返回给客户端"""

    id: int
