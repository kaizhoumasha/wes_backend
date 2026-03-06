# 完整示例：创建仓库模块

本文档演示如何从零开始创建一个完整的仓库（Warehouse）模块。

## 需求分析

**功能需求**：
- 仓库基本信息管理（名称、编码、地址）
- 支持软删除和回收站
- 记录创建人和更新人
- 标准 CRUD 操作

**技术决策**：
- ✅ 平面结构（不需要树形）
- ✅ 软删除（需要回收站）
- ✅ 审计字段（需要记录操作人）
- ❌ 乐观锁（并发不高）
- ❌ 审计日志（非关键业务）

**Mixin 组合**：
```python
DataTableMixin + EnterpriseMixin + SoftDeleteMixin
```

## 步骤 1：定义数据模型

### 1.1 创建模型文件

```bash
mkdir -p src/app/biz/warehouse/models
touch src/app/biz/warehouse/models/__init__.py
touch src/app/biz/warehouse/models/warehouse.py
```

### 1.2 编写模型代码

**文件**：`src/app/biz/warehouse/models/warehouse.py`

```python
"""
仓库模型定义
"""

from datetime import datetime
from typing import Literal

from sqlmodel import Field

from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, SoftDeleteMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class WarehouseBase(BaseMixin):
    """
    仓库基础字段（用于 Schema 复用）
    """

    name: str = Field(
        min_length=2,
        max_length=50,
        description="仓库名称",
        index=True,
    )
    code: str = Field(
        min_length=2,
        max_length=20,
        description="仓库编码",
        index=True,
        unique=True,
    )
    address: str | None = Field(
        default=None,
        max_length=200,
        description="仓库地址",
    )
    contact_person: str | None = Field(
        default=None,
        max_length=50,
        description="联系人",
    )
    contact_phone: str | None = Field(
        default=None,
        max_length=20,
        pattern=r"^1[3-9]\d{9}$",
        description="联系电话",
    )


class Warehouse(WarehouseBase, DataTableMixin, EnterpriseMixin, SoftDeleteMixin, table=True):
    """
    仓库数据库表模型
    """

    __tablename__: Literal["warehouses"] = "warehouses"
    __schema__ = SchemaType.BIZ.value

    # 表特有字段
    capacity: int | None = Field(
        default=None,
        ge=0,
        description="仓库容量（立方米）",
    )
    is_active: bool = Field(
        default=True,
        description="是否启用",
    )


class WarehouseCreate(ModelFactory(WarehouseBase).for_create()):
    """
    仓库创建 Schema（基于 WarehouseBase，所有字段必需）
    """
    # 可以添加额外字段
    capacity: int | None = Field(default=None, ge=0)
    is_active: bool = Field(default=True)


class WarehouseUpdate(ModelFactory(WarehouseBase).for_update()):
    """
    仓库更新 Schema（基于 WarehouseBase，所有字段可选）
    """
    # 可以添加额外字段
    capacity: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class WarehouseResponse(WarehouseBase):
    """
    仓库响应 Schema（基于 WarehouseBase，添加系统字段）
    """

    id: int
    capacity: int | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    created_by: int | None
    updated_by: int | None
    remark: str | None
```

### 1.3 更新模型 __init__.py

**文件**：`src/app/biz/warehouse/models/__init__.py`

```python
from .warehouse import (
    Warehouse,
    WarehouseBase,
    WarehouseCreate,
    WarehouseUpdate,
    WarehouseResponse,
)

__all__ = [
    "Warehouse",
    "WarehouseBase",
    "WarehouseCreate",
    "WarehouseUpdate",
    "WarehouseResponse",
]
```

## 步骤 2：定义 Repository

### 2.1 创建 Repository 文件

```bash
mkdir -p src/app/biz/warehouse/repositories
touch src/app/biz/warehouse/repositories/__init__.py
touch src/app/biz/warehouse/repositories/warehouse_repository.py
```

### 2.2 编写 Repository 代码

**文件**：`src/app/biz/warehouse/repositories/warehouse_repository.py`

