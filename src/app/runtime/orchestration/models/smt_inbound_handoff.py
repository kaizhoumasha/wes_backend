"""SMT 入库 handoff 账本模型。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - SQLModel 运行时需要解析字段类型
from decimal import Decimal  # noqa: TC003 - SQLModel 运行时需要解析字段类型
from enum import Enum
from typing import Any, ClassVar, cast

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydanticField
from sqlalchemy import JSON, CheckConstraint, Column, Index, Numeric, Text, UniqueConstraint, text
from sqlalchemy import Enum as SQLAEnum
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import ColumnElement
from sqlmodel import Field

from src.core.mixins import BaseMixin, DataTableMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class _JsonObjectServerDefault(ColumnElement[str]):
    """JSON 对象默认值，兼容 PostgreSQL Alembic 对比和 SQLite 测试建表。"""

    inherit_cache = True


@compiles(_JsonObjectServerDefault, "postgresql")
def _compile_json_object_server_default_for_postgresql(
    element: _JsonObjectServerDefault,
    compiler: Any,
    **kwargs: Any,
) -> str:
    return "'{}'::json"


@compiles(_JsonObjectServerDefault)
def _compile_json_object_server_default(
    element: _JsonObjectServerDefault,
    compiler: Any,
    **kwargs: Any,
) -> str:
    return "'{}'"


class SmtInboundHandoffDemandStatus(str, Enum):
    """SMT 入库 handoff demand 主状态。"""

    CREATED = "CREATED"
    EVALUATING = "EVALUATING"
    WAITING_FULL_BOX_EXCHANGE = "WAITING_FULL_BOX_EXCHANGE"
    RECONCILING = "RECONCILING"
    FULL_BOX_EXCHANGED = "FULL_BOX_EXCHANGED"
    READY_FOR_SORTING = "READY_FOR_SORTING"
    CLAIMED_BY_SORTING = "CLAIMED_BY_SORTING"
    SORTING_IN_PROGRESS = "SORTING_IN_PROGRESS"
    COMPLETED = "COMPLETED"
    MANUAL_HOLD = "MANUAL_HOLD"
    CANCELLED = "CANCELLED"


class SmtInboundHandoffSourceItemStatus(str, Enum):
    """SMT 入库 handoff source item 主状态。"""

    READY = "READY"
    PICK_REQUESTED = "PICK_REQUESTED"
    CLAIMED_BY_SORTING = "CLAIMED_BY_SORTING"
    PICKED = "PICKED"
    SORTING = "SORTING"
    SORTED = "SORTED"
    EXCHANGED = "EXCHANGED"
    SKIPPED = "SKIPPED"
    MANUAL_HOLD = "MANUAL_HOLD"


class SmtInboundHandoffDemandBase(BaseMixin):
    """SMT 入库 handoff demand 基础字段。"""

    demand_key: str = Field(max_length=200, description="handoff demand 幂等键")
    rack_release_id: str = Field(max_length=200, description="粗分机释放货架的稳定事实 ID")
    source_workline_id: int | None = Field(
        default=None,
        foreign_key="wes_biz.work_lines.id",
        description="粗分机工作线 ID",
    )
    source_workline_code: str | None = Field(default=None, max_length=100, description="粗分机工作线编码")
    target_workline_id: int | None = Field(
        default=None,
        foreign_key="wes_biz.work_lines.id",
        description="目标分拣工作线 ID",
    )
    target_workline_code: str | None = Field(default=None, max_length=100, description="目标分拣工作线编码")
    single_layer_rack_code: str = Field(max_length=100, description="被释放的单层货架编码")
    release_reason_code: str | None = Field(default=None, max_length=120, description="释放原因码")
    bin_snapshots_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, server_default=_JsonObjectServerDefault()),
        description="释放时料箱和料格快照，仅作为 release evidence",
    )
    decision_status: str | None = Field(default=None, max_length=50, description="满箱交换决策状态")
    handling_operation_key: str | None = Field(default=None, max_length=200, description="满箱交换 handling 操作键")
    full_box_exchange_station_code: str | None = Field(
        default=None,
        max_length=120,
        description="E11 满箱交换阶段门冻结站点",
    )
    full_box_exchange_rack_face: str | None = Field(
        default=None,
        max_length=1,
        description="E11 满箱交换阶段门冻结货架面",
    )
    active_full_box_exchange_intent_id: int | None = Field(
        default=None,
        foreign_key="wes_runtime.runtime_intent_logs.id",
        description="当前 E11 root RuntimeIntentLog；终态成功后清空",
    )
    sorting_source_demand_key: str | None = Field(default=None, max_length=200, description="分拣 source demand 幂等键")
    status: SmtInboundHandoffDemandStatus = Field(
        default=SmtInboundHandoffDemandStatus.CREATED,
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(
                SmtInboundHandoffDemandStatus,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="handoff demand 主状态",
    )
    failure_code: str | None = Field(default=None, max_length=120, index=True, description="受控失败原因码")
    failure_message: str | None = Field(default=None, sa_column=Column(Text), description="失败说明")
    next_attempt_at: datetime | None = Field(default=None, index=True, description="下一次兜底扫描时间")
    trace_id: str | None = Field(default=None, max_length=100, index=True, description="跨链路追踪 ID")


class SmtInboundHandoffDemand(SmtInboundHandoffDemandBase, DataTableMixin, table=True):
    """SMT 入库 handoff demand 表。"""

    __tablename__: ClassVar[str] = "smt_inbound_handoff_demands"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint("demand_key", name="uq_smt_inbound_handoff_demands_demand_key"),
        UniqueConstraint("rack_release_id", name="uq_smt_inbound_handoff_demands_rack_release_id"),
        CheckConstraint(
            "full_box_exchange_rack_face IN ('A', 'B')",
            name="ck_smt_handoff_demands_exchange_rack_face",
        ),
        Index(
            "ix_smt_handoff_demands_exchange_intent",
            "active_full_box_exchange_intent_id",
        ),
        Index(
            "ix_smt_inbound_handoff_demands_due_scan",
            "next_attempt_at",
            "updated_at",
            "id",
            postgresql_where=text("status IN ('CREATED', 'EVALUATING', 'FULL_BOX_EXCHANGED', 'READY_FOR_SORTING')"),
            sqlite_where=text("status IN ('CREATED', 'EVALUATING', 'FULL_BOX_EXCHANGED', 'READY_FOR_SORTING')"),
        ),
        Index(
            "ix_smt_inbound_handoff_demands_status_target_updated",
            "status",
            "target_workline_id",
            "updated_at",
        ),
        {"schema": SchemaType.BIZ.value},
    )


class SmtInboundHandoffSourceItemBase(BaseMixin):
    """SMT 入库 handoff source item 基础字段。"""

    handoff_demand_id: int = Field(
        foreign_key="wes_biz.smt_inbound_handoff_demands.id",
        description="所属 handoff demand ID",
    )
    item_key: str = Field(max_length=200, description="demand 内 source item 幂等键")
    bin_code: str | None = Field(default=None, max_length=100, description="source 料箱编码")
    bin_cell_index: int | None = Field(default=None, description="source 料格序号")
    bin_cell_code: str | None = Field(default=None, max_length=100, description="source 料格编码")
    material_identity_key: str | None = Field(default=None, max_length=200, description="source 物料身份键")
    pkg_code: str | None = Field(default=None, max_length=200, description="source 流水号")
    reel_thickness_mm: Decimal | None = Field(
        default=None,
        sa_column=Column(Numeric(10, 3)),
        description="盘厚 evidence，单位 mm",
    )
    status: SmtInboundHandoffSourceItemStatus = Field(
        default=SmtInboundHandoffSourceItemStatus.READY,
        sa_type=cast(
            "Any",
            SQLAEnum(
                SmtInboundHandoffSourceItemStatus,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="source item 主状态",
    )
    target_workline_id: int | None = Field(
        default=None,
        foreign_key="wes_biz.work_lines.id",
        description="实际认领的分拣工作线 ID",
    )
    target_workline_code: str | None = Field(default=None, max_length=100, description="实际认领的分拣工作线编码")
    sorting_session_id: int | None = Field(
        default=None,
        foreign_key="wes_biz.workline_sessions.id",
        description="认领后的 SMT_SORTING_INBOUND session ID",
    )
    claim_attempt_no: int = Field(default=1, ge=1, description="source pick request 代次")
    source_pick_inbox_id: int | None = Field(
        default=None,
        foreign_key="wes_runtime.runtime_inbox.id",
        description="SORTING_SOURCE_PICK_REQUESTED RuntimeInbox ID",
    )
    source_pick_command_id: int | None = Field(
        default=None,
        foreign_key="wes_biz.device_commands.id",
        description="首盘 SORTING_SOURCE_PICK command ID",
    )
    source_pick_command_code: str | None = Field(default=None, max_length=200, description="首盘 command code")
    source_pick_dispatch_key: str | None = Field(default=None, max_length=200, description="首盘 dispatch evidence")
    failure_code: str | None = Field(default=None, max_length=120, description="item 级受控失败原因码")
    failure_message: str | None = Field(default=None, sa_column=Column(Text), description="item 级失败说明")
    claimed_at: datetime | None = Field(default=None, description="认领时间")
    completed_at: datetime | None = Field(default=None, description="完成时间")
    next_attempt_at: datetime | None = Field(default=None, description="下一次可重试时间")


class SmtInboundHandoffSourceItem(SmtInboundHandoffSourceItemBase, DataTableMixin, table=True):
    """SMT 入库 handoff source item 表。"""

    __tablename__: ClassVar[str] = "smt_inbound_handoff_source_items"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint(
            "handoff_demand_id",
            "item_key",
            name="uq_smt_inbound_handoff_source_items_demand_item_key",
        ),
        Index(
            "ix_smt_inbound_handoff_source_items_ready_claim",
            "next_attempt_at",
            "handoff_demand_id",
            "id",
            postgresql_where=text("status = 'READY'"),
            sqlite_where=text("status = 'READY'"),
        ),
        Index(
            "ix_smt_inbound_handoff_source_items_post_claim_recovery",
            "updated_at",
            "id",
            postgresql_where=text(
                "status IN ('PICK_REQUESTED', 'CLAIMED_BY_SORTING') AND source_pick_inbox_id IS NOT NULL"
            ),
            sqlite_where=text(
                "status IN ('PICK_REQUESTED', 'CLAIMED_BY_SORTING') AND source_pick_inbox_id IS NOT NULL"
            ),
        ),
        Index(
            "ix_smt_inbound_handoff_source_items_demand_status_id",
            "handoff_demand_id",
            "status",
            "id",
        ),
        Index("ix_smt_in_handoff_items_demand_id", "handoff_demand_id"),
        Index("ix_smt_in_handoff_items_failure_code", "failure_code"),
        Index("ix_smt_in_handoff_items_material_key", "material_identity_key"),
        Index("ix_smt_in_handoff_items_next_attempt", "next_attempt_at"),
        Index("ix_smt_in_handoff_items_pick_command", "source_pick_command_id"),
        Index("ix_smt_in_handoff_items_pick_inbox", "source_pick_inbox_id"),
        Index("ix_smt_in_handoff_items_sorting_session", "sorting_session_id"),
        Index("ix_smt_in_handoff_items_status", "status"),
        {"schema": SchemaType.BIZ.value},
    )


class SmtInboundHandoffDemandCreate(ModelFactory(SmtInboundHandoffDemandBase).for_create()):
    """SMT 入库 handoff demand 创建 Schema。"""


class SmtInboundHandoffDemandUpdate(ModelFactory(SmtInboundHandoffDemandBase).for_update()):
    """SMT 入库 handoff demand 更新 Schema。"""


class SmtInboundHandoffSourceItemCreate(ModelFactory(SmtInboundHandoffSourceItemBase).for_create()):
    """SMT 入库 handoff source item 创建 Schema。"""


class SmtInboundHandoffSourceItemUpdate(ModelFactory(SmtInboundHandoffSourceItemBase).for_update()):
    """SMT 入库 handoff source item 更新 Schema。"""


class SmtInboundHandoffSourceItemDetailResponse(BaseModel):
    """SMT 入库 handoff source item 详情投影。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    item_key: str | None = None
    bin_code: str | None = None
    bin_cell_index: int | None = None
    bin_cell_code: str | None = None
    material_identity_key: str | None = None
    pkg_code: str | None = None
    reel_thickness_mm: str | None = None
    status: str
    target_workline_id: int | None = None
    target_workline_code: str | None = None
    sorting_session_id: int | None = None
    claim_attempt_no: int = 1
    source_pick_inbox_id: int | None = None
    source_pick_command_id: int | None = None
    source_pick_command_code: str | None = None
    source_pick_dispatch_key: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    source_pick_inbox: dict[str, Any] | None = None
    source_pick_command: dict[str, Any] | None = None
    source_pick_outbox: dict[str, Any] | None = None
    available_actions: list[str] = PydanticField(default_factory=list)


