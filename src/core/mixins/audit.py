"""
审计 Mixin (Model 层)

为模型添加审计字段,记录创建人和更新人信息
"""

from sqlalchemy import BigInteger
from sqlmodel import Field

from src.core.mixins.timestamp import TimestampMixin


class AuditMixin(TimestampMixin):
    """
    审计 Mixin

    为模型添加 created_by, updated_by, created_at, updated_at 字段
    - created_by: 创建人ID
    - updated_by: 更新人ID
    - created_at: 自动设置创建时间(继承自 TimestampMixin)
    - updated_at: 自动更新修改时间(继承自 TimestampMixin)

    使用示例:
        class User(AuditMixin, table=True):
            id: Optional[int] = Field(primary_key=True)
            name: str
    """

    created_by: int | None = Field(
        default=None,
        sa_type=BigInteger,
        sa_column_kwargs={"nullable": True, "comment": "创建人ID"},
    )
    updated_by: int | None = Field(
        default=None,
        sa_type=BigInteger,
        sa_column_kwargs={"nullable": True, "comment": "更新人ID"},
    )
