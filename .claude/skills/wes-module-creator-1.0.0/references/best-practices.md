# WES Backend 最佳实践

本文档提供 WES Backend 项目的最佳实践指南。

## 模型设计最佳实践

### 1. Base + Table 分离模式

**原则**：将业务字段和表特有字段分离

```python
# ✅ 正确：Base 只包含业务字段
class WarehouseBase(BaseMixin):
    """业务字段（用于 Schema 复用）"""
    name: str = Field(max_length=50)
    code: str = Field(max_length=20)

class Warehouse(WarehouseBase, DataTableMixin, EnterpriseMixin, table=True):
    """数据库表（Base + Mixins + 表特有字段）"""
    __tablename__ = "warehouses"
    capacity: int | None = None  # 表特有字段

# ❌ 错误：Base 包含 Mixin 字段
class WarehouseBase(DataTableMixin, BaseMixin):
    name: str
    # 问题：Schema 会包含 id, created_at 等系统字段
```

**优势**：
- Schema 复用更清晰
- 避免 Create/Update Schema 包含系统字段
- 符合 DRY 原则

### 2. ModelFactory 自动生成 Schema

**原则**：使用 ModelFactory 自动生成 Create/Update Schema

```python
# ✅ 正确：使用 ModelFactory
class WarehouseCreate(ModelFactory(WarehouseBase).for_create()):
    """自动生成，所有字段必需"""
    pass

class WarehouseUpdate(ModelFactory(WarehouseBase).for_update()):
    """自动生成，所有字段可选"""
    pass

# ❌ 错误：手动定义
class WarehouseCreate(BaseModel):
    name: str
    code: str
    # 问题：重复代码，维护困难
```

**优势**：
- 自动排除系统字段（id, created_at, updated_at）
- Create 时字段必需，Update 时字段可选
- 保留 Pydantic 验证器
- 单例缓存，提高性能

### 3. Pydantic 验证

**原则**：在模型定义时添加验证规则

```python
# ✅ 正确：完整的验证
class WarehouseBase(BaseMixin):
    name: str = Field(
        min_length=2,
        max_length=50,
        description="仓库名称"
    )
    code: str = Field(
        min_length=2,
        max_length=20,
        pattern=r"^[A-Z0-9]+$",
        description="仓库编码"
    )
    email: str | None = Field(
        default=None,
        max_length=100,
        pattern=r"^[\w\.-]+@[\w\.-]+\.\w+$"
    )

# ❌ 错误：缺少验证
class WarehouseBase(BaseMixin):
    name: str
    code: str
    # 问题：无法防止无效数据
```

**常用验证**：
- `min_length` / `max_length` - 字符串长度
- `pattern` - 正则表达式
- `ge` / `le` - 数值范围
- `default` - 默认值
- `description` - 字段说明

## Repository 最佳实践

### 1. 继承正确的基类

```python
# 平面结构
class UserRepository(BaseRepository[User]):
    pass

# 树形结构
class MenuRepository(TreeRepository[Menu]):
    pass
```

### 2. 使用 Hook 扩展逻辑

**原则**：通过 Hook 添加业务逻辑，不修改基类

```python
# ✅ 正确：使用 Hook
class UserRepository(BaseRepository[User]):
    def __init__(self):
        super().__init__(User)
        self.add_hook(HookType.BEFORE_CREATE, self._validate_username)

    async def _validate_username(self, context: HookContext) -> None:
        data = context.params.get("data", {})
        username = data.get("username")
        if username and len(username) < 3:
            raise ValueError("用户名至少 3 个字符")

# ❌ 错误：重写 create 方法
class UserRepository(BaseRepository[User]):
    async def create(self, db, data):
        # 验证逻辑
        if len(data["username"]) < 3:
            raise ValueError("用户名至少 3 个字符")
        # 调用父类
        return await super().create(db, data)
        # 问题：破坏基类封装，难以维护
```

### 3. 自定义查询方法

**原则**：添加业务特定的查询方法

```python
class UserRepository(BaseRepository[User]):
    async def get_by_username(
        self,
        db: AsyncSession,
        username: str
    ) -> User | None:
        """根据用户名查询"""
        result = await db.execute(
            select(User).where(User.username == username)
        )
        return result.scalar_one_or_none()

    async def get_active_users(
        self,
        db: AsyncSession
    ) -> list[User]:
        """获取活跃用户"""
        result = await db.execute(
            select(User)
            .where(User.is_active == True)
            .order_by(User.created_at.desc())
        )
        return list(result.scalars().all())
```

## Service 最佳实践

### 1. 启用缓存

**原则**：为频繁查询的数据启用缓存

```python
# ✅ 正确：启用缓存
class UserService(BaseService[User, UserRepository]):
    def __init__(self):
        super().__init__(
            user_repository,
            enable_cache=True,
            cache_prefix="app:user:detail",
        )

# ❌ 错误：未启用缓存
class UserService(BaseService[User, UserRepository]):
    def __init__(self):
        super().__init__(user_repository)
        # 问题：每次查询都访问数据库
```

