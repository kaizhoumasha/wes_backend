"""WMS 熔断器状态模型。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import Any, ClassVar, Literal, cast

from sqlalchemy import Enum as SQLAEnum
from sqlalchemy import Index
from sqlmodel import Field

from src.core.mixins import DataTableMixin
from src.database.schema_conf import SchemaType
from src.utils.timezone import timezone


class WmsCircuitBreakerStatus(str, Enum):
    """WMS 熔断器状态。"""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class WmsCircuitBreakerState(DataTableMixin, table=True):
    """按 target_code + operation_name 共享的 WMS 熔断状态。"""

    __tablename__: ClassVar[Literal["wms_circuit_breaker_state"]] = "wms_circuit_breaker_state"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_wms_circuit_breaker_target_operation", "target_code", "operation_name", unique=True),
        {"schema": SchemaType.BIZ.value},
    )

    target_code: str = Field(min_length=1, max_length=240, description="WMS/RCS 目标编码")
    operation_name: str = Field(min_length=1, max_length=120, description="WMS 操作名")
    state: WmsCircuitBreakerStatus = Field(
        default=WmsCircuitBreakerStatus.CLOSED,
        sa_type=cast(
            "Any",
            SQLAEnum(WmsCircuitBreakerStatus, native_enum=False, create_constraint=True, length=50),
        ),
        description="熔断器状态",
    )
    failure_count: int = Field(default=0, ge=0, description="连续失败次数")
    half_open_attempt_count: int = Field(default=0, ge=0, description="HALF_OPEN 探测尝试次数")
    half_open_success_count: int = Field(default=0, ge=0, description="HALF_OPEN 探测成功次数")
    half_open_probe_generation: int = Field(default=0, ge=0, description="HALF_OPEN 探测代次")
    half_open_probe_expires_at: datetime | None = Field(default=None, description="HALF_OPEN 当前探针过期时间")
    last_failure_at: datetime | None = Field(default=None, description="最近一次失败时间")
    opened_until: datetime | None = Field(default=None, description="OPEN 状态持续到该时间")
    last_evidence_key: str | None = Field(default=None, max_length=240, description="最近关联的 evidence_key")
    last_transition_at: datetime = Field(default_factory=timezone.now_for_db, description="最近状态转换时间")


__all__ = [
    "WmsCircuitBreakerState",
    "WmsCircuitBreakerStatus",
]
