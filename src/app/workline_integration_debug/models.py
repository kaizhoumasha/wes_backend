"""人工出库联调 run 与步骤关联。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any, ClassVar, Literal

from sqlalchemy import JSON, BigInteger, CheckConstraint, Column, Index, Text, UniqueConstraint
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class IntegrationRun(EnterpriseMixin, DataTableMixin, table=True):
    """一次固定人工出库联调会话；不代表正式 WorkLine execution。"""

    __tablename__: ClassVar[Literal["workline_integration_runs"]] = "workline_integration_runs"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.RUNTIME.value
    __table_args__ = (
        CheckConstraint(
            "status IN ('CREATED','WAITING_TASK','ACTIVE','WAITING_EXTERNAL','COMPLETED',"
            "'NEEDS_ATTENTION','CLOSED_BY_OPERATOR')",
            name="workline_integration_run_status_valid",
        ),
        CheckConstraint(
            "profile IN ('CONTRACT_SIMULATION','DEVICE_INTEGRATION','FULL_SITE_INTEGRATION')",
            name="workline_integration_run_profile_valid",
        ),
        CheckConstraint(
            "scenario_key = 'manual_outbound_picking@v1'",
            name="workline_integration_run_scenario_valid",
        ),
        CheckConstraint(
            "current_phase IN ('BIND_TASK','TASK_PREPARE','PLAN_RECEIPT','RACK_TRANSPORT','RACK_ARRIVAL',"
            "'BIN_INBOUND_BATCH','BIN_TRANSPORT','POINT1_ARRIVAL','POINT2_SCAN','WORK_ADMISSION',"
            "'WORK_COMPLETION','POINT2_RELEASE','POINT3_ROUTE','RETURN_BUFFER','BIN_RETURN_BATCH',"
            "'BIN_RETURN_TRANSPORT','RACK_DEPARTURE','TASK_COMPLETION','CLEANUP')",
            name="workline_integration_run_phase_valid",
        ),
        CheckConstraint(
            "(status = 'CLOSED_BY_OPERATOR' AND active_scope IS NULL) OR "
            "(status <> 'CLOSED_BY_OPERATOR' AND active_scope IS NOT NULL)",
            name="workline_integration_run_active_scope_consistent",
        ),
        UniqueConstraint("run_id", name="ux_workline_integration_runs_run_id"),
        UniqueConstraint("active_scope", name="ux_workline_integration_runs_active_scope"),
        Index("ix_workline_integration_runs_recent", "updated_at", "id"),
        {"schema": SchemaType.RUNTIME.value},
    )

    run_id: str = Field(min_length=1, max_length=80)
    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", sa_type=SQL_COMPAT_BIGINT)
    workline_code: str = Field(min_length=1, max_length=50)
    scenario_key: str = Field(max_length=80)
    expected_plugin_key: str = Field(max_length=100)
    profile: str = Field(max_length=30)
    environment_label: str = Field(min_length=1, max_length=80)
    operator_user_id: int = Field(sa_type=BigInteger)
    active_scope: str | None = Field(default=None, max_length=80)
    status: str = Field(max_length=30)
    current_phase: str = Field(max_length=30)
    picking_task_id: int | None = Field(
        default=None,
        foreign_key="wes_biz.picking_tasks.id",
        sa_type=SQL_COMPAT_BIGINT,
    )
    task_id: str | None = Field(default=None, max_length=100)
    issued_operation_id: str | None = Field(default=None, max_length=36)
    bin_code: str | None = Field(default=None, max_length=100)
    device_code: str | None = Field(default=None, max_length=100)
    rack_id: str | None = Field(default=None, max_length=100)
    configuration_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    attention_code: str | None = Field(default=None, max_length=120)
    attention_detail: str | None = Field(default=None, sa_type=Text)
    wms_cleanup_confirmed: bool = Field(default=False)
    site_cleanup_confirmed: bool = Field(default=False)
    completed_at: datetime | None = Field(default=None)
    closed_at: datetime | None = Field(default=None)


class IntegrationRunStep(EnterpriseMixin, DataTableMixin, table=True):
    """联调步骤及已创建可靠对象的身份关联。"""

    __tablename__: ClassVar[Literal["workline_integration_run_steps"]] = "workline_integration_run_steps"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.RUNTIME.value
    __table_args__ = (
        CheckConstraint("ordinal >= 0", name="workline_integration_step_ordinal_valid"),
        CheckConstraint(
            "status IN ('PENDING','WAITING','SUCCEEDED','NEEDS_ATTENTION')",
            name="workline_integration_step_status_valid",
        ),
        UniqueConstraint("run_id", "ordinal", name="ux_workline_integration_steps_run_ordinal"),
        UniqueConstraint("client_request_id", name="ux_workline_integration_steps_client_request"),
        Index("ix_workline_integration_steps_confirmation", "wms_confirmation_id"),
        Index("ix_workline_integration_steps_transport", "transport_task_id"),
        Index("ix_workline_integration_steps_device", "device_command_code"),
        {"schema": SchemaType.RUNTIME.value},
    )

    run_id: str = Field(
        foreign_key="wes_runtime.workline_integration_runs.run_id",
        ondelete="CASCADE",
        max_length=80,
    )
    ordinal: int
    phase: str = Field(max_length=30)
    status: str = Field(default="PENDING", max_length=30)
    client_request_id: str | None = Field(default=None, max_length=120)
    operation: str | None = Field(default=None, max_length=160)
    operation_id: str | None = Field(default=None, max_length=160)
    wms_confirmation_id: int | None = Field(
        default=None,
        foreign_key="wes_biz.wms_confirmations.id",
        sa_type=SQL_COMPAT_BIGINT,
    )
    transport_task_id: str | None = Field(default=None, max_length=80)
    device_command_code: str | None = Field(default=None, max_length=80)
    request_summary_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    result_summary_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    reason_code: str | None = Field(default=None, max_length=120)


__all__ = ["IntegrationRun", "IntegrationRunStep"]
