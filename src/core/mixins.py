"""
SQLModel Mixin 类

提供可复用的模型字段和行为，遵循 DRY 原则

参考: https://sqlmodel.tiangolo.com/tutorial/automatic_id_none_refresh/
"""

from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel
from sqlalchemy import Column, DateTime, event, Integer, BigInteger
from sqlalchemy.dialects.postgresql import BIGINT


# ==================== 基础 Mixin ====================
class BaseMixin(SQLModel):
    """
    基础 Mixin

    系统内所有数据类的通用基类
    """

    pass


# ==================== 主键 Mixin ====================
class IntPKMixin(BaseMixin):
    """
    整型自增主键 Mixin

    使用 BIGINT 类型以保持与雪花 ID 的一致性，
    确保外键关联时类型匹配。

    使用示例:
        class User(IntPKMixin, table=True):
            name: str
    """

    id: Optional[int] = Field(
        default=None,
        sa_type=BigInteger,  # 统一使用 BIGINT
        sa_column_kwargs={
            "autoincrement": "auto",
            "nullable": False,
            "primary_key": True,
            "comment": "主键 ID",
        },
    )


class SnowflakePKMixin(BaseMixin):
    """
    雪花算法主键 Mixin（53位方案 - JavaScript 安全）

    使用 Twitter Snowflake 算法生成分布式唯一 ID
    优点：
    - 全局唯一（分布式系统）
    - 趋势递增（按时间排序）
    - 高性能（本地生成，无需网络交互）
    - 长期使用（69年无需迁移）
    - JavaScript 安全整数范围内，前端无需使用 BigInt

    ID 结构（53 位）:
    - 41 位时间戳（毫秒，可使用约 69 年）
    - 3 位数据中心 ID（0-7）
    - 3 位工作机器 ID（0-7）
    - 6 位序列号（0-63）

    JavaScript 兼容性:
    - 生成的 ID 保证在安全整数范围内（≤ 9007199254740991）
    - 前端可直接使用 Number 类型处理
    - 15-16位十进制数字（如：123456789012345）
    - 无需 BigInt

    节点配置:
    - 最多 8×8 = 64 个节点
    - 适合中小型分布式系统
    - 每个节点每毫秒可生成 64 个 ID

    使用示例:
        class Product(SnowflakePKMixin, table=True):
            name: str

        # 自动生成雪花 ID
        product = Product(name="iPhone")
        print(product.id)  # 例如: 123456789012345

    注意:
    - 这是 JavaScript 安全的标准方案
    - 64个节点对大多数项目完全够用
    - 从2024年开始可用到2093年

    参考: https://developer.twitter.com/en/docs/twitter-ids
    """

    @staticmethod
    def _generate_snowflake_id() -> int:
        """
        生成雪花 ID

        可在子类中重写此方法以自定义生成逻辑
        """
        # 延迟导入避免循环依赖
        from src.core.snowflake import generate_snowflake_id

        return generate_snowflake_id()

    # 使用 BigInteger 类型
    id: Optional[int] = Field(
        default_factory=lambda: SnowflakePKMixin._generate_snowflake_id(),
        primary_key=True,
        index=True,
        sa_type=BigInteger,
        sa_column_kwargs={
            "nullable": False,
            "comment": "雪花算法主键 ID",
        },
    )


def _create_primary_key_mixin():
    """
    根据配置动态创建主键 Mixin

    由 USE_SNOWFLAKE_ID 环境变量控制：
    - True: 使用 SnowflakePKMixin（分布式系统）
    - False: 使用 IntPKMixin（单机应用）

    使用示例:
        class User(PrimaryKeyMixin, table=True):
            name: str

        # 根据配置自动选择主键类型
        # USE_SNOWFLAKE_ID=true  → 使用雪花ID
        # USE_SNOWFLAKE_ID=false → 使用自增ID
    """
    try:
        from src.core.conf import settings

        use_snowflake = settings.USE_SNOWFLAKE_ID
    except (ImportError, AttributeError):
        # 如果配置不可用，默认使用自增ID
        use_snowflake = False

    if use_snowflake:
        return SnowflakePKMixin
    else:
        return IntPKMixin


# 动态主键 Mixin（根据配置自动选择）
PrimaryKeyMixin = _create_primary_key_mixin()


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
        from src.core.timezone import timezone

        # 数据库使用 TIMESTAMP WITHOUT TIME ZONE，需要 UTC naive datetime
        return timezone.now_for_db()

    created_at: datetime = Field(
        default_factory=lambda: TimestampMixin._get_now(),
        sa_column_kwargs={
            "nullable": False,
            "comment": "创建时间 (UTC)",
        },
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        # default_factory=lambda: TimestampMixin._get_now(),
        sa_column_kwargs={
            "nullable": True,
            "comment": "更新时间 (UTC)",
        },
    )


# ==================== SQLAlchemy 事件监听器 ====================


