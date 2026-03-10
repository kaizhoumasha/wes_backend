"""
乐观锁机制测试

测试 OptimisticLockMixin 和 BaseRepository 的乐观锁功能。

测试场景:
1. 创建记录时 version 初始化为 0
2. 更新记录时 version 自动递增
3. 并发修改时检测版本冲突
4. 缺少 version 字段时抛出异常
"""

import pytest
from pydantic import ValidationError
from sqlmodel import Field, SQLModel

from src.core.exceptions import OptimisticLockException
from src.core.mixins import BaseMixin, DataTableMixin, OptimisticLockMixin
from src.database.base_repository import BaseRepository
from src.database.model_factory import ModelFactory


class Product(DataTableMixin, OptimisticLockMixin, table=True):
    """测试产品模型（带乐观锁）"""

    __tablename__ = "test_products"

    name: str = Field(max_length=100)
    price: float = Field(ge=0)
    stock: int = Field(ge=0)


class ProductCreate(SQLModel):
    """产品创建 Schema"""

    name: str
    price: float
    stock: int


class ProductUpdate(SQLModel):
    """产品更新 Schema"""

    name: str | None = None
    price: float | None = None
    stock: int | None = None
    version: int | None = None


class ProductUpdateBase(BaseMixin):
    """用于测试 ModelFactory 乐观锁更新 Schema 的基础模型"""

    name: str = Field(max_length=100)
    price: float = Field(ge=0)
    stock: int = Field(ge=0)


OptimisticProductUpdate = ModelFactory(ProductUpdateBase).for_optimistic_update()


def test_for_optimistic_update_requires_version() -> None:
    """for_optimistic_update 应生成 version 必填的更新模型"""
    assert "version" in OptimisticProductUpdate.model_fields
    assert OptimisticProductUpdate.model_fields["version"].is_required()

    update = OptimisticProductUpdate(name="iPhone 15", version=3)
    assert update.version == 3
    assert update.price is None

    with pytest.raises(ValidationError):
        OptimisticProductUpdate(name="iPhone 15")


@pytest.mark.asyncio
async def test_optimistic_lock_initialization(db_session):
    """测试乐观锁初始化 - 创建记录时 version 应为 0"""
    product_repo = BaseRepository[Product](Product)

    # 创建产品
    product = await product_repo.create(
        db_session,
        {"name": "iPhone 15", "price": 5999.0, "stock": 100},
    )
    await db_session.commit()

    # 验证 version 初始化为 0
    assert product.version == 0
    assert product.id is not None
    assert product.name == "iPhone 15"


@pytest.mark.asyncio
async def test_optimistic_lock_auto_increment(db_session):
    """测试版本号自动递增"""
    product_repo = BaseRepository[Product](Product)

    # 创建产品
    product = await product_repo.create(
        db_session,
        {"name": "iPhone 15", "price": 5999.0, "stock": 100},
    )
    await db_session.commit()

    # 第一次更新 (version: 0 -> 1)
    updated_product = await product_repo.update(
        db_session,
        product.id,
        {"name": "iPhone 15 Pro", "price": 6999.0, "version": 0},
    )
    await db_session.commit()

    assert updated_product.version == 1
    assert updated_product.name == "iPhone 15 Pro"
    assert updated_product.price == 6999.0

    # 第二次更新 (version: 1 -> 2)
    updated_product = await product_repo.update(
        db_session,
        product.id,
        {"stock": 50, "version": 1},
    )
    await db_session.commit()

    assert updated_product.version == 2
    assert updated_product.stock == 50


@pytest.mark.asyncio
async def test_optimistic_lock_conflict_detection(db_session):
    """测试并发修改冲突检测"""
    product_repo = BaseRepository[Product](Product)

    # 创建产品
    product = await product_repo.create(
        db_session,
        {"name": "iPhone 15", "price": 5999.0, "stock": 100},
    )
    await db_session.commit()

    # 用户 A 获取产品 (version = 0)
    product_a = await product_repo.get_by_id(db_session, product.id)
    assert product_a.version == 0

    # 用户 B 获取产品 (version = 0)
    product_b = await product_repo.get_by_id(db_session, product.id)
    assert product_b.version == 0

    # 用户 A 先更新 (version: 0 -> 1)
    updated_a = await product_repo.update(
        db_session,
        product.id,
        {"name": "iPhone 15 Pro", "price": 6999.0, "version": 0},
    )
    await db_session.commit()
    assert updated_a.version == 1

    # 用户 B 后更新 (version: 0 != 1) -> 应该抛出 OptimisticLockException
    with pytest.raises(OptimisticLockException) as exc_info:
        await product_repo.update(
            db_session,
            product.id,
            {"stock": 50, "version": 0},  # 旧版本号
        )
        await db_session.commit()

    # 验证异常信息
    exception = exc_info.value
    assert exception.code == "OPTIMISTIC_LOCK"
    assert "Product" in exception.detail.get("resource_type", "")
    assert exception.detail.get("current_version") == 1
    assert exception.detail.get("provided_version") == 0
    assert "已被其他用户修改" in exception.message


