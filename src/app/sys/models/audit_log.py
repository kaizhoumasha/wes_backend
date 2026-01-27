from datetime import datetime
from enum import IntEnum

import sqlalchemy as sa
from sqlmodel import Field

from src.core.mixins import BaseMixin, PrimaryKeyMixin
from src.database.model_factory import ModelFactory
from src.utils.timezone import timezone


class OperaStatus(IntEnum):
    """操作日志状态"""

    FAIL = 0
    SUCCESS = 1


class AuditLogBase(BaseMixin):
    """
    AuditLog 基础字段
    """

    trace_id: str = Field(max_length=64, index=True)
    username: str | None = Field(default=None, max_length=32, index=True)
    method: str = Field(max_length=10)  # GET, POST, PUT, DELETE etc
    title: str = Field(max_length=100)
    path: str = Field(max_length=200)
    ip: str = Field(max_length=64)
    country: str | None = Field(default=None, max_length=64)
    region: str | None = Field(default=None, max_length=64)
    city: str | None = Field(default=None, max_length=64)
    user_agent: str = Field(max_length=500)
    os: str | None = Field(default=None, max_length=64)
    browser: str | None = Field(default=None, max_length=64)
    device: str | None = Field(default=None, max_length=64)
    args: dict | None = Field(default=None, sa_type=sa.JSON)
    status: OperaStatus = Field(default=OperaStatus.SUCCESS)
    code: str = Field(max_length=20)
    msg: str | None = Field(default=None, sa_type=sa.Text)
    cost_time: float = Field(ge=0)  # 添加非负数验证
    opera_time: datetime = Field(
        default_factory=timezone.now,
        sa_type=sa.TIMESTAMP(timezone=True),  # type: ignore[misc]
        index=True,
    )


class AuditLog(PrimaryKeyMixin, AuditLogBase, table=True):  # type: ignore[misc]
    """
    AuditLog 数据库表模型
    """

    __tablename__ = "audit_logs"


AuditLogCreate = ModelFactory(AuditLogBase).for_create()


class AuditLogResponse(AuditLogBase):
    """
    AuditLog 响应 Schema
    """

    id: int
