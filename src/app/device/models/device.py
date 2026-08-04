"""
设备基础信息模型 (Device)

用于管理设备的注册、配置和状态监控。
专注设备本身的信息，不涉及指令执行和事件日志。

基础能力边界:
- @docs/architecture/device-command-contract.md
- @docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md
"""

from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Literal, cast

from sqlalchemy import JSON, Column
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field, Index, Relationship

from src.app.workline.models.workline import WorkLine  # noqa: TC001 - runtime import keeps WorkLine mapper registered
from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, SoftDeleteMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType

# ==================== 枚举定义 ====================


class DeviceProtocol(str, Enum):
    """收敛前实现基线；目标架构只保留当前 HTTP 需求，厂商协议由对应 Adapter 独立拥有。"""

    HTTP = "HTTP"
    HTTPS = "HTTPS"
    TCP = "TCP"
    MODBUS = "MODBUS"
    MQTT = "MQTT"


class DeviceStatus(str, Enum):
    """WES 共享设备状态枚举；厂商状态映射由对应 Adapter 拥有。"""

    IDLE = "IDLE"  # 空闲，可接收新任务
    RUNNING = "RUNNING"  # 忙碌，正在执行任务
    ERROR = "ERROR"  # 故障，需人工介入
    OFFLINE = "OFFLINE"  # 离线（WES 判定）
    MAINTENANCE = "MAINTENANCE"  # 人工维护中


# ==================== 基础字段 (用于 Schema 复用) ====================


