"""外部系统单次 HTTP 尝试的统一诊断记录。"""

from datetime import datetime
from typing import Any, ClassVar, Literal

from pydantic import ConfigDict, field_validator
from sqlalchemy import JSON, CheckConstraint, Column, Index, String
from sqlmodel import Field

from src.core.mixins import BaseMixin, DataTableMixin
from src.database.schema_conf import SchemaType
from src.utils.timezone import timezone


class APIAccessLogBase(BaseMixin):
    system_id: str = Field(max_length=64, description="外部系统标识")
    direction: Literal["INBOUND", "OUTBOUND"] = Field(max_length=8, sa_type=String(8), description="交互方向")
    method: str = Field(max_length=10, description="HTTP 方法")
    path: str = Field(max_length=500, description="实际路径，不含 query")
    peer_address: str | None = Field(default=None, max_length=500, description="对端地址")
    request_id: str | None = Field(default=None, max_length=100, index=True, description="请求或 attempt ID")
    trace_id: str | None = Field(default=None, max_length=100, index=True, description="Trace ID")
    event_id: str | None = Field(default=None, max_length=200, description="事件 ID")
    causation_id: str | None = Field(default=None, max_length=200, description="因果事件 ID")
    status_code: int | None = Field(default=None, description="实际 HTTP 状态，无响应为 null")
    response_time_ms: int | None = Field(default=None, ge=0, description="测得的交互耗时，毫秒")
    delivery_state: str | None = Field(default=None, max_length=32, description="出站送达事实")
    error_code: str | None = Field(default=None, max_length=100, description="稳定错误分类")


class APIAccessLog(APIAccessLogBase, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "api_access_logs"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.SYS.value
    __table_args__ = (
        CheckConstraint("direction IN ('INBOUND', 'OUTBOUND')", name="direction"),
        CheckConstraint("response_time_ms IS NULL OR response_time_ms >= 0", name="response_time_ms_nonnegative"),
        Index("ix_api_access_logs_created_at_id", "created_at", "id"),
        Index("ix_api_access_logs_system_direction_created_at", "system_id", "direction", "created_at"),
        {"schema": SchemaType.SYS.value},
    )
    details: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True), description="有界诊断快照"
    )


class APIAccessLogCreate(APIAccessLogBase):
    model_config = ConfigDict(frozen=True, extra="forbid")
    details: dict[str, Any] | None = None


class APIAccessLogSummary(APIAccessLogBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def utc_created_at(cls, value: datetime) -> datetime:
        return timezone.to_utc(value)


class APIAccessLogResponse(APIAccessLogSummary):
    details: dict[str, Any] | None = None


__all__ = ["APIAccessLog", "APIAccessLogCreate", "APIAccessLogResponse", "APIAccessLogSummary"]