class SmtInboundHandoffDemandSummaryResponse(BaseModel):
    """SMT 入库 handoff demand 列表摘要投影。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    demand_key: str
    rack_release_id: str
    source_workline_id: int | None = None
    source_workline_code: str | None = None
    target_workline_id: int | None = None
    target_workline_code: str | None = None
    single_layer_rack_code: str
    release_reason_code: str | None = None
    decision_status: str | None = None
    handling_operation_key: str | None = None
    sorting_source_demand_key: str | None = None
    status: str
    failure_code: str | None = None
    failure_message: str | None = None
    trace_id: str | None = None
    item_status_counts: dict[str, int] = PydanticField(default_factory=dict)
    handling_trace_summary: dict[str, Any] = PydanticField(default_factory=dict)
    claim_recovery_summary: dict[str, int] = PydanticField(default_factory=dict)
    available_actions: list[str] = PydanticField(default_factory=list)


class SmtInboundHandoffDemandListResponse(BaseModel):
    """SMT 入库 handoff demand 列表响应数据。"""

    total: int = PydanticField(default=0, ge=0)
    items: list[SmtInboundHandoffDemandSummaryResponse] = PydanticField(default_factory=list)
    limit: int = PydanticField(default=50, ge=1)
    offset: int = PydanticField(default=0, ge=0)


class SmtInboundHandoffDemandDetailResponse(SmtInboundHandoffDemandSummaryResponse):
    """SMT 入库 handoff demand 详情投影。"""

    release_snapshot: dict[str, Any] = PydanticField(default_factory=dict)
    source_items: list[SmtInboundHandoffSourceItemDetailResponse] = PydanticField(default_factory=list)


class SmtInboundHandoffActionResponse(BaseModel):
    """SMT 入库 handoff 手工动作响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    available_actions: list[str] = PydanticField(default_factory=list)


__all__ = [
    "SmtInboundHandoffActionResponse",
    "SmtInboundHandoffDemand",
    "SmtInboundHandoffDemandBase",
    "SmtInboundHandoffDemandCreate",
    "SmtInboundHandoffDemandDetailResponse",
    "SmtInboundHandoffDemandListResponse",
    "SmtInboundHandoffDemandStatus",
    "SmtInboundHandoffDemandSummaryResponse",
    "SmtInboundHandoffDemandUpdate",
    "SmtInboundHandoffSourceItem",
    "SmtInboundHandoffSourceItemBase",
    "SmtInboundHandoffSourceItemCreate",
    "SmtInboundHandoffSourceItemDetailResponse",
    "SmtInboundHandoffSourceItemStatus",
    "SmtInboundHandoffSourceItemUpdate",
]