```python
"""
仓库 Repository
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.base_repository import BaseRepository
from src.app.biz.warehouse.models import Warehouse


class WarehouseRepository(BaseRepository[Warehouse]):
    """
    仓库数据访问层

    CRITICAL: 必须实现 __init__ 方法并调用 super().__init__(Model)
    否则会报错：TypeError: BaseRepository.__init__() missing 1 required positional argument: 'model'
    """

    def __init__(self):
        super().__init__(Warehouse)  # ✅ 必须调用父类构造函数

    async def get_by_code(
        self,
        db: AsyncSession,
        code: str
    ) -> Warehouse | None:
        """
        根据编码查询仓库

        Args:
            db: 数据库会话
            code: 仓库编码

        Returns:
            Warehouse | None: 仓库对象或 None
        """
        result = await db.execute(
            select(Warehouse).where(Warehouse.code == code)
        )
        return result.scalar_one_or_none()

    async def get_active_warehouses(
        self,
        db: AsyncSession
    ) -> list[Warehouse]:
        """
        获取所有启用的仓库

        Args:
            db: 数据库会话

        Returns:
            list[Warehouse]: 启用的仓库列表
        """
        result = await db.execute(
            select(Warehouse)
            .where(Warehouse.is_active == True)
            .order_by(Warehouse.sort_order, Warehouse.created_at.desc())
        )
        return list(result.scalars().all())


# 创建单例
warehouse_repository = WarehouseRepository()
```

### 2.3 更新 Repository __init__.py

**文件**：`src/app/biz/warehouse/repositories/__init__.py`

```python
from .warehouse_repository import WarehouseRepository, warehouse_repository

__all__ = ["WarehouseRepository", "warehouse_repository"]
```

## 步骤 3：定义 Service

### 3.1 创建 Service 文件

```bash
mkdir -p src/app/biz/warehouse/services
touch src/app/biz/warehouse/services/__init__.py
touch src/app/biz/warehouse/services/warehouse_service.py
```

### 3.2 编写 Service 代码

**文件**：`src/app/biz/warehouse/services/warehouse_service.py`

```python
"""
仓库 Service
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base_service import BaseService
from src.core.cache_service import CacheService
from src.core.exceptions import AppException, ResourceNotFoundException
from src.app.biz.warehouse.models import Warehouse
from src.app.biz.warehouse.repositories import warehouse_repository, WarehouseRepository


class WarehouseService(BaseService[Warehouse, WarehouseRepository]):
    """
    仓库业务逻辑层
    """

    def __init__(self):
        super().__init__(
            warehouse_repository,
            enable_cache=True,
            cache_prefix="app:warehouse:detail",
        )

    async def get_by_code(
        self,
        db: AsyncSession,
        code: str
    ) -> Warehouse | None:
        """
        根据编码查询仓库

        Args:
            db: 数据库会话
            code: 仓库编码

        Returns:
            Warehouse | None: 仓库对象或 None
        """
        return await self.repo.get_by_code(db, code)

    async def get_active_warehouses(
        self,
        db: AsyncSession
    ) -> list[Warehouse]:
        """
        获取所有启用的仓库

        Args:
            db: 数据库会话

        Returns:
            list[Warehouse]: 启用的仓库列表
        """
        return await self.repo.get_active_warehouses(db)

    async def activate_warehouse(
        self,
        db: AsyncSession,
        cache: CacheService,
        warehouse_id: int
    ) -> Warehouse:
        """
        启用仓库

        Args:
            db: 数据库会话
            cache: 缓存服务
            warehouse_id: 仓库 ID

        Returns:
            Warehouse: 启用后的仓库对象

        Raises:
            ResourceNotFoundException: 仓库不存在
        """
        warehouse = await self.get_by_id(db, cache, warehouse_id)
        if not warehouse:
            raise ResourceNotFoundException(f"仓库 {warehouse_id} 不存在")

        if warehouse.is_active:
            raise AppException("仓库已经是启用状态")

        return await self.update(
            db,
            cache,
            warehouse_id,
            {"is_active": True}
        )

    async def deactivate_warehouse(
        self,
        db: AsyncSession,
        cache: CacheService,
        warehouse_id: int
    ) -> Warehouse:
        """
        停用仓库

        Args:
            db: 数据库会话
            cache: 缓存服务
            warehouse_id: 仓库 ID

        Returns:
            Warehouse: 停用后的仓库对象

        Raises:
            ResourceNotFoundException: 仓库不存在
        """
        warehouse = await self.get_by_id(db, cache, warehouse_id)
        if not warehouse:
            raise ResourceNotFoundException(f"仓库 {warehouse_id} 不存在")

        if not warehouse.is_active:
            raise AppException("仓库已经是停用状态")

        return await self.update(
            db,
            cache,
            warehouse_id,
            {"is_active": False}
        )


# 创建单例
warehouse_service = WarehouseService()
```

### 3.3 更新 Service __init__.py

