from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Literal, cast

import sqlalchemy as sa
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.core.mixins import BaseMixin, DataTableMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType
from src.utils.timezone import timezone


class OperaStatus(str, Enum):
    """操作日志状态"""

    FAIL = "FAIL"
    SUCCESS = "SUCCESS"


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
    args: dict[str, Any] | None = Field(default=None, sa_type=sa.JSON)

    # 🔥 使用 VARCHAR + CHECK 约束
    status: OperaStatus = Field(
        default=OperaStatus.SUCCESS,
        sa_type=cast("Any", SQLAEnum(
            OperaStatus,
            native_enum=False,
            create_constraint=True,
            length=50,
        )),
        description="操作状态",
    )

    code: str = Field(max_length=20)
    msg: str | None = Field(default=None, sa_type=sa.Text)
    cost_time: float = Field(ge=0)  # 添加非负数验证
    opera_time: datetime = Field(
        default_factory=timezone.now,
        sa_type=sa.TIMESTAMP(timezone=True),  # type: ignore[misc]
        index=True,
    )


class AuditLog(AuditLogBase, DataTableMixin, table=True):  # type: ignore[misc]
    """
    AuditLog 数据库表模型
    """

    __tablename__: ClassVar[Literal["audit_logs"]] = "audit_logs"  # pyright: ignore[reportIncompatibleVariableOverride]

    __schema__ = SchemaType.SYS.value


AuditLogCreate = ModelFactory(AuditLogBase).for_create()


class AuditLogResponse(AuditLogBase):
    """
    AuditLog 响应 Schema
    """

    id: int
