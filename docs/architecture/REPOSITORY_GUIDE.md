# 通用 Repository 使用指南

## 概述

`BaseRepository[T]` 是一个泛型基类，为所有 SQLModel 提供通用的 CRUD 操作，避免重复代码。

## 架构设计

```text
BaseRepository[T]
    ↓
UserRepository(BaseRepository[User])
    ↓
UserService(UserRepository)
    ↓
API 层
```

## 基础使用

### 1. 直接使用 BaseRepository

```python
from src.database.base_repository import BaseRepository
from src.app.admin.models import User

# 创建仓库实例
user_repo = BaseRepository[User](User)

# CRUD 操作
user = await user_repo.get_by_id(db, 1)
total, users = await user_repo.get_list(db)
new_user = await user_repo.create(db, {"username": "test", "email": "test@example.com"})
```

### 2. 继承创建特定 Repository（推荐）

```python
from src.database.base_repository import BaseRepository
from src.app.admin.models import User

class UserRepository(BaseRepository[User]):
    """用户仓库 - 继承通用 CRUD 能力"""

    def __init__(self):
        super().__init__(User)

    # 扩展用户特定的查询方法
    async def get_by_username(self, db: AsyncSession, username: str):
        return await self.get_by_field(db, "username", username)

    async def get_active_users(self, db: AsyncSession):
        _, users = await self.get_list(db, limit=1000, where_clauses_raw=[User.is_active])
        return users

# 使用
user_repo = UserRepository()
active_users = await user_repo.get_active_users(db)
```

## 通用 CRUD 方法

### 查询方法

| 方法 | 说明 | 示例 |
|------|------|------|
| `get_by_id(db, id)` | 根据 ID 查询 | `await repo.get_by_id(db, 1)` |
| `get_by_field(db, field_name, value)` | 根据字段查询 | `await repo.get_by_field(db, "username", "admin")` |
| `get_list(db, limit, offset, filters, sort)` | 获取总数与记录列表 | `total, items = await repo.get_list(db, limit=10)` |
| `exists(db, **kwargs)` | 检查是否存在 | `await repo.exists(db, username="admin")` |
| `count(db, where_clauses)` | 统计数量 | `await repo.count(db, where_clauses=[User.is_active])` |

### 修改方法

| 方法 | 说明 | 示例 |
|------|------|------|
| `create(db, data)` | 创建记录 | `await repo.create(db, {"username": "test"})` |
| `update(db, id, data)` | 更新记录 | `await repo.update(db, 1, {"email": "new@example.com"})` |
| `delete(db, id)` | 删除记录 | `await repo.delete(db, 1)` |
| `bulk_create(db, items)` | 批量创建 | `await repo.bulk_create(db, [{"name": "test1"}, {"name": "test2"}])` |

## 概念示例

以下 `Product`、`Order` 仅用于说明 Repository 扩展方式，不代表当前领域模型或项目内已存在模块。实现时必须替换为
当前领域对象，并遵循 API → Service → Repository → Database 分层。

### 示例 1: Product Repository

```python
from src.database.base_repository import BaseRepository
from src.app.admin.models import Product

class ProductRepository(BaseRepository[Product]):
    """产品仓库"""

    def __init__(self):
        super().__init__(Product)

    async def get_by_category(self, db: AsyncSession, category_id: int):
        """获取指定分类的产品"""
        _, products = await self.get_list(
            db, limit=1000,
            where_clauses_raw=[Product.category_id == category_id],
            order_by_raw=[Product.created_at.desc()]
        )
        return products

    async def get_in_stock(self, db: AsyncSession):
        """获取有库存的产品"""
        _, products = await self.get_list(
            db, limit=1000,
            where_clauses_raw=[Product.stock > 0],
            order_by_raw=[Product.stock.desc()]
        )
        return products

# 使用
product_repo = ProductRepository()
products = await product_repo.get_in_stock(db)
```

### 示例 2: Order Repository

```python
from src.database.base_repository import BaseRepository
from src.app.admin.models import Order

class OrderRepository(BaseRepository[Order]):
    """订单仓库"""

    def __init__(self):
        super().__init__(Order)

    async def get_by_status(self, db: AsyncSession, status: str):
        """根据状态获取订单"""
        _, orders = await self.get_list(
            db, limit=1000,
            where_clauses_raw=[Order.status == status],
            order_by_raw=[Order.created_at.desc()]
        )
        return orders

    async def get_user_orders(self, db: AsyncSession, user_id: int):
        """获取用户订单"""
        _, orders = await self.get_list(
            db, limit=1000,
            where_clauses_raw=[Order.user_id == user_id],
            order_by_raw=[Order.created_at.desc()]
        )
        return orders

# 使用
order_repo = OrderRepository()
pending_orders = await order_repo.get_by_status(db, "pending")
```

## 与 Service 层配合

```python
class UserService:
    """用户服务"""

    def __init__(self, user_repo: UserRepository):
        self.user_repo = user_repo

    async def get_user_with_profile(self, db, user_id: int):
        """获取用户及其资料"""
        user = await self.user_repo.get_by_id(db, user_id)
        if not user:
            raise NotFoundException("用户不存在")
        return user

    async def search_users(self, db, keyword: str):
        """搜索用户"""
        _, users = await self.user_repo.get_list(
            db, limit=1000,
            where_clauses_raw=[
                User.username.contains(keyword) | User.email.contains(keyword)
            ]
        )
        return users
```

## 优势总结

| 特性 | 传统方式 | 使用 BaseRepository |
|------|---------|-------------------|
| **代码复用** | ❌ 每个 Repository 重复 CRUD | ✅ 继承通用方法 |
| **维护成本** | ❌ 修改需要改 N 个文件 | ✅ 只需修改 BaseRepository |
| **一致性** | ❌ 每个 Repository 实现可能不同 | ✅ 统一的接口和行为 |
| **扩展性** | ❌ 添加新功能需要改所有类 | ✅ 在 BaseRepository 添加即可 |
| **类型安全** | ⚠️ 需要手动维护 | ✅ 泛型保证类型安全 |

## 注意事项

1. **特定逻辑放在子类**：通用操作在 BaseRepository，特定业务逻辑在子类
2. **不要过度抽象**：如果某个操作只有特定 Model 需要，放在子类而不是 BaseRepository
3. **保持简单**：BaseRepository 只提供最常用的 CRUD，复杂查询在子类实现
4. **事务管理**：在 Service 层管理事务，Repository 层只负责数据访问