**文件**：`src/app/biz/warehouse/services/__init__.py`

```python
from .warehouse_service import WarehouseService, warehouse_service

__all__ = ["WarehouseService", "warehouse_service"]
```

## 步骤 4：定义 API

### 4.1 创建 API 文件

```bash
mkdir -p src/app/biz/warehouse/v1
touch src/app/biz/warehouse/v1/__init__.py
touch src/app/biz/warehouse/v1/warehouse.py
```

### 4.2 编写 API 代码

**文件**：`src/app/biz/warehouse/v1/warehouse.py`

```python
"""
仓库 API 路由
"""

from fastapi import APIRouter

from src.core.base_api import BaseAPI
from src.database.dependencies import AsyncSessionDep, CacheDep
from src.app.biz.warehouse.models import (
    Warehouse,
    WarehouseCreate,
    WarehouseUpdate,
    WarehouseResponse,
)
from src.app.biz.warehouse.services import warehouse_service


def register_custom_routes(router: APIRouter, api) -> None:
    """注册自定义路由"""

    @router.get("/active", response_model=list[WarehouseResponse])
    async def get_active_warehouses(
        db: AsyncSessionDep,
    ):
        """获取所有启用的仓库"""
        return await api.service.get_active_warehouses(db)

    @router.post("/{id}/activate", response_model=WarehouseResponse)
    async def activate_warehouse(
        id: int,
        db: AsyncSessionDep,
        cache: CacheDep,
    ):
        """启用仓库"""
        return await api.service.activate_warehouse(db, cache, id)

    @router.post("/{id}/deactivate", response_model=WarehouseResponse)
    async def deactivate_warehouse(
        id: int,
        db: AsyncSessionDep,
        cache: CacheDep,
    ):
        """停用仓库"""
        return await api.service.deactivate_warehouse(db, cache, id)


warehouse_api = BaseAPI(
    module_name="biz",
    model=Warehouse,
    service=warehouse_service,
    create_schema=WarehouseCreate,
    update_schema=WarehouseUpdate,
    response_schema=WarehouseResponse,
    prefix="/warehouses",
    tags=["仓库管理"],
    gen_create=True,
    gen_update=True,
    gen_delete=True,
    gen_bulk_delete=False,
    enable_permission=True,
    custom_routes=[register_custom_routes],
)

router = warehouse_api.router
```

## 步骤 5：注册路由

### 5.1 更新 src/register.py

```python
# 在 register_routes 函数中添加
from src.app.biz.warehouse.v1.warehouse import router as warehouse_router

def register_routes(app: FastAPI) -> None:
    # ... 其他路由 ...

    # 仓库管理
    app.include_router(warehouse_router, prefix="/api/v1")
```

## 步骤 6：生成数据库迁移

```bash
# 生成迁移脚本
./scripts/generate_migration.sh "Add warehouse module"

# 查看生成的迁移文件
cat migrations/versions/xxx_add_warehouse_module.py

# 执行迁移
./scripts/migrate.sh upgrade
```

## 步骤 7：代码质量检查

```bash
# 格式化代码
ruff format src/app/biz/warehouse/

# 检查代码
ruff check src/app/biz/warehouse/

# 类型检查
mypy src/app/biz/warehouse/
```

## 步骤 8：测试

### 8.1 创建测试文件

```bash
mkdir -p tests/biz
touch tests/biz/test_warehouse.py
```

### 8.2 编写测试代码

**文件**：`tests/biz/test_warehouse.py`

```python
"""
仓库模块测试
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.biz.warehouse.services import warehouse_service
from src.core.cache_service import CacheService


@pytest.mark.asyncio
async def test_create_warehouse(db_session: AsyncSession, cache_service: CacheService):
    """测试创建仓库"""
    warehouse_data = {
        "name": "测试仓库",
        "code": "WH001",
        "address": "测试地址",
        "contact_person": "张三",
        "contact_phone": "13800138000",
        "capacity": 1000,
    }

    warehouse = await warehouse_service.create(db_session, cache_service, warehouse_data)

    assert warehouse.name == "测试仓库"
    assert warehouse.code == "WH001"
    assert warehouse.is_active is True


@pytest.mark.asyncio
async def test_get_by_code(db_session: AsyncSession, cache_service: CacheService):
    """测试根据编码查询"""
    # 创建仓库
    warehouse_data = {
        "name": "测试仓库",
        "code": "WH002",
    }
    await warehouse_service.create(db_session, cache_service, warehouse_data)

    # 查询
    warehouse = await warehouse_service.get_by_code(db_session, "WH002")

    assert warehouse is not None
    assert warehouse.code == "WH002"


@pytest.mark.asyncio
async def test_activate_deactivate(db_session: AsyncSession, cache_service: CacheService):
    """测试启用/停用仓库"""
    # 创建仓库
    warehouse_data = {
        "name": "测试仓库",
        "code": "WH003",
    }
    warehouse = await warehouse_service.create(db_session, cache_service, warehouse_data)

    # 停用
    warehouse = await warehouse_service.deactivate_warehouse(
        db_session,
        cache_service,
        warehouse.id
    )
    assert warehouse.is_active is False

    # 启用
    warehouse = await warehouse_service.activate_warehouse(
        db_session,
        cache_service,
        warehouse.id
    )
    assert warehouse.is_active is True
```

