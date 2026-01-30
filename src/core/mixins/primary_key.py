"""
主键 Mixin

提供不同类型的主键生成策略:
- PrimaryKeyMixin: 根据配置动态选择的主键 Mixin
"""

from sqlalchemy import BigInteger
from sqlmodel import Field

from src.core.conf import settings
from src.core.mixins.base import BaseMixin

# 动态主键 Mixin(根据配置自动选择)
# 根据配置动态设置 id 字段
if settings.USE_SNOWFLAKE_ID:
    from src.utils.snowflake import generate_snowflake_id

    # type: ignore - 动态生成的类,无法进行静态类型检查
    class PrimaryKeyMixin(BaseMixin):  # type: ignore[misc]
        """
        动态主键 Mixin(根据配置自动选择)
        """

        id: int | None = Field(
            default_factory=generate_snowflake_id,
            primary_key=True,
            index=True,
            sa_type=BigInteger,
            sa_column_kwargs={"nullable": False, "comment": "雪花算法主键 ID"},
        )  # type: ignore[assignment]
else:
    # type: ignore - 动态生成的类,无法进行静态类型检查
    class PrimaryKeyMixin(BaseMixin):  # type: ignore[misc]
        """
        动态主键 Mixin(根据配置自动选择)
        """

        id: int | None = Field(
            default=None,
            primary_key=True,
            index=True,
            unique=True,
            sa_type=BigInteger,
            sa_column_kwargs={
                "autoincrement": True,
                "nullable": False,
                "index": True,
                "unique": True,
                "comment": "主键 ID",
            },
        )  # type: ignore[assignment]
