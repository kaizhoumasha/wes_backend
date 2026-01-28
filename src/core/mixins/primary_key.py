"""
主键 Mixin

提供不同类型的主键生成策略:
- IntPKMixin: 整型自增主键
- SnowflakePKMixin: 雪花算法主键(分布式系统)
- PrimaryKeyMixin: 根据配置动态选择的主键 Mixin
"""

from sqlalchemy import BigInteger
from sqlmodel import Field

from src.core.mixins.base import BaseMixin


class IntPKMixin(BaseMixin):
    """
    整型自增主键 Mixin

    使用 BIGINT 类型以保持与雪花 ID 的一致性,
    确保外键关联时类型匹配。

    使用示例:
        class User(IntPKMixin, table=True):
            name: str
    """

    id: int | None = Field(
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
    雪花算法主键 Mixin(53位方案 - JavaScript 安全)

    使用 Twitter Snowflake 算法生成分布式唯一 ID
    优点:
    - 全局唯一(分布式系统)
    - 趋势递增(按时间排序)
    - 高性能(本地生成,无需网络交互)
    - 长期使用(69年无需迁移)
    - JavaScript 安全整数范围内,前端无需使用 BigInt

    ID 结构(53 位):
    - 41 位时间戳(毫秒,可使用约 69 年)
    - 3 位数据中心 ID(0-7)
    - 3 位工作机器 ID(0-7)
    - 6 位序列号(0-63)

    JavaScript 兼容性:
    - 生成的 ID 保证在安全整数范围内(≤ 9007199254740991)
    - 前端可直接使用 Number 类型处理
    - 15-16位十进制数字(如: 123456789012345)
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
        from src.utils.snowflake import generate_snowflake_id

        return generate_snowflake_id()

    # 使用 BigInteger 类型
    id: int | None = Field(
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

    由 USE_SNOWFLAKE_ID 环境变量控制:
    - True: 使用 SnowflakePKMixin(分布式系统)
    - False: 使用 IntPKMixin(单机应用)

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
        # 如果配置不可用,默认使用自增ID
        use_snowflake = False

    if use_snowflake:
        return SnowflakePKMixin
    return IntPKMixin


# 动态主键 Mixin(根据配置自动选择)
# type: ignore - 动态生成的类,无法进行静态类型检查
PrimaryKeyMixin = _create_primary_key_mixin()