### 8.3 运行测试

```bash
# 运行测试
pytest tests/biz/test_warehouse.py -v

# 生成覆盖率报告
pytest tests/biz/test_warehouse.py --cov=src/app/biz/warehouse --cov-report=html
```

## 步骤 9：API 测试

### 9.1 启动服务

```bash
uvicorn main:app --reload
```

### 9.2 测试 API

```bash
# 创建仓库
curl -X POST "http://localhost:8000/api/v1/warehouses" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "name": "主仓库",
    "code": "WH001",
    "address": "北京市朝阳区",
    "contact_person": "张三",
    "contact_phone": "13800138000",
    "capacity": 1000
  }'

# 查询仓库列表
curl -X POST "http://localhost:8000/api/v1/warehouses/query" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "limit": 10,
    "offset": 0
  }'

# 获取单个仓库
curl -X GET "http://localhost:8000/api/v1/warehouses/1" \
  -H "Authorization: Bearer YOUR_TOKEN"

# 更新仓库
curl -X PUT "http://localhost:8000/api/v1/warehouses/1" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "name": "主仓库（更新）",
    "capacity": 2000
  }'

# 启用仓库
curl -X POST "http://localhost:8000/api/v1/warehouses/1/activate" \
  -H "Authorization: Bearer YOUR_TOKEN"

# 停用仓库
curl -X POST "http://localhost:8000/api/v1/warehouses/1/deactivate" \
  -H "Authorization: Bearer YOUR_TOKEN"

# 软删除仓库
curl -X DELETE "http://localhost:8000/api/v1/warehouses/1" \
  -H "Authorization: Bearer YOUR_TOKEN"

# 恢复仓库
curl -X POST "http://localhost:8000/api/v1/warehouses/1/restore" \
  -H "Authorization: Bearer YOUR_TOKEN"

# 查看回收站
curl -X GET "http://localhost:8000/api/v1/warehouses/trash" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

## 生成的 API 路由

BaseAPI 自动生成以下路由：

| 方法 | 路径 | 说明 | 权限码 |
|------|------|------|--------|
| POST | /warehouses | 创建仓库 | biz:warehouse:create |
| PUT | /warehouses/{id} | 更新仓库 | biz:warehouse:update |
| DELETE | /warehouses/{id} | 软删除仓库 | biz:warehouse:delete |
| GET | /warehouses/{id} | 获取单个仓库 | biz:warehouse:detail |
| POST | /warehouses/query | 查询仓库列表 | biz:warehouse:list |
| POST | /warehouses/{id}/restore | 恢复仓库 | biz:warehouse:restore |
| GET | /warehouses/trash | 回收站 | biz:warehouse:trash |
| POST | /warehouses/trash/restore | 批量恢复 | biz:warehouse:restore |
| DELETE | /warehouses/trash/permanent | 批量永久删除 | biz:warehouse:delete |

自定义路由：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /warehouses/active | 获取启用的仓库 |
| POST | /warehouses/{id}/activate | 启用仓库 |
| POST | /warehouses/{id}/deactivate | 停用仓库 |

## 目录结构

```
src/app/biz/warehouse/
├── __init__.py
├── models/
│   ├── __init__.py
│   └── warehouse.py
├── repositories/
│   ├── __init__.py
│   └── warehouse_repository.py
├── services/
│   ├── __init__.py
│   └── warehouse_service.py
└── v1/
    ├── __init__.py
    └── warehouse.py

