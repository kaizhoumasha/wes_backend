from datetime import datetime, timedelta
from enum import Enum
from ipaddress import ip_address
from typing import Literal

from pydantic import computed_field, field_validator, model_validator
from sqlalchemy import JSON, Index
from sqlmodel import Field

from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, SoftDeleteMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType
from src.utils.timezone import timezone


class AppStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class AppType(str, Enum):
    ECS = "ECS"
    RCS = "RCS"
    WMS = "WMS"
    ThirdParty = "Third-Party"


class ValidityPeriod(str, Enum):
    """有效期枚举"""

    ONE_DAY = "1d"  # 1天
    ONE_WEEK = "1w"  # 1周
    ONE_MONTH = "1m"  # 1个月
    SIX_MONTHS = "6m"  # 6个月
    ONE_YEAR = "1y"  # 1年
    NEVER = "never"  # 永不过期

    def to_timedelta(self) -> timedelta | None:
        """转换为 timedelta 对象"""
        mapping = {
            self.ONE_DAY: timedelta(days=1),
            self.ONE_WEEK: timedelta(weeks=1),
            self.ONE_MONTH: timedelta(days=30),
            self.SIX_MONTHS: timedelta(days=180),
            self.ONE_YEAR: timedelta(days=365),
            self.NEVER: None,
        }
        return mapping[self]

    @classmethod
    def from_description(cls) -> dict[str, str]:
        """返回枚举值的中文描述"""
        return {
            "1d": "1天",
            "1w": "1周",
            "1m": "1个月",
            "6m": "6个月",
            "1y": "1年",
            "never": "永不过期",
        }


class APIApplicationBase(BaseMixin):
    app_name: str = Field(max_length=100, description="应用名称")
    app_type: AppType = Field(default=AppType.ECS, description="应用类型")
    description: str | None = Field(default=None, max_length=500, description="应用描述")
    ip_whitelist: list[str] | None = Field(default=None, description="IP白名单")
    rate_limit_per_minute: int = Field(default=100, ge=1, le=10000, description="每分钟请求限制")
    rate_limit_per_hour: int = Field(default=5000, ge=1, le=1000000, description="每小时请求限制")

    validity_period: ValidityPeriod = Field(default=ValidityPeriod.ONE_YEAR, description="有效期时长")

    @field_validator("ip_whitelist")
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
        # 添加 None 检查，防止验证器在实例化过程中提前执行时出错
        rate_limit_min = self.rate_limit_per_minute
        rate_limit_hour = self.rate_limit_per_hour

        if (rate_limit_min and rate_limit_hour) and rate_limit_hour >= (rate_limit_min * 60):
            raise ValueError(f"小时限制 ({rate_limit_hour}) 应该 < 分钟限制 * 60 ({rate_limit_min * 60})")
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

    app_id: str = Field(max_length=50, description="应用ID")
    app_secret_encrypted: str = Field(max_length=500, description="加密后的密钥")
    status: AppStatus = Field(default=AppStatus.ACTIVE, description="状态")
    ip_whitelist: list[str] | None = Field(default=None, sa_type=JSON, description="IP白名单")

    expires_at: datetime | None = Field(default=None, description="过期时间")

    __table_args__ = (
        Index("ix_api_applications_status", "status", "is_deleted"),
        Index("ix_api_applications_type", "app_type", "is_deleted"),
        Index("ix_api_applications_id", "id", unique=True),
        Index("ix_api_applications_app_id", "app_id", "is_deleted", unique=True, postgresql_where="NOT is_deleted"),
    )


class APIApplicationCreate(ModelFactory(APIApplicationBase).for_create()):
    pass


class APIApplicationUpdate(ModelFactory(APIApplicationBase).for_optimistic_update()):
    pass


class ResetValidityPeriodSchema(BaseMixin):
    """重置有效期 Schema"""

    version: int = Field(default=0, description="数据版本")
    validity_period: ValidityPeriod = Field(description="新的有效期时长")


class APIApplicationResponse(APIApplicationBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin):
    app_id: str
    version: int
    status: AppStatus = Field(default=AppStatus.ACTIVE)
    expires_at: datetime | None = None

    @computed_field
    @property
    def remaining_days(self) -> int | None:
        """剩余天数"""
        if self.expires_at is None:
            return None

        delta = self.expires_at - timezone.now_for_db()
        return max(0, delta.days)
