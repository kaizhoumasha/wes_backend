"""
作业线相关模型

包含 WorkLine 数据库表模型和相关的 Pydantic Schemas
"""

from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Literal, cast

from pydantic import BaseModel, field_validator
from sqlalchemy import JSON, Column, Text, text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.app.runtime.capability_catalog import WorklineCapabilityDefinition, get_workline_capability_definition
from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, SoftDeleteMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class LineType(str, Enum):
    """作业线类型枚举"""

    AUTO = "AUTO"  # 自动线
    MANUAL = "MANUAL"  # 人工线
    HYBRID = "HYBRID"  # 混合线


class WorkLineRunMode(str, Enum):
    """作业线运行模式枚举。"""

    AUTO = "AUTO"  # 自动运行，允许真实设备和外部副作用
    MANUAL = "MANUAL"  # 人工确认/人工介入
    SIMULATION = "SIMULATION"  # 沙箱模拟，派发到沙箱通道


class WorkLineBase(BaseMixin):
    """作业线基础字段 - 用于 Schema 复用"""

    line_code: str = Field(
        min_length=1,
        max_length=50,
        index=True,
        description="作业线编码（业务主键）",
    )
    line_name: str = Field(min_length=1, max_length=100, description="作业线名称")

    # 🔥 使用 VARCHAR + CHECK 约束
    line_type: LineType = Field(
        sa_type=cast(
            "Any",
            SQLAEnum(
                LineType,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="作业线类型",
    )

    zone_name: str | None = Field(
        default=None,
        max_length=100,
        description="区域名称",
    )
    plugin_key: str | None = Field(
        default=None,
        max_length=100,
        index=True,
        description="工作线执行插件标识",
    )
    contract_version: str | None = Field(
        default=None,
        max_length=50,
        description="工作线默认插件契约版本",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="工作线插件配置",
    )
    runtime_config_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="工作线运行时配置（重试、超时、会话归属等）",
    )
    run_mode: WorkLineRunMode = Field(
        default=WorkLineRunMode.AUTO,
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(
                WorkLineRunMode,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="工作线运行模式",
    )
    diagnostic_profile: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="工作线诊断配置（软件/硬件分类偏好、展示策略等）",
    )
    description: str | None = Field(default=None, max_length=500, description="作业线描述")


class WorkLine(
    WorkLineBase,
    EnterpriseMixin,
    SoftDeleteMixin,
    DataTableMixin,
    table=True,
):
    """
    作业线数据库表模型

    作业线是生产线或工作站的抽象，用于组织和管理设备。

    除基础信息外，它还是运行能力配置容器：
    - plugin_key / contract_version: 默认能力和契约来源
    - runtime_config_json: 运行时行为配置
    - diagnostic_profile: 诊断展示与分类配置
    """

    __tablename__: ClassVar[Literal["work_lines"]] = "work_lines"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value  # 业务数据表

    start_admission_status: str | None = Field(
        default=None,
        max_length=50,
        description="最近一次 START 准入检查状态",
    )
    start_admission_message: str | None = Field(
        default=None,
        sa_column=Column(Text),
        description="最近一次 START 准入检查说明",
    )
    start_admission_failed_device_code: str | None = Field(
        default=None,
        max_length=100,
        description="最近一次 START 准入失败设备编码",
    )
    start_admission_checked_at: datetime | None = Field(
        default=None,
        description="最近一次 START 准入检查时间",
    )
    last_start_request_id: str | None = Field(
        default=None,
        max_length=100,
        description="最近一次 START 请求 ID",
    )
    last_start_trace_id: str | None = Field(
        default=None,
        max_length=100,
        description="最近一次 START Trace ID",
    )
    is_active: bool = Field(default=False, sa_column_kwargs={"server_default": text("false")}, description="是否启用")

    @property
    def plugin_definition(self) -> WorklineCapabilityDefinition | None:
        """按 plugin_key 解析运行能力定义。"""

        return get_workline_capability_definition(self.plugin_key)

    @property
    def plugin_class(self) -> type[Any] | None:
        """Phase5 后运行时不再通过 WorkLine 动态解析插件类。"""

        return None

    @property
    def resolved_runtime_config(self) -> dict[str, Any]:
        """合并后的运行时配置视图。"""

        merged = dict(self.runtime_config_json or {})
        merged.setdefault("plugin_key", self.plugin_key)
        merged.setdefault("contract_version", self.contract_version)
        merged.setdefault(
            "run_mode", self.run_mode.value if isinstance(self.run_mode, WorkLineRunMode) else self.run_mode
        )
        return merged

    @property
    def diagnostic_summary(self) -> dict[str, Any]:
        """供排错与页面复用的作业线诊断摘要。"""

        return {
            "diagnostic_profile": dict(self.diagnostic_profile or {}),
        }


class WorkLineCreate(ModelFactory(WorkLineBase).for_create(exclude=("is_active",))):
    """作业线创建 Schema - 接收客户端输入"""


class WorkLineUpdate(ModelFactory(WorkLineBase).for_optimistic_update(exclude=("is_active",))):
    """作业线更新 Schema - 所有字段可选"""


class WorkLineResponse(WorkLineBase):
    """作业线响应 Schema - 返回给客户端"""

    id: int
    version: int
    is_active: bool


class DeviceRequirement(BaseModel):
    """插件所需设备角色和数量/能力约束。"""

    role: str = Field(description="必需角色名")
    min_count: int = Field(description="最小数量限制")
    max_count: int | None = Field(default=None, description="最大数量限制")
    hardware_capabilities: list[str] = Field(default_factory=list, description="要求硬件能力声明")
    required: bool = Field(
        default=True,
        description="required=true 设备不可用时 WorkLine 不允许启动；optional=true 可启动但 capability 从候选剔除",
    )


class SharedDevice(BaseModel):
    """共享设备声明（CEO-012）。

    主计划 §9.6 + §3.2：共享设备可被多条 WorkLine 或多个 capability 引用，
    必须声明影响范围和 required/optional 角色，避免跨线抢占。
    """

    device_code: str = Field(description="共享设备编码")
    role: str = Field(description="设备角色")
    shared_by: list[str] = Field(
        default_factory=list,
        description="共享该设备的 WorkLine code 列表；空表示同 WorkLine 内多 capability 共享",
    )
    impact_scope: str = Field(
        description="影响范围：WORKLINE / SESSION / WORK_ITEM；OFFLINE 时按 scope 决定 hold 范围",
    )


class SafetyZone(BaseModel):
    """安全区域声明（CEO-012）。

    主计划 §9.6 + §7.1：SafetyZone 标记物理安全边界，区域内设备
    OFFLINE/MAINTENANCE/ESTOP 时 RuntimeHold 按区域隔离，不污染整线。
    """

    zone_code: str = Field(description="安全区域编码")
    device_codes: list[str] = Field(
        default_factory=list,
        description="区域内设备编码列表；空表示按 role 匹配",
    )
    roles: list[str] = Field(default_factory=list, description="区域内设备角色列表")
    isolation_policy: str = Field(
        default="HOLD_ZONE",
        description="隔离策略：HOLD_ZONE（仅 hold 区域内）/ HOLD_WORKLINE（hold 整线）",
    )


class RackPositionCarrierCapability(BaseModel):
    """WES 管理货架停靠位的货架/槽位承载能力。"""

    allowed_rack_kinds: list[str] = Field(default_factory=list, description="停靠位允许承载的货架类型")
    min_capacity: int = Field(description="停靠位最小承载容量限制")
    max_capacity: int = Field(description="停靠位最大承载容量限制")
    allowed_slot_kinds: list[str] = Field(default_factory=list, description="停靠位允许承载的槽位类型")


class RackPosition(BaseModel):
    """WES 管理的货架停靠位/库存事实锚点，不代表泛化物理位置。"""

    code: str = Field(description="WES 管理货架停靠位编码，也是库存事实锚点编码")
    role: str = Field(description="货架停靠位业务角色")
    station_code: str = Field(description="插件内 station/工作位逻辑编码")
    carrier_capability: RackPositionCarrierCapability = Field(description="货架停靠位承载能力")


class NodeRef(BaseModel):
    """拓扑节点引用。"""

    kind: str = Field(description="拓扑节点引用类型")
    ref: str = Field(description="拓扑节点引用值")


class FlowEdge(BaseModel):
    """拓扑中的物料流或操作关系。"""

    from_node: NodeRef = Field(description="起点节点")
    to_node: NodeRef = Field(description="终点节点")
    type: str = Field(description="拓扑边类型")


class TopologySpec(BaseModel):
    """插件声明的静态拓扑。"""

    flow_edges: list[FlowEdge] = Field(default_factory=list, description="拓扑边列表")


class EventBinding(BaseModel):
    """插件声明的业务事件及来源设备角色。"""

    event: str = Field(description="事件类型")
    source_device_roles: list[str] = Field(default_factory=list, description="来源设备角色")
    category: str = Field(description="事件分类")


class CommandBinding(BaseModel):
    """插件命令及目标设备角色。"""

    command: str = Field(description="命令类型")
    target_device_role: str = Field(description="目标设备角色")


class ResourceBoundary(BaseModel):
    """插件声明的资源边界。"""

    rack_position_code: str = Field(description="manifest 货架停靠位编码")
    rack_kind: str = Field(description="承接货架类型")
    business_demand_type: str = Field(description="驱动该边界的业务需求类型")
    wms_operation_type: str = Field(description="由 WMS 转发的货架运输 operation 类型")
    snapshot_kind: str = Field(description="WES 需要读取的 active 快照类型")
    lease_scope: str = Field(description="WES 业务预占范围")


class SessionSubject(BaseModel):
    """插件运行会话的业务主体。"""

    type: str = Field(description="业务主体类型")
    physical_form: str = Field(description="业务主体物理形态")
    identity_sources: list[str] = Field(default_factory=list, description="主体身份来源字段")


class StateMachineSubject(BaseModel):
    """状态机绑定的业务主体。"""

    category: str = Field(description="主体类别")
    type: str = Field(description="主体类型")
    physical_form: str = Field(description="主体物理形态")


class StateMachineOwner(BaseModel):
    """状态机状态字段归属。"""

    model: str = Field(description="状态归属模型")
    field: str = Field(description="状态归属字段")


class StateMachineTransition(BaseModel):
    """状态机允许的状态流转。"""

    from_state: str = Field(description="起始状态")
    to_states: list[str] = Field(default_factory=list, description="允许到达状态")


class StateMachine(BaseModel):
    """插件声明的业务状态机。"""

    id: str = Field(description="状态机标识")
    subject: StateMachineSubject = Field(description="状态机业务主体")
    state_owner: StateMachineOwner = Field(description="状态字段归属")
    granularity: str = Field(description="状态机粒度")
    transitions: list[StateMachineTransition] = Field(default_factory=list, description="状态流转声明")


class PipelineQueue(BaseModel):
    """插件声明的管线队列。"""

    code: str = Field(description="队列编码")
    role: str = Field(description="队列角色")
    capacity: int | str = Field(description="队列容量，支持正整数或 MANY")
    order_policy: str = Field(default="FIFO", description="队列排序策略")

    @field_validator("capacity")
    @classmethod
    def _validate_capacity(cls, value: int | str) -> int | str:
        """收紧契约：仅接受正整数或字面量 MANY，拒绝 bool/0/负数/其它字符串。"""
        if isinstance(value, bool):
            raise TypeError("capacity must be a positive integer or 'MANY'")
        if isinstance(value, int):
            if value <= 0:
                raise ValueError("capacity must be a positive integer or 'MANY'")
            return value
        if value == "MANY":
            return value
        raise ValueError("capacity must be a positive integer or 'MANY'")


class WorkLinePluginOption(BaseModel):
    """作业线插件下拉选项。"""

    plugin_key: str = Field(description="工作线执行插件标识")
    label: str = Field(description="插件显示文本")
    contract_versions: list[str] = Field(default_factory=list, description="可选契约版本")
    default_contract_version: str = Field(description="默认契约版本")


class WorkLinePluginManifestSummary(BaseModel):
    """单插件 manifest 摘要。"""

    plugin_key: str = Field(description="工作线执行插件标识")
    contract_version: str = Field(description="插件契约版本")
    devices: list[DeviceRequirement] = Field(default_factory=list, description="设备角色要求")
    rack_positions: list[RackPosition] = Field(default_factory=list, description="货架停靠位声明")
    topology: TopologySpec = Field(description="静态拓扑声明")
    events: list[EventBinding] = Field(default_factory=list, description="事件绑定")
    commands: list[CommandBinding] = Field(default_factory=list, description="命令绑定")
    resource_boundaries: list[ResourceBoundary] = Field(default_factory=list, description="资源边界")
    safety_zones: list[SafetyZone] = Field(
        default_factory=list,
        description="安全区域声明；区域内设备异常时按 isolation_policy 隔离",
    )
    shared_devices: list[SharedDevice] = Field(
        default_factory=list,
        description="共享设备声明；跨线/跨 capability 共享设备的影响范围",
    )
    session_subject: SessionSubject | None = Field(default=None, description="插件运行会话业务主体")
    state_machines: list[StateMachine] = Field(default_factory=list, description="业务状态机声明")
    pipeline_queues: list[PipelineQueue] = Field(default_factory=list, description="管线队列声明")


class WorkLineConfigurationCheck(BaseModel):
    """作业线启用前结构化检查项。"""

    code: str = Field(description="检查项编码")
    status: Literal["PASS", "FAIL", "WARN"] = Field(description="检查结果")
    severity: Literal["INFO", "WARNING", "BLOCKER"] = Field(description="严重程度")
    context: dict[str, Any] = Field(default_factory=dict, description="检查上下文")


class WorkLineConfigurationStatus(BaseModel):
    """作业线配置状态响应。"""

    workline_id: int = Field(description="作业线 ID")
    is_active: bool = Field(description="是否已启用")
    can_activate: bool = Field(description="是否满足启用条件")
    checks: list[WorkLineConfigurationCheck] = Field(default_factory=list, description="启用前检查项")


class WorkLineStateTransitionRequest(BaseModel):
    """作业线启停请求。"""

    version: int = Field(description="WorkLine 乐观锁版本号")
