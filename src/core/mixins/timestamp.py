"""
时间戳 Mixin

为模型添加时间戳字段和自动更新功能:
- created_at: 自动设置创建时间
- updated_at: 自动更新修改时间
"""

from datetime import datetime
from typing import Any

from sqlalchemy import event
from sqlmodel import Field

from src.core.mixins.base import BaseMixin


class TimestampMixin(BaseMixin):
    """
    时间戳 Mixin

    为模型添加 created_at 和 updated_at 字段
    - created_at: 自动设置创建时间
    - updated_at: 自动更新修改时间

    使用示例:
        class User(TimestampMixin, table=True):
            id: Optional[int] = Field(primary_key=True)
            name: str
    """

    # 延迟导入时区模块避免循环依赖
    @staticmethod
    def _get_now() -> datetime:
        from src.utils.timezone import timezone

        # 数据库使用 TIMESTAMP WITHOUT TIME ZONE,需要 UTC naive datetime
        return timezone.now_for_db()

    created_at: datetime = Field(
        default_factory=lambda: TimestampMixin._get_now(),
        sa_column_kwargs={
            "nullable": False,
            "comment": "创建时间 (UTC)",
        },
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_column_kwargs={
            "nullable": True,
            "comment": "更新时间 (UTC)",
        },
    )


# ==================== SQLAlchemy 事件监听器 ====================


@event.listens_for(TimestampMixin, "before_update", propagate=True)
def timestamp_before_update(mapper: Any, connection: Any, target: TimestampMixin) -> None:
    """
    自动更新 updated_at 字段

    在任何继承 TimestampMixin 的模型更新之前,
    自动将 updated_at 设置为当前 UTC 时间。

    使用示例:
        user = await db.get(User, 1)
        user.email = 'new@email'
        await db.commit()
        # updated_at 自动更新,无需手动设置
    """
    from src.utils.timezone import timezone

    target.updated_at = timezone.now_for_db()
