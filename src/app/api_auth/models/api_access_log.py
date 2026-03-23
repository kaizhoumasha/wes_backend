from typing import ClassVar, Literal

from sqlalchemy import Index
from sqlmodel import Field

from src.core.mixins import BaseMixin, DataTableMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class APIAccessLogBase(BaseMixin):
    app_id: str = Field(max_length=50, description="应用ID")
    app_name: str = Field(max_length=100, description="应用名称")
    request_id: str = Field(max_length=50, description="请求ID")
    method: str = Field(max_length=10, description="HTTP方法")
    path: str = Field(max_length=500, description="请求路径")
    status_code: int = Field(description="响应状态码")
    response_time_ms: int = Field(description="响应时间(毫秒)")
    ip_address: str = Field(max_length=50, description="客户端IP")
    user_agent: str | None = Field(default=None, max_length=500, description="User-Agent")
    error_message: str | None = Field(default=None, max_length=1000, description="错误信息")


class APIAccessLog(APIAccessLogBase, DataTableMixin, table=True):
    __tablename__: ClassVar[Literal["api_access_logs"]] = "api_access_logs"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.SYS.value

    __table_args__ = (
        Index("ix_api_access_log_app_time", "app_id", "created_at"),
        Index("ix_api_access_log_status", "status_code", "created_at"),
        Index("ix_api_access_log_path", "path", "created_at", postgresql_ops={"path": "text_pattern_ops"}),
    )


class APIAccessLogCreate(ModelFactory(APIAccessLogBase).for_create()):
    pass


class APIAccessLogUpdate(ModelFactory(APIAccessLogBase).for_update()):
    pass


class APIAccessLogResponse(APIAccessLogBase):
    id: int