tests/biz/
└── test_warehouse.py
```

## 总结

通过以上 9 个步骤，我们完成了一个完整的仓库模块：

1. ✅ 定义数据模型（Base + Table + Schema）
2. ✅ 定义 Repository（继承 BaseRepository）
3. ✅ 定义 Service（继承 BaseService）
4. ✅ 定义 API（继承 BaseAPI）
5. ✅ 注册路由
6. ✅ 生成数据库迁移
7. ✅ 代码质量检查
8. ✅ 编写测试
9. ✅ API 测试

**关键特性**：
- 零代码 CRUD（继承 BaseAPI）
- 软删除和回收站（SoftDeleteMixin）
- 审计字段（EnterpriseMixin）
- 自定义业务方法（activate/deactivate）
- 自定义路由（/active, /activate, /deactivate）
- 完整的测试覆盖

**代码行数**：
- 模型：~100 行
- Repository：~50 行
- Service：~100 行
- API：~60 行
- 测试：~80 行
- **总计**：~390 行

**开发时间**：
- 模型定义：10 分钟
- Repository：5 分钟
- Service：15 分钟
- API：10 分钟
- 测试：20 分钟
- **总计**：~60 分钟

## 常见错误和解决方案

### 错误 1：Repository 初始化失败

**症状**：
```
TypeError: BaseRepository.__init__() missing 1 required positional argument: 'model'
```

**原因**：Repository 类缺少 `__init__` 方法或未调用父类构造函数。

**解决方案**：
```python
class WarehouseRepository(BaseRepository[Warehouse]):
    def __init__(self):
        super().__init__(Warehouse)  # ✅ 必须调用父类构造函数
```

### 错误 2：Alembic 未检测到新表

**症状**：
```bash
alembic revision --autogenerate -m "Add warehouse"
# 输出：No changes detected
```

**原因**：`migrations/env.py` 中未导入新模型。

**解决方案**：
```python
# migrations/env.py
from src.app.biz.warehouse.models import Warehouse  # noqa: F401

target_metadata = Base.metadata
```

### 错误 3：Service 方法调用缺少参数

**症状**：
```
TypeError: BaseService.get_by_id() missing 1 required positional argument: 'cache'
```

**原因**：调用 Service 方法时未传递 `cache` 参数。

**解决方案**：
```python
# ❌ 错误
warehouse = await warehouse_service.get_by_id(db, warehouse_id)

# ✅ 正确
warehouse = await warehouse_service.get_by_id(db, cache, warehouse_id)
```

### 错误 4：模块导入失败

**症状**：
```
ImportError: cannot import name 'warehouse_service' from 'src.app.biz.warehouse.services'
```

**原因**：`services/__init__.py` 中未导出 `warehouse_service` 实例。

**解决方案**：
```python
# services/__init__.py
from .warehouse_service import WarehouseService, warehouse_service

__all__ = ["WarehouseService", "warehouse_service"]  # ✅ 导出类和实例
```

### 错误 5：路由 404 Not Found

**症状**：
```bash
curl http://localhost:8000/api/v1/warehouses
# 404 Not Found
```

**原因**：`src/register.py` 中未注册路由。

**解决方案**：
```python
# src/register.py
from src.app.biz.warehouse.v1.warehouse import router as warehouse_router

def register_routes(app: FastAPI) -> None:
    app.include_router(warehouse_router, prefix="/api/v1")  # ✅ 注册路由
```

### 错误 6：乐观锁更新失败

**症状**：
```
OptimisticLockException: 更新失败，数据可能已被其他用户修改
```

**原因**：更新时未包含 `version` 字段（如果模型使用了 `OptimisticLockMixin`）。

**解决方案**：
```python
# 获取当前对象
warehouse = await warehouse_service.get_by_id(db, cache, warehouse_id)

# 更新时包含 version 字段
await warehouse_service.update(db, cache, warehouse_id, {
    "name": "新名称",
    "version": warehouse.version  # ✅ 包含 version 字段
})
```

## 最佳实践总结

1. **Repository 必须实现 `__init__`**：调用 `super().__init__(Model)`
2. **Alembic 必须导入模型**：在 `migrations/env.py` 中添加导入
3. **Service 方法必须传递 cache**：即使为 None 也要传递
4. **模块必须完整导出**：在 `__init__.py` 中导出类和实例
5. **路由必须注册**：在 `src/register.py` 中注册路由
6. **乐观锁必须传递 version**：更新时包含 version 字段

## 参考资料

- **故障排查指南**：`references/troubleshooting.md`
- **验证清单**：`references/checklist.md`
- **最佳实践**：`references/best-practices.md`
- **Mixin 指南**：`references/mixin-guide.md`
- **Hook 系统**：`references/hook-system.md`
