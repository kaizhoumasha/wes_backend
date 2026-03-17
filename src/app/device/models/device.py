"""
设备基础信息模型 (Device)

用于管理设备的注册、配置和状态监控。
专注设备本身的信息，不涉及指令执行和事件日志。

相关文档:
- 白皮书: @docs/third_party_integration_whitepaper.md
- SRS: @docs/SRS.md (第 3.3.0 节 - 设备层次结构)
"""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Literal

from sqlalchemy import JSON, Column, Enum as SQLAEnum
from sqlmodel import Field, Index, Relationship

from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, SoftDeleteMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType

if TYPE_CHECKING:
    from src.app.workline.models.workline import WorkLine


# ==================== 枚举定义 ====================


class DeviceType(str, Enum):
    """设备类型枚举 (SRS 3.3.0 节)"""

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


class DeviceProtocol(str, Enum):
    """设备通信协议枚举（白皮书 2.1 节）"""

    HTTP = "HTTP"
    HTTPS = "HTTPS"
    TCP = "TCP"
    MODBUS = "MODBUS"
    MQTT = "MQTT"


class DeviceStatus(str, Enum):
    """设备状态枚举（白皮书 5.2 节）"""

    IDLE = "IDLE"  # 空闲，可接收新任务
    RUNNING = "RUNNING"  # 忙碌，正在执行任务
    ERROR = "ERROR"  # 故障，需人工介入
    OFFLINE = "OFFLINE"  # 离线（WES 判定）


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

    # 🔥 使用 VARCHAR + CHECK 约束
    device_type: DeviceType = Field(
        sa_type=SQLAEnum(
            DeviceType,
            native_enum=False,
            create_constraint=True,
            length=50,
        ),
        description="设备类型",
    )

    work_line_id: int | None = Field(
        default=None,
        foreign_key="wes_biz.work_lines.id",
        description="所属作业线 ID",
    )
    description: str | None = Field(default=None, max_length=500, description="设备用途说明")
    is_active: bool = Field(default=True, description="是否启用")
    sort_order: int = Field(default=0, description="排序顺序")

    # ===== 角色和拓扑（架构 8.2 节）=====
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
        description="上游设备ID（线性拓扑）",
    )

    # ===== 厂商和能力（架构 8.2 节）=====
    vendor_type: str | None = Field(
        default=None,
        max_length=50,
        description="厂商类型（ECS, KEYENCE, FANUC...）",
    )
    capabilities: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description="能力列表（业务能力，如 [SCAN, PICK, PUT]）",
    )

    # ===== 通信配置（白皮书 2.1-2.3 节）=====
    host: str | None = Field(default=None, max_length=100, description="设备 IP 地址")
    port: int | None = Field(default=None, ge=1, le=65535, description="服务端口")

    # 🔥 使用 VARCHAR + CHECK 约束
    protocol: DeviceProtocol = Field(
        default=DeviceProtocol.HTTP,
        sa_type=SQLAEnum(
            DeviceProtocol,
            native_enum=False,
            create_constraint=True,
            length=50,
        ),
        description="通信协议",
    )

    auth_token: str | None = Field(default=None, max_length=500, description="认证 Token（Bearer Token）")
    timeout: int = Field(default=10000, ge=1000, le=300000, description="请求超时时间（毫秒，默认 10s）")

    # ===== 设备状态（白皮书 3.1.2 节）=====
    # 🔥 使用 VARCHAR + CHECK 约束
    device_status: DeviceStatus = Field(
        default=DeviceStatus.IDLE,
        sa_type=SQLAEnum(
            DeviceStatus,
            native_enum=False,
            create_constraint=True,
            length=50,
        ),
        description="设备实时状态（IDLE/RUNNING/ERROR/OFFLINE）",
    )

    current_command_id: int | None = Field(default=None, description="当前执行的指令 ID（关联 DeviceCommand.id）")
    last_heartbeat_at: datetime | None = Field(default=None, description="最后心跳时间")
    error_code: str | None = Field(default=None, max_length=50, description="错误代码（status=ERROR 时）")

    # ===== 能力配置（白皮书 5.1 节）=====
    supported_commands: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description="支持的指令类型（PICK/PUT/SCAN/ROTATE/PROCESS）",
    )
    max_concurrent_tasks: int = Field(default=1, ge=1, le=10, description="最大并发任务数")

    # ===== 幂等性配置（白皮书 4.1 节）=====
    idempotency_ttl: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="指令去重缓存时间（秒，默认 1 小时）",
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
    - 基本信息: device_code, device_name, device_type, work_line_id
    - 角色和拓扑: device_role, role_index, upstream_device_id（架构 8.2 节）
    - 厂商和能力: vendor_type, capabilities（架构 8.2 节）
    - 通信配置: host, port, protocol, auth_token, timeout（白皮书 2.1-2.3 节）
    - 设备状态: device_status, current_command_id, last_heartbeat_at, error_code（白皮书 5.2 节）
    - 能力配置: supported_commands, max_concurrent_tasks（白皮书 5.1 节）
    - 幂等性配置: idempotency_ttl（白皮书 4.1 节）

    架构设计参考:
    - device_role: 业务角色（SCANNER, ROBOT_ARM, XRAY），用于插件按角色选设备
    - role_index: 同角色多设备序号（如 ROBOT_ARM_1, ROBOT_ARM_2）
    - upstream_device_id: 线性拓扑（符合 KISS 原则）
    - capabilities: 业务能力列表，区别于 device_type（设备类型）

    注意:
    - WES 回调端点固定在路由中: /api/v1/callback/result, /api/v1/callback/event
    - 设备方需自行配置回调地址（即 WES 服务的访问地址）

    关系:
    - work_line: 所属作业线（多对一）
    - upstream_device: 上游设备（多对一）
    - downstream_devices: 下游设备（一对多，隐式关系）
    - commands: 设备指令（一对多，在 command.py 中定义）
    - events: 设备事件（一对多，在 event_log.py 中定义）
    """

    __tablename__: Literal["devices"] = "devices"
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


# ==================== 自动生成的 Schema ====================


class DeviceCreate(ModelFactory(DeviceBase).for_create()):
    """设备创建 Schema - 接收客户端输入"""


class DeviceUpdate(ModelFactory(DeviceBase).for_optimistic_update()):
    """设备更新 Schema - 所有字段可选"""


class DeviceResponse(DeviceBase):
    """设备响应 Schema - 返回给客户端"""

    id: int
    version: int


# ==================== 导出 ====================


__all__ = [
    "Device",
    "DeviceBase",
    "DeviceCreate",
    "DeviceProtocol",
    "DeviceResponse",
    "DeviceStatus",
    "DeviceType",
    "DeviceUpdate",
]