class DeviceBase(BaseMixin):
    """设备基础字段 - 用于 Schema 复用"""

    # ===== 基本信息 =====
    device_code: str = Field(
        min_length=1,
        max_length=50,
        index=True,
        description="设备编码（业务主键）",
    )
    device_name: str = Field(min_length=1, max_length=100, description="设备名称")

    work_line_id: int | None = Field(
        default=None,
        foreign_key="wes_biz.work_lines.id",
        description="所属作业线 ID",
    )
    description: str | None = Field(default=None, max_length=500, description="设备用途说明")
    is_active: bool = Field(default=True, description="是否启用")
    sort_order: int = Field(default=0, description="排序顺序")

    # ===== 角色和拓扑（当前设备基础数据）=====
    device_role: str = Field(
        max_length=50,
        description="设备业务角色（SCANNER, ROBOT_ARM, XRAY, CONVEYOR）",
    )
    role_index: int = Field(
        default=1,
        ge=1,
        description="同角色序号（1, 2, 3...）",
    )
    upstream_device_id: int | None = Field(
        default=None,
        foreign_key="wes_biz.devices.id",
        ondelete="SET NULL",
        description="上游设备ID（物理路径辅助信息）",
    )

    # ===== 厂商和能力（Adapter 边界）=====
    vendor_type: str | None = Field(
        default=None,
        max_length=50,
        description="厂商类型（ECS, KEYENCE, FANUC...）",
    )
    capabilities_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="设备能力声明（支持事件、命令、回调等）",
    )

    # ===== 通信配置（Adapter 连接参数）=====
    host: str | None = Field(default=None, max_length=100, description="设备 IP 地址")
    port: int | None = Field(default=None, ge=1, le=65535, description="服务端口")

    # 🔥 使用 VARCHAR + CHECK 约束
    protocol: DeviceProtocol = Field(
        default=DeviceProtocol.HTTP,
        sa_type=cast(
            "Any",
            SQLAEnum(
                DeviceProtocol,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="通信协议",
    )

    auth_token: str | None = Field(default=None, max_length=500, description="认证 Token（Bearer Token）")
    timeout: int = Field(default=10000, ge=1000, le=300000, description="请求超时时间（毫秒，默认 10s）")
    callback_path: str | None = Field(default=None, max_length=255, description="设备侧回调/命令接收路径覆盖")

    # ===== 设备状态（WES 共享投影）=====
    # 🔥 使用 VARCHAR + CHECK 约束
    device_status: DeviceStatus = Field(
        default=DeviceStatus.IDLE,
        sa_type=cast(
            "Any",
            SQLAEnum(
                DeviceStatus,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="设备实时状态（IDLE/RUNNING/ERROR/OFFLINE/MAINTENANCE）",
    )

    current_command_id: int | None = Field(default=None, description="当前执行的指令 ID（关联 DeviceCommand.id）")
    last_heartbeat_at: datetime | None = Field(default=None, description="最后心跳时间")
    error_code: str | None = Field(default=None, max_length=50, description="错误代码（status=ERROR 时）")
    maintenance_mode: bool = Field(default=False, description="是否处于维护模式（维护中不参与正常编排）")

    # ===== 能力配置（单硬件任务治理）=====
    max_concurrent_tasks: int = Field(default=1, ge=1, le=1, description="固定为 1：单设备同一时间只允许一个硬件任务")

    # ===== 幂等性配置（设备命令可靠性边界）=====
    idempotency_ttl: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="指令去重缓存时间（秒，默认 1 小时）",
    )

    # ===== 诊断配置（排错平台） =====
    diagnostic_profile: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="设备诊断配置（责任角色、显示偏好、扩展属性）",
    )


# ==================== 数据库表模型 ====================


class Device(
    DeviceBase,
    EnterpriseMixin,
    SoftDeleteMixin,
    DataTableMixin,
    table=True,
):
    """
    设备数据库表模型

    设备是作业线上的具体设备实例，用于执行具体的作业任务。

    字段说明:
    - 基本信息: device_code, device_name, work_line_id
    - 角色和拓扑: device_role, role_index, upstream_device_id
    - 厂商与能力: vendor_type, capabilities_json
    - 通信配置: host, port, protocol, auth_token, timeout, callback_path（由 Adapter 使用）
    - 设备状态: device_status, current_command_id, last_heartbeat_at,
      error_code, maintenance_mode（WES 共享投影）
    - 能力配置: max_concurrent_tasks（核心单设备并发约束）
    - 幂等性配置: idempotency_ttl（设备命令可靠性边界）
    - 诊断配置: diagnostic_profile

    架构设计参考:
    - device_role: 业务角色（SCANNER, ROBOT_ARM, XRAY），用于插件按角色选设备
    - role_index: 同角色多设备序号（如 ROBOT_ARM_1, ROBOT_ARM_2）
    - upstream_device_id: 物理路径辅助信息；插件配置事实以角色和能力为准
    - plugin_key/contract_version 的唯一来源是 WorkLine，而非 Device

    注意:
    - WES 回调端点固定在路由中: /api/v1/callback/result, /api/v1/callback/event
    - 设备方需自行配置回调地址（即 WES 服务的访问地址）

    关系:
    - work_line: 所属作业线（多对一）
    - upstream_device: 上游设备（多对一）
    - downstream_devices: 下游设备（一对多，隐式关系）
    - commands: 设备指令（一对多，在 command.py 中定义）
    """

    __tablename__: ClassVar[Literal["devices"]] = "devices"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value  # 业务数据表

    # 部分唯一索引：软删除后可重用 device_code
    __table_args__ = (
        Index(
            "ux_devices_device_code_deleted",
            "device_code",
            unique=True,
            postgresql_where="NOT is_deleted",
        ),
    )

    # 关系定义
    work_line: "WorkLine" = Relationship(sa_relationship_kwargs={"lazy": "selectin"})
    upstream_device: "Device" = Relationship(
        sa_relationship_kwargs={
            "remote_side": "Device.id",
            "foreign_keys": "[Device.upstream_device_id]",
            "primaryjoin": "Device.upstream_device_id == Device.id",
        }
    )

    @property
    def communication_profile(self) -> dict[str, Any]:
        """供派发与诊断链路复用的通信配置快照。"""

        protocol_value = getattr(self.protocol, "value", self.protocol)
        return {
            "protocol": protocol_value,
            "host": self.host,
            "port": self.port,
            "timeout": self.timeout,
            "callback_path": self.callback_path,
        }


# ==================== 自动生成的 Schema ====================


class DeviceEditableBase(BaseMixin):
    """普通设备编辑字段；不包含运行态投影字段。"""

    device_code: str = Field(min_length=1, max_length=50, description="设备编码（业务主键）")
    device_name: str = Field(min_length=1, max_length=100, description="设备名称")
    work_line_id: int | None = Field(default=None, description="所属作业线 ID")
    description: str | None = Field(default=None, max_length=500, description="设备用途说明")
    is_active: bool = Field(default=True, description="是否启用")
    sort_order: int = Field(default=0, description="排序顺序")
    device_role: str = Field(max_length=50, description="设备业务角色（SCANNER, ROBOT_ARM, XRAY, CONVEYOR）")
    role_index: int = Field(default=1, ge=1, description="同角色序号（1, 2, 3...）")
    upstream_device_id: int | None = Field(default=None, description="上游设备ID（物理路径辅助信息）")
    vendor_type: str | None = Field(default=None, max_length=50, description="厂商类型（ECS, KEYENCE, FANUC...）")
    capabilities_json: dict[str, Any] = Field(
        default_factory=dict, description="设备能力声明（支持事件、命令、回调等）"
    )
    host: str | None = Field(default=None, max_length=100, description="设备 IP 地址")
    port: int | None = Field(default=None, ge=1, le=65535, description="服务端口")
    protocol: DeviceProtocol = Field(default=DeviceProtocol.HTTP, description="通信协议")
    auth_token: str | None = Field(default=None, max_length=500, description="认证 Token（Bearer Token）")
    timeout: int = Field(default=10000, ge=1000, le=300000, description="请求超时时间（毫秒，默认 10s）")
    callback_path: str | None = Field(default=None, max_length=255, description="设备侧回调/命令接收路径覆盖")
    idempotency_ttl: int = Field(default=3600, ge=60, le=86400, description="指令去重缓存时间（秒，默认 1 小时）")
    diagnostic_profile: dict[str, Any] = Field(
        default_factory=dict, description="设备诊断配置（责任角色、显示偏好、扩展属性）"
    )


class DeviceCreate(ModelFactory(DeviceBase).for_create()):
    """设备创建 Schema - 接收客户端输入"""


class DeviceUpdate(ModelFactory(DeviceEditableBase).for_optimistic_update()):
    """设备更新 Schema - 只允许主数据与通信配置，运行态走专用操作"""


class DeviceResponse(DeviceBase):
    """设备响应 Schema - 返回给客户端"""

    id: int
    version: int


# ==================== 导出 ====================


__all__ = [
    "Device",
    "DeviceBase",
    "DeviceCreate",
    "DeviceEditableBase",
    "DeviceProtocol",
    "DeviceResponse",
    "DeviceStatus",
    "DeviceUpdate",
]
