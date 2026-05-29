"""
作业线相关模型

包含 WorkLine 数据库表模型和相关的 Pydantic Schemas
"""

from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Literal, cast

from pydantic import BaseModel
from sqlalchemy import JSON, Column
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, SoftDeleteMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType
from src.workline_plugin_registry import WorklinePluginDefinition, get_workline_plugin_definition


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

    除基础信息外，它还是插件运行容器：
    - plugin_key / contract_version: 默认插件和契约来源
    - runtime_config_json: 运行时行为配置
    - diagnostic_profile: 诊断展示与分类配置
    """

    __tablename__: ClassVar[Literal["work_lines"]] = "work_lines"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value  # 业务数据表

    runtime_status: WorkLineRuntimeStatus = Field(
        default=WorkLineRuntimeStatus.READY,
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(
                WorkLineRuntimeStatus,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="WorkLine 运行安全状态",
    )
    active_safety_incident_id: int | None = Field(
        default=None,
        index=True,
        description="当前生效的安全事件 ID",
    )
    stopped_at: datetime | None = Field(default=None, index=True, description="进入急停冻结的时间")
    stopped_reason: str | None = Field(default=None, max_length=200, description="进入急停冻结的原因")
    resumed_at: datetime | None = Field(default=None, index=True, description="恢复 READY 的时间")
    is_active: bool = Field(default=False, description="是否启用")

    @property
    def plugin_definition(self) -> WorklinePluginDefinition | None:
        """按 plugin_key 解析插件定义。"""

        return get_workline_plugin_definition(self.plugin_key)

    @property
    def plugin_class(self) -> type[Any] | None:
        """按 plugin_key 解析插件类。"""

        definition = self.plugin_definition
        return definition.plugin_class if definition else None

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


class WorkLinePluginOption(BaseModel):
    """作业线插件下拉选项。"""

    plugin_key: str = Field(description="工作线执行插件标识")
    label: str = Field(description="插件显示文本")
    contract_versions: list[str] = Field(default_factory=list, description="可选契约版本")
    default_contract_version: str = Field(description="默认契约版本")


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
