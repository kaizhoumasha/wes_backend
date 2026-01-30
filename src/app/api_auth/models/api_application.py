from datetime import datetime
from enum import Enum
from ipaddress import ip_address
from typing import Literal

from pydantic import field_validator, model_validator
from sqlalchemy import JSON, Index
from sqlmodel import Field

from src.core.mixins import AuditableMixin, BaseMixin, DataTableMixin, EnterpriseMixin, SoftDeleteMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class AppStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class AppType(str, Enum):
    ECS = "ECS"
    RCS = "RCS"
    WMS = "WMS"
    ThirdParty = "Third-Party"


class APIApplicationBase(BaseMixin):
    app_name: str = Field(max_length=100, description="应用名称")
    app_type: AppType = Field(default=AppType.ECS, description="应用类型")
    description: str | None = Field(default=None, max_length=500, description="应用描述")
    ip_whitelist: list[str] | None = Field(default=None, description="IP白名单")
    rate_limit_per_minute: int = Field(default=100, ge=1, le=10000, description="每分钟请求限制")
    rate_limit_per_hour: int = Field(default=5000, ge=1, le=1000000, description="每小时请求限制")
    expires_at: datetime | None = Field(default=None, description="过期时间")

    @field_validator("ip_whitelist", mode="before")
    @classmethod
    def validate_ip_whitelist(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        for ip in v:
            try:
                ip_address(ip)  # 验证 IP 格式
            except ValueError as ve:
                raise ValueError(f"无效的 IP 地址: {ip}") from ve
        return v

    @model_validator(mode="after")
    def validate_rate_limits(self) -> "APIApplicationBase":
        # 确保小时限制 < 分钟限制 * 60 (防止持续高频设计)
        if self.rate_limit_per_hour >= self.rate_limit_per_minute * 60:
            raise ValueError(
                f"小时限制 ({self.rate_limit_per_hour}) 应该 < 分钟限制 * 60 ({self.rate_limit_per_minute * 60})"
            )
        return self


class APIApplication(
    APIApplicationBase,
    EnterpriseMixin,
    SoftDeleteMixin,
    DataTableMixin,
    table=True,
):
    __tablename__: Literal["api_applications"] = "api_applications"
    __schema__ = SchemaType.SYS.value

    app_id: str = Field(unique=True, index=True, max_length=50, description="应用ID")
    app_secret_encrypted: str = Field(max_length=500, description="加密后的密钥")
    status: AppStatus = Field(default=AppStatus.ACTIVE, description="状态")
    ip_whitelist: list[str] | None = Field(default=None, sa_type=JSON, description="IP白名单")

    __table_args__ = (
        Index("ix_api_app_status", "status", "is_deleted"),
        Index("ix_api_app_type", "app_type", "is_deleted"),
    )


class APIApplicationCreate(ModelFactory(APIApplicationBase).for_create()):
    pass


class APIApplicationUpdate(ModelFactory(APIApplicationBase).for_update()):
    pass


class APIApplicationResponse(APIApplicationBase, AuditableMixin):
    id: int
    app_id: str
    status: AppStatus = Field(default=AppStatus.ACTIVE)
