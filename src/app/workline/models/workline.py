"""作业线模型及其 API Schema。"""

from enum import Enum
from typing import Any, ClassVar, Literal, cast

from pydantic import BaseModel
from sqlalchemy import JSON, Column, text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, SoftDeleteMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class LineType(str, Enum):
    """作业线类型枚举。"""

    AUTO = "AUTO"  # 自动线
    MANUAL = "MANUAL"  # 人工线
    HYBRID = "HYBRID"  # 混合线


class WorkLineRunMode(str, Enum):
    """作业线运行模式枚举。"""

    AUTO = "AUTO"  # 自动运行，允许真实设备和外部副作用
    MANUAL = "MANUAL"  # 人工确认/人工介入
    SIMULATION = "SIMULATION"  # 沙箱模拟，派发到沙箱通道


class WorkLineEditableBase(BaseMixin):
    """不含业务插件配置的通用 WorkLine 可维护字段。"""

    line_code: str = Field(min_length=1, max_length=50, index=True, description="作业线编码（业务主键）")
    line_name: str = Field(min_length=1, max_length=100, description="作业线名称")
    line_type: LineType = Field(
        sa_type=cast("Any", SQLAEnum(LineType, native_enum=False, create_constraint=True, length=50)),
        description="作业线类型",
    )
    zone_name: str | None = Field(default=None, max_length=100, description="区域名称")
    runtime_config_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
        description="工作线运行时配置（重试、超时、会话归属等）",
    )
    run_mode: WorkLineRunMode = Field(
        default=WorkLineRunMode.AUTO,
        index=True,
        sa_type=cast("Any", SQLAEnum(WorkLineRunMode, native_enum=False, create_constraint=True, length=50)),
        description="工作线运行模式",
    )
    diagnostic_profile: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
        description="工作线诊断配置（软件/硬件分类偏好、展示策略等）",
    )
    description: str | None = Field(default=None, max_length=500, description="作业线描述")


class WorkLineBase(WorkLineEditableBase):
    """包含当前业务插件草稿的 WorkLine 完整配置。"""

    plugin_key: str | None = Field(default=None, min_length=1, max_length=100, index=True, description="业务插件标识")
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="当前业务插件配置")


class WorkLine(WorkLineBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):
    """作业线静态身份、通用配置和启停状态。"""

    __tablename__: ClassVar[Literal["work_lines"]] = "work_lines"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value

    is_active: bool = Field(default=False, sa_column_kwargs={"server_default": text("false")}, description="是否启用")

    @property
    def resolved_runtime_config(self) -> dict[str, Any]:
        """合并后的运行时配置视图。"""

        merged = dict(self.runtime_config_json or {})
        merged.setdefault(
            "run_mode", self.run_mode.value if isinstance(self.run_mode, WorkLineRunMode) else self.run_mode
        )
        return merged

    @property
    def diagnostic_summary(self) -> dict[str, Any]:
        """供排错与页面复用的作业线诊断摘要。"""

        return {"diagnostic_profile": dict(self.diagnostic_profile or {})}


class WorkLineCreate(ModelFactory(WorkLineEditableBase).for_create(exclude=("is_active",))):
    """作业线创建 Schema。"""


class WorkLineUpdate(ModelFactory(WorkLineEditableBase).for_optimistic_update(exclude=("is_active",))):
    """作业线更新 Schema。"""


class WorkLineResponse(WorkLineBase):
    """作业线响应 Schema。"""

    id: int
    version: int
    is_active: bool


class WorkLineConfigurationCheck(BaseModel):
    """作业线启用前结构化检查项。"""

    code: str = Field(description="检查项编码")
    status: Literal["PASS", "FAIL", "WARN"] = Field(description="检查结果")
    severity: Literal["INFO", "WARNING", "BLOCKER"] = Field(description="检查严重程度")
    context: dict[str, Any] = Field(default_factory=dict, description="检查上下文")


class WorkLineConfigurationStatus(BaseModel):
    """作业线配置状态响应。"""

    workline_id: int = Field(description="作业线 ID")
    is_active: bool = Field(description="是否已启用")
    can_activate: bool = Field(description="是否满足启用条件")
    checks: list[WorkLineConfigurationCheck] = Field(default_factory=list, description="启用前检查项")


class WorkLineConfigurationUpdate(BaseModel):
    """停用 WorkLine 的插件配置与设备全集替换请求。"""

    version: int = Field(description="WorkLine 乐观锁版本号")
    plugin_key: str | None = Field(default=None, min_length=1, max_length=100, description="业务插件标识")
    config: dict[str, Any] = Field(default_factory=dict, description="当前业务插件配置")
    device_codes: tuple[str, ...] = Field(default_factory=tuple, description="目标工作线设备编码全集")


class WorkLineConfigurationResponse(BaseModel):
    """业务插件配置全集保存结果。"""

    workline_id: int
    version: int
    plugin_key: str | None
    config: dict[str, Any]
    device_codes: tuple[str, ...]


class WorkLinePluginSummary(BaseModel):
    """部署清单中的业务插件及当前 WorkLine 兼容性。"""

    plugin_key: str
    plugin_version: str
    display_name: str
    supported_line_types: tuple[LineType, ...]
    compatible: bool
    incompatibility_reasons: tuple[str, ...] = ()


class WorkLineStateTransitionRequest(BaseModel):
    """作业线启停请求。"""

    version: int = Field(description="WorkLine 乐观锁版本号")
