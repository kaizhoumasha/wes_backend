"""
乐观锁 Mixin

为模型添加乐观锁支持,防止并发修改导致的更新丢失。

设计理念:
- 使用 version 字段检测并发修改
- 自动在 BaseRepository 中注册版本验证 Hook
- 更新时自动递增版本号
- 符合 FastAPI 和 SQLAlchemy 最佳实践

使用示例:
    from src.core.mixins import BaseTableModelMixin, OptimisticLockMixin

    class Product(BaseTableModelMixin, OptimisticLockMixin, table=True):
        name: str
        price: float

    # 创建产品 (version = 0)
    product = await product_repo.create(db, {"name": "iPhone", "price": 5999})

    # 用户 A 获取产品 (version = 0)
    product_a = await product_repo.get_by_id(db, product.id)

    # 用户 B 获取产品 (version = 0)
    product_b = await product_repo.get_by_id(db, product.id)

    # 用户 A 先更新 (version = 0 -> 1)
    await product_repo.update(db, product.id, {"name": "iPhone 15", "version": 0})

    # 用户 B 后更新 (version = 0 != 1) -> 抛出 OptimisticLockError
    try:
        await product_repo.update(db, product.id, {"price": 6999, "version": 0})
    except OptimisticLockError as e:
        print(f"更新失败: {e}")  # "记录已被其他用户修改，请刷新后重试"
"""

from sqlmodel import Field

from src.core.mixins.base import BaseMixin


class OptimisticLockMixin(BaseMixin):
    """
    乐观锁 Mixin

    为模型添加 version 字段,用于检测并发修改。

    字段:
        version: 版本号,每次更新自动递增

    工作原理:
        1. 读取记录时获取当前 version
        2. 更新时验证 version 是否变化
        3. 如果 version 匹配则更新并递增
        4. 如果 version 不匹配则抛出 OptimisticLockError

    注意:
        - 此 Mixin 会自动在 BaseRepository 中注册版本验证 Hook
        - 更新时必须在数据中包含 version 字段
        - 删除操作不受乐观锁影响

    最佳实践:
        - 在需要防止并发修改的关键业务模型上使用
        - 如订单状态、库存数量、价格等
        - 前端应显示友好提示并引导用户刷新数据
    """

    version: int = Field(
        default=0,
        sa_column_kwargs={
            "nullable": False,
            "comment": "版本号",
            "default": 0,
        },
    )

    def increment_version(self) -> None:
        """
        递增版本号

        在更新操作时调用，自动将版本号加 1。
        此方法由 BaseRepository 在更新时自动调用。
        """
        self.version += 1  # type: ignore[assignment]


__all__ = ["OptimisticLockMixin"]
