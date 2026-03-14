---
purpose: 常见任务指南
indexed_by: CLAUDE.md
tags: [howto, module, hook, tree, migration]
---

# 常见任务指南

> 详见 CLAUDE.md 的任务索引，本文档提供详细的操作步骤。

## 创建新模块

### 目录结构

```
src/app/{module}/
├── models/           # 数据模型和 Pydantic Schema
├── repositories/     # 数据访问层
├── services/         # 业务逻辑层
└── v1/              # API 路由层
```

### 完整示例

```python
# ===== models/warehouse.py =====
from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, SoftDeleteMixin
from src.database.model_factory import ModelFactory

class WarehouseBase(BaseMixin):
    """仓库基础字段 - 用于 Schema 复用"""
    name: str
    code: str
    location: str | None = None

class Warehouse(WarehouseBase, DataTableMixin, EnterpriseMixin, SoftDeleteMixin, table=True):
    """仓库数据库表模型"""
    __tablename__ = "warehouses"
    capacity: int | None = None

class WarehouseCreate(ModelFactory(WarehouseBase).for_create()):
    pass

class WarehouseUpdate(ModelFactory(WarehouseBase).for_update()):
    pass

class WarehouseResponse(WarehouseBase):
    id: int
    capacity: int | None
    created_at: datetime
    updated_at: datetime

# ===== repositories/warehouse_repository.py =====
class WarehouseRepository(BaseRepository[Warehouse]):
    pass

warehouse_repository = WarehouseRepository()

# ===== services/warehouse_service.py =====
class WarehouseService(BaseService[Warehouse, WarehouseRepository]):
    def __init__(self):
        super().__init__(warehouse_repository, enable_cache=True)

warehouse_service = WarehouseService()

# ===== v1/warehouse.py =====
from src.core.base_api import BaseAPI

warehouse_api = BaseAPI(
    module_name="biz",
    model=Warehouse,
    service=warehouse_service,
    create_schema=WarehouseCreate,
    update_schema=WarehouseUpdate,
    response_schema=WarehouseResponse,
    prefix="/warehouses",
    tags=["仓库管理"],
)

router = warehouse_api.router
```

### 注册路由

在 `src/register.py` 中添加：
```python
from src.app.warehouse.v1.warehouse import router as warehouse_router
app.include_router(warehouse_router, prefix="/api/v1")
```

## 添加自定义业务逻辑

### 方式1：在 Service 中添加新方法

```python
class UserService(BaseService[User, UserRepository]):
    async def get_by_username(self, db, username: str):
        return await self.repo.get_by_field(db, "username", username)
```

### 方式2：使用 Hook 拦截 CRUD 操作

```python
class UserService(BaseService[User, UserRepository]):
    def __init__(self):
        super().__init__(UserRepository())
        self.add_hook(HookType.BEFORE_CREATE, self._custom_logic)

    async def _custom_logic(self, context: HookContext) -> None:
        # 自定义逻辑
        pass
```

## 添加状态验证

```python
from src.database.status_mixins import DocumentStatusMixin
from src.database.document_status import DocStatus

class Inbound(DocumentStatusMixin, DataTableMixin, table=True):
    doc_status: str = Field(default=DocStatus.DRAFT)
    # 自动获得 validate_document_status() 方法

# 使用示例
inbound = await service.create(db, {"doc_status": "draft", ...})
await service.update(db, inbound.id, {"quantity": 100})  # ✅ 成功

await service.update(db, inbound.id, {"doc_status": "confirmed"})
await service.update(db, inbound.id, {"quantity": 200})  # ❌ ValueError
```

## 树形结构处理

```python
from src.core.mixins import TreeMixin, DataTableMixin
from src.database.tree_repository import TreeRepository
from src.core.tree_api import TreeAPI

class Category(TreeMixin, DataTableMixin, table=True):
    name: str

class CategoryRepository(TreeRepository[Category]):
    pass

class CategoryService(TreeServiceMixin, BaseService[Category, CategoryRepository]):
    pass

category_api = TreeAPI(
    module_name="biz",
    model=Category,
    service=category_service,
    # ... 其他参数
)

# 自动获得树形 API：
# - GET /categories/tree - 获取树形结构
# - GET /categories/siblings/{node_id} - 获取同级节点
# - GET /categories/ancestors/{node_id} - 获取祖先路径
# - PUT /categories/move - 移动节点
```

## 查询构建

```python
from src.core.query_models import FilterGroup, FilterCondition, FilterOperator

# 等于查询
filters = FilterGroup(conditions=[
    FilterCondition(field="username", operator=FilterOperator.EQ, value="admin")
])

# 模糊查询
filters = FilterGroup(conditions=[
    FilterCondition(field="email", operator=FilterOperator.ILIKE, value="example.com")
])

# OR 逻辑
filters = FilterGroup(
    logic="OR",
    conditions=[
        FilterCondition(field="username", operator=FilterOperator.EQ, value="admin"),
        FilterCondition(field="email", operator=FilterOperator.EQ, value="admin@example.com"),
    ]
)

total, users = await repo.get_list(db, limit=10, offset=0, filters=filters)
```

## 数据库迁移

```bash
# 生成迁移脚本
./scripts/generate_migration.sh "描述"

# 升级到最新版本
./scripts/migrate.sh upgrade

# 降级一个版本
./scripts/migrate.sh downgrade

# 查看当前版本
./scripts/migrate.sh current
```