@pytest.mark.asyncio
async def test_optimistic_lock_missing_version(db_session):
    """测试缺少 version 字段时抛出异常"""
    product_repo = BaseRepository[Product](Product)

    # 创建产品
    product = await product_repo.create(
        db_session,
        {"name": "iPhone 15", "price": 5999.0, "stock": 100},
    )
    await db_session.commit()

    # 尝试更新但不提供 version 字段 -> 应该抛出 OptimisticLockException
    with pytest.raises(OptimisticLockException) as exc_info:
        await product_repo.update(
            db_session,
            product.id,
            {"name": "iPhone 15 Pro"},  # 缺少 version 字段
        )
        await db_session.commit()

    # 验证异常信息
    exception = exc_info.value
    assert exception.code == "OPTIMISTIC_LOCK"
    assert "缺少 version 字段" in exception.message


@pytest.mark.asyncio
async def test_optimistic_lock_correct_retry(db_session):
    """测试正确的重试流程 - 刷新后重试

    注意：此测试使用硬编码的版本号而不是 ORM 对象的 version 属性，
    因为在同一会话中，SQLAlchemy 会自动刷新 ORM 对象的状态。
    """
    product_repo = BaseRepository[Product](Product)

    # 创建产品
    product = await product_repo.create(
        db_session,
        {"name": "iPhone 15", "price": 5999.0, "stock": 100},
    )
    await db_session.commit()
    assert product.version == 0

    # 用户 B 获取产品并更新
    updated_b = await product_repo.update(
        db_session,
        product.id,
        {"price": 6999.0, "version": 0},  # 使用当前版本号 0
    )
    await db_session.commit()
    assert updated_b.version == 1

    # 用户 A 尝试使用旧版本号（0）更新 -> 应该失败
    with pytest.raises(OptimisticLockException):
        await product_repo.update(
            db_session,
            product.id,
            {"stock": 50, "version": 0},  # 旧版本号，应该失败
        )

    # 用户 A 刷新数据后重试 -> 成功
    fresh_product = await product_repo.get_by_id(db_session, product.id)
    assert fresh_product.version == 1

    updated_a = await product_repo.update(
        db_session,
        product.id,
        {"stock": 50, "version": 1},  # 使用最新版本号
    )
    await db_session.commit()
    assert updated_a.version == 2
    assert updated_a.stock == 50


@pytest.mark.asyncio
async def test_optimistic_lock_partial_update(db_session):
    """测试部分更新（只更新部分字段）时版本号仍然递增"""
    product_repo = BaseRepository[Product](Product)

    # 创建产品
    product = await product_repo.create(
        db_session,
        {"name": "iPhone 15", "price": 5999.0, "stock": 100},
    )
    await db_session.commit()
    assert product.version == 0

    # 只更新价格
    updated = await product_repo.update(
        db_session,
        product.id,
        {"price": 6999.0, "version": 0},
    )
    await db_session.commit()

    assert updated.version == 1
    assert updated.price == 6999.0
    assert updated.name == "iPhone 15"  # 未修改
    assert updated.stock == 100  # 未修改


@pytest.mark.asyncio
async def test_model_without_optimistic_lock(db_session):
    """测试没有 OptimisticLockMixin 的模型不受影响"""
    from sqlalchemy import Table
    from sqlmodel import Field

    from src.core.mixins import DataTableMixin

    class SimpleProduct(DataTableMixin, table=True):
        """没有乐观锁的产品模型"""

        __tablename__ = "test_simple_products"

        name: str = Field(max_length=100)
        price: float = Field(ge=0)

    # 手动创建表（因为类是动态定义的，不在 SQLModel.metadata 中）
    async with db_session.begin():
        conn = await db_session.connection()
        await conn.run_sync(lambda sync_conn: SimpleProduct.__table__.create(sync_conn, checkfirst=True))

    simple_repo = BaseRepository[SimpleProduct](SimpleProduct)

    # 创建产品（没有 version 字段）
    product = await simple_repo.create(
        db_session,
        {"name": "Simple Product", "price": 100.0},
    )
    await db_session.commit()

    # 更新时不提供 version 字段（应该正常工作）
    updated = await simple_repo.update(
        db_session,
        product.id,
        {"price": 200.0},  # 没有 version 字段，也能正常更新
    )
    await db_session.commit()

    assert updated.price == 200.0
    assert not hasattr(updated, "version")
