"""设备静态身份与 WorkLine 物理拓扑。"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal

from pydantic import AfterValidator
from sqlalchemy import JSON, Column
from sqlmodel import Field, Index, Relationship

from src.app.device.endpoint import validate_device_endpoint_base_url
from src.app.workline.models.workline import WorkLine  # noqa: TC001 - 保证 mapper 注册
from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, SoftDeleteMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType

DeviceEndpointBaseUrl = Annotated[str, AfterValidator(validate_device_endpoint_base_url)]


class DeviceBase(BaseMixin):
    """不承载通信、认证或运行态的设备主数据。"""

    device_code: str = Field(min_length=1, max_length=100, index=True, description="独立命令资源编码")
    device_name: str = Field(min_length=1, max_length=100, description="设备名称")
    work_line_id: int | None = Field(default=None, foreign_key="wes_biz.work_lines.id")
    description: str | None = Field(default=None, max_length=500)
    is_active: bool = Field(default=True, description="是否允许进入新运行代际")
    sort_order: int = Field(default=0)
    device_role: str = Field(min_length=1, max_length=50, description="物理拓扑角色")
    role_index: int = Field(default=1, ge=1)
    upstream_device_id: int | None = Field(
        default=None,
        foreign_key="wes_biz.devices.id",
        ondelete="SET NULL",
    )
    diagnostic_profile: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    endpoint_base_url: DeviceEndpointBaseUrl | None = Field(default=None, max_length=255)


class Device(DeviceBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):
    """设备静态主数据；实时状态只来自 ECS observation。"""

    __tablename__: ClassVar[Literal["devices"]] = "devices"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index(
            "ux_devices_device_code_deleted",
            "device_code",
            unique=True,
            postgresql_where="NOT is_deleted",
        ),
    )

    work_line: WorkLine = Relationship(sa_relationship_kwargs={"lazy": "selectin"})
    upstream_device: Device = Relationship(
        sa_relationship_kwargs={
            "remote_side": "Device.id",
            "foreign_keys": "[Device.upstream_device_id]",
            "primaryjoin": "Device.upstream_device_id == Device.id",
        }
    )


class DeviceEditableBase(BaseMixin):
    """可维护的静态设备字段。"""

    device_code: str = Field(min_length=1, max_length=100)
    device_name: str = Field(min_length=1, max_length=100)
    work_line_id: int | None = None
    description: str | None = Field(default=None, max_length=500)
    is_active: bool = True
    sort_order: int = 0
    device_role: str = Field(min_length=1, max_length=50)
    role_index: int = Field(default=1, ge=1)
    upstream_device_id: int | None = None
    diagnostic_profile: dict[str, Any] = Field(default_factory=dict)
    endpoint_base_url: DeviceEndpointBaseUrl | None = Field(default=None, max_length=255)


class DeviceCreate(ModelFactory(DeviceBase).for_create()):
    """设备创建合同。"""


class DeviceUpdate(ModelFactory(DeviceEditableBase).for_optimistic_update()):
    """设备静态主数据更新合同。"""


class DeviceResponse(DeviceBase):
    """设备静态主数据响应。"""

    id: int
    version: int


__all__ = ["Device", "DeviceBase", "DeviceCreate", "DeviceEditableBase", "DeviceResponse", "DeviceUpdate"]