### 2. 业务方法封装

**原则**：在 Service 层封装业务逻辑

```python
class UserService(BaseService[User, UserRepository]):
    async def activate_user(
        self,
        db: AsyncSession,
        cache: CacheService,
        user_id: int
    ) -> User:
        """激活用户"""
        # 1. 获取用户
        user = await self.get_by_id(db, cache, user_id)
        if not user:
            raise ResourceNotFoundException(f"用户 {user_id} 不存在")

        # 2. 更新状态
        return await self.update(
            db,
            cache,
            user_id,
            {"is_active": True}
        )

    async def deactivate_user(
        self,
        db: AsyncSession,
        cache: CacheService,
        user_id: int
    ) -> User:
        """停用用户"""
        user = await self.get_by_id(db, cache, user_id)
        if not user:
            raise ResourceNotFoundException(f"用户 {user_id} 不存在")

        return await self.update(
            db,
            cache,
            user_id,
            {"is_active": False}
        )
```

### 3. Service 相互调用

**原则**：方法内部懒加载导入，避免循环依赖

```python
# ✅ 正确：方法内部导入
class InboundService(BaseService):
    async def confirm_inbound(self, db, inbound_id: int):
        # 1. 更新入库单
        await self.update(db, inbound_id, {"status": "confirmed"})

        # 2. 延迟导入（避免循环依赖）
        from src.app.warehousing.services import inventory_service

        # 3. 调用其他 Service
        await inventory_service.increase_stock(db, inbound_id)

# ❌ 错误：__init__ 中导入
class InboundService(BaseService):
    def __init__(self):
        super().__init__(inbound_repository)
        # 问题：可能导致循环导入
        from src.app.warehousing.services import inventory_service
        self.inventory_service = inventory_service
```

## API 最佳实践

### 1. 使用 BaseAPI 零代码生成

**原则**：继承 BaseAPI 自动生成标准 CRUD 路由

```python
# ✅ 正确：零代码生成
user_api = BaseAPI(
    module_name="admin",
    model=User,
    service=user_service,
    create_schema=UserCreate,
    update_schema=UserUpdate,
    response_schema=UserResponse,
    prefix="/users",
    tags=["用户管理"],
)

router = user_api.router

# ❌ 错误：手动定义所有路由
@router.post("/users")
async def create_user(...):
    pass

@router.put("/users/{id}")
async def update_user(...):
    pass
# 问题：重复代码，维护困难
```

### 2. 自定义路由扩展

**原则**：通过 `custom_routes` 参数扩展自定义路由

```python
def register_custom_routes(router: APIRouter, api) -> None:
    """注册自定义路由"""

    @router.post("/activate/{id}")
    async def activate_user(
        id: int,
        db: AsyncSessionDep,
        cache: CacheDep,
    ):
        return await api.service.activate_user(db, cache, id)

    @router.post("/deactivate/{id}")
    async def deactivate_user(
        id: int,
        db: AsyncSessionDep,
        cache: CacheDep,
    ):
        return await api.service.deactivate_user(db, cache, id)


user_api = BaseAPI(
    module_name="admin",
    model=User,
    service=user_service,
    create_schema=UserCreate,
    update_schema=UserUpdate,
    response_schema=UserResponse,
    prefix="/users",
    tags=["用户管理"],
    custom_routes=[register_custom_routes],  # 注册自定义路由
)
```

### 3. 权限控制

**原则**：使用 `enable_permission` 自动生成权限码

```python
# 自动生成的权限码：
# - admin:user:create
# - admin:user:update
# - admin:user:delete
# - admin:user:list
# - admin:user:detail

user_api = BaseAPI(
    module_name="admin",
    model=User,
    service=user_service,
    enable_permission=True,  # 启用权限控制
)
```

## 分层架构最佳实践

### 严格遵守分层规则

```
API 层 → Service 层 → Repository 层 → 数据库
```

**禁止的模式**：

```python
# ❌ 错误：API 层直接访问数据库
from sqlalchemy import select

@router.get("/users")
async def get_users(db: AsyncSessionDep):
    result = await db.execute(select(User))
    return result.scalars().all()

# ❌ 错误：API 层直接访问 Repository
@router.get("/users")
async def get_users(db: AsyncSessionDep):
    return await user_repository.get_list(db)

# ✅ 正确：API 层调用 Service
@router.get("/users")
async def get_users(db: AsyncSessionDep, cache: CacheDep):
    return await user_service.get_list(db, cache)
```

## 代码质量最佳实践

### 1. 类型注解

**原则**：所有函数参数和返回值都要有类型注解