@event.listens_for(TimestampMixin, "before_update", propagate=True)
def timestamp_before_update(mapper, connection, target):
    """
    自动更新 updated_at 字段

    在任何继承 TimestampMixin 的模型更新之前，
    自动将 updated_at 设置为当前 UTC 时间。

    使用示例:
        user = await db.get(User, 1)
        user.email = 'new@email'
        await db.commit()
        # updated_at 自动更新，无需手动设置
    """
    from src.core.timezone import timezone

    target.updated_at = timezone.now_for_db()


class AuditMixin(TimestampMixin):
    """
    审计 Mixin

    为模型添加 created_by, updated_by, created_at, updated_at 字段
    - created_by: 创建人ID
    - updated_by: 更新人ID
    - created_at: 自动设置创建时间（继承自 TimestampMixin）
    - updated_at: 自动更新修改时间（继承自 TimestampMixin）

    使用示例:
        class User(AuditMixin, table=True):
            id: Optional[int] = Field(primary_key=True)
            name: str
    """

    created_by: Optional[int] = Field(
        default=None,
        sa_column_kwargs={"nullable": True, "comment": "创建人ID"},
    )
    updated_by: Optional[int] = Field(
        default=None,
        sa_column_kwargs={"nullable": True, "comment": "更新人ID"},
    )


class SoftDeleteMixin(BaseMixin):
    """
    软删除 Mixin

    为模型添加软删除功能，数据不会被物理删除
    - deleted_at: 删除时间（None 表示未删除）
    - is_deleted: 删除标记

    使用示例:
        class Article(SoftDeleteMixin, table=True):
            id: Optional[int] = Field(primary_key=True)
            title: str

        article.soft_delete()  # 标记为已删除
        article.restore()     # 恢复已删除的记录
    """

    deleted_by: Optional[int] = Field(
        default=None,
        sa_column_kwargs={"nullable": True, "comment": "删除人ID"},
    )
    deleted_at: datetime | None = Field(
        default=None,
        sa_column_kwargs={"nullable": True, "comment": "删除时间"},
    )
    is_deleted: bool = Field(default=False, sa_column_kwargs={"comment": "是否已删除"})

    def soft_delete(self, deleted_by: Optional[int] = None) -> None:
        """
        标记为已删除

        :param deleted_by: 删除人ID（如果模型有 AuditMixin）
        """
        from src.core.timezone import timezone

        self.is_deleted = True
        self.deleted_at = timezone.now()
        if deleted_by is not None and hasattr(self, "deleted_by"):
            self.deleted_by = deleted_by

    def restore(self) -> None:
        """恢复已删除的记录"""
        self.is_deleted = False
        self.deleted_at = None
        if hasattr(self, "deleted_by"):
            self.deleted_by = None


class ReprMixin(BaseMixin):
    """
    通用 __repr__ Mixin

    自动生成包含所有字段值的字符串表示

    使用示例:
        class User(ReprMixin, table=True):
            id: Optional[int] = Field(primary_key=True)
            name: str

        print(user)  # <User(id=1, name='test')>
    """

    def __repr__(self) -> str:
        class_name = self.__class__.__name__
        attributes = [
            f"{k}={repr(v)}" for k, v in self.__dict__.items() if not k.startswith("_")
        ]
        return f"<{class_name}({', '.join(attributes)})>"


# ==================== 组合 Mixin ====================


class BaseModelMixin(TimestampMixin, ReprMixin):
    """
    基础模型 Mixin

    组合了最常用的 Mixin：时间戳 + repr
    适用于大多数业务模型

    使用示例:
        class User(BaseModelMixin, table=True):
            id: Optional[int] = Field(default=None, primary_key=True)
            name: str
    """

    pass


class AuditModelMixin(AuditMixin, ReprMixin):
    """
    审计模型 Mixin

    组合了审计字段 + repr
    适用于需要审计追踪的业务模型

    使用示例:
        class Article(AuditModelMixin, table=True):
            id: Optional[int] = Field(default=None, primary_key=True)
            title: str

        # 创建时记录创建人
        article = Article(title="测试", created_by=user_id)
    """

    pass


class FullModelMixin(AuditModelMixin, SoftDeleteMixin):
    """
    完整模型 Mixin

    组合了所有 Mixin：时间戳 + 审计 + 软删除 + repr
    适用于需要完整功能的模型

    使用示例:
        class Article(FullModelMixin, table=True):
            id: Optional[int] = Field(default=None, primary_key=True)
            title: str

        # 创建
        article = Article(title="测试", created_by=1)

        # 更新
        article.title = "新标题"
        article.updated_by = 2

        # 软删除（自动设置 deleted_by）
        article.soft_delete(deleted_by=3)
    """

    pass


# ==================== 带主键的组合 Mixin ====================


class BaseTableModelMixin(PrimaryKeyMixin, BaseModelMixin):
    """
    基础表模型 Mixin（主键）

    组合了：主键(自增/雪花) + 时间戳 + repr
    最常用的表模型配置

    使用示例:
        class User(BaseTableModelMixin, table=True):
            username: str
            email: str

        # 无需定义 id，自动继承
    """

    pass