```python
# ✅ 正确：完整的类型注解
async def get_by_username(
    self,
    db: AsyncSession,
    username: str
) -> User | None:
    pass

# ❌ 错误：缺少类型注解
async def get_by_username(self, db, username):
    pass
```

### 2. 文档字符串

**原则**：类和关键方法添加中文文档字符串

```python
class UserService(BaseService[User, UserRepository]):
    """
    用户业务逻辑层

    提供用户管理的业务逻辑，包括：
    - 用户激活/停用
    - 密码管理
    - 权限验证
    """

    async def activate_user(
        self,
        db: AsyncSession,
        cache: CacheService,
        user_id: int
    ) -> User:
        """
        激活用户

        Args:
            db: 数据库会话
            cache: 缓存服务
            user_id: 用户 ID

        Returns:
            User: 激活后的用户对象

        Raises:
            ResourceNotFoundException: 用户不存在
        """
        pass
```

### 3. 错误处理

**原则**：使用自定义异常，提供友好的错误信息

```python
# ✅ 正确：使用自定义异常
from src.core.exceptions import ResourceNotFoundException, AppException

if not user:
    raise ResourceNotFoundException(f"用户 {user_id} 不存在")

if user.is_deleted:
    raise AppException("用户已被删除，无法操作")

# ❌ 错误：使用通用异常
if not user:
    raise Exception("User not found")
```

## 性能优化最佳实践

### 1. 避免 N+1 查询

**原则**：使用 Schema 驱动的自动关系加载

```python
# ✅ 正确：使用 Schema 自动加载
class UserResponse(BaseModel):
    id: int
    username: str
    roles: list[RoleResponse]  # 自动加载

users = await user_service.get_list(
    db,
    cache,
    schema=UserResponse  # 自动加载 roles
)

# ❌ 错误：手动循环加载
users = await user_service.get_list(db, cache)
for user in users:
    user.roles = await role_service.get_by_user_id(db, user.id)
    # 问题：N+1 查询
```

### 2. 使用缓存

**原则**：为频繁查询的数据启用缓存

```python
# Service 层自动缓存
class UserService(BaseService[User, UserRepository]):
    def __init__(self):
        super().__init__(
            user_repository,
            enable_cache=True,  # 启用缓存
            cache_prefix="app:user:detail",
        )

# 权限缓存（5 分钟）
permissions = await permission_service.get_user_permissions(db, user_id)
```

### 3. 批量操作

**原则**：使用批量操作减少数据库往返

```python
# ✅ 正确：批量创建
users = await user_service.bulk_create(db, cache, [
    {"username": "user1", "email": "user1@example.com"},
    {"username": "user2", "email": "user2@example.com"},
])

# ❌ 错误：循环创建
for data in user_data_list:
    await user_service.create(db, cache, data)
    # 问题：多次数据库往返
```

## 测试最佳实践

### 1. 单元测试

```python
import pytest
from src.app.admin.services import user_service

@pytest.mark.asyncio
async def test_create_user(db_session, cache_service):
    """测试创建用户"""
    user_data = {
        "username": "testuser",
        "email": "test@example.com",
        "password": "password123",
    }

    user = await user_service.create(db_session, cache_service, user_data)

    assert user.username == "testuser"
    assert user.email == "test@example.com"
    assert user.hashed_password != "password123"  # 密码已哈希
```

### 2. 集成测试

```python
from fastapi.testclient import TestClient

def test_create_user_api(client: TestClient, auth_headers):
    """测试创建用户 API"""
    response = client.post(
        "/api/v1/users",
        json={
            "username": "testuser",
            "email": "test@example.com",
            "password": "password123",
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "testuser"
```

## 常见陷阱

### 1. 循环导入

**问题**：在模块顶部导入导致循环依赖

**解决**：方法内部懒加载导入

```python
# ❌ 错误
from src.app.warehousing.services import inventory_service

class InboundService(BaseService):
    async def confirm(self, db, id):
        await inventory_service.update_stock(db, id)

# ✅ 正确
class InboundService(BaseService):
    async def confirm(self, db, id):
        from src.app.warehousing.services import inventory_service
        await inventory_service.update_stock(db, id)
```

### 2. 忘记导出

**问题**：在 `__init__.py` 中忘记导出新添加的类

**解决**：创建新类后立即更新 `__init__.py`

```python
# repositories/__init__.py
from .user_repository import UserRepository, user_repository

__all__ = ["UserRepository", "user_repository"]
```

### 3. 破坏分层架构

**问题**：API 层直接访问数据库或 Repository

**解决**：严格遵守 API → Service → Repository 的调用链

```python
# ❌ 错误：API 层直接访问数据库
@router.get("/users")
async def get_users(db: AsyncSessionDep):
    result = await db.execute(select(User))
    return result.scalars().all()

# ✅ 正确：通过 Service 层
@router.get("/users")
async def get_users(db: AsyncSessionDep, cache: CacheDep):
    return await user_service.get_list(db, cache)
```
