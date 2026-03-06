---
name: wes-module-creator
description: WES Backend 功能模块创建工作流。用于创建新的业务模块时，自动生成符合项目架构规范的代码（Models、Repository、Service、API）。支持平面结构和树形结构两种模式。使用场景：(1) 创建新的业务模块，(2) 需要遵循项目分层架构，(3) 需要自动生成 CRUD 代码，(4) 需要选择合适的 Mixin 组合，(5) 需要符合 RUFF 和 CLAUDE.md 规范。
---

# WES Backend 模块创建工作流

本技能提供 WES Backend 项目的标准化模块创建流程，包括指导文档和自动化代码生成。

## 核心工作流程

创建新功能模块遵循 4 步流程：

1. **定义数据模型** - 选择合适的 Mixin，考虑是否需要树形结构
2. **定义 Repository** - 继承 BaseRepository 或 TreeRepository
3. **定义 Service** - 继承 BaseService 或 TreeServiceMixin
4. **定义 API** - 继承 BaseAPI 或 TreeAPI

## 使用方式

### 交互式创建

```bash
# 运行交互式脚本
python wes-module-creator-skill/scripts/generate_module.py
```

脚本会询问：
- 模块名称（如：warehouse, inventory）
- 是否需要树形结构
- 需要的 Mixin 组合
- 是否需要软删除
- 是否需要审计日志
- 自定义字段定义

### 快速创建（命令行参数）

```bash
# 创建平面结构模块
python scripts/generate_module.py --name warehouse --flat

# 创建树形结构模块
python scripts/generate_module.py --name category --tree

# 指定 Mixin
python scripts/generate_module.py --name product \
  --mixins DataTableMixin,EnterpriseMixin,SoftDeleteMixin
```

## 模块结构模式

### 平面结构模块

适用场景：用户、角色、仓库、订单等独立实体

**Mixin 组合**：
- `DataTableMixin` - 标准表字段（id, created_at, updated_at）
- `EnterpriseMixin` - 企业字段（created_by, updated_by, remark）
- `SoftDeleteMixin` - 软删除（is_deleted, deleted_at, deleted_by）

**参考示例**：
- 用户模块：`src/app/admin/models/user.py`
- 角色模块：`src/app/admin/repositories/role_repository.py`

### 树形结构模块

适用场景：菜单、分类、组织架构等层级关系

**Mixin 组合**：
- `TreeMixin` - 树形字段（parent_id, tree_path, level, sort_order）
- `DataTableMixin` - 标准表字段
- `SoftDeleteMixin` - 软删除

**参考示例**：
- 菜单模块：`src/app/admin/models/menu.py`
- 菜单 Repository：`src/app/admin/repositories/menu_repository.py`

## 关键设计原则

### 模型定义模式

```python
# 1. 定义 Base（纯业务字段）
class WarehouseBase(BaseMixin):
    name: str = Field(max_length=50)
    code: str = Field(max_length=20)

# 2. 定义 Table（Base + Mixins + 表特有字段）
class Warehouse(WarehouseBase, DataTableMixin, EnterpriseMixin, table=True):
    __tablename__ = "warehouses"
    capacity: int | None = None

# 3. 使用 ModelFactory 生成 Schema
class WarehouseCreate(ModelFactory(WarehouseBase).for_create()):
    pass

class WarehouseUpdate(ModelFactory(WarehouseBase).for_update()):
    pass
```

### 分层架构规则（CRITICAL）

```
API 层 → Service 层 → Repository 层 → 数据库
```

**严格禁止**：
- ❌ API 层直接访问 Repository
- ❌ API 层直接执行 SQL
- ❌ 跨层访问

### 命名约定

- 数据库表：复数（`users`, `warehouses`）
- 模型类：单数（`User`, `Warehouse`）
- Base 模型：`{Model}Base`
- Repository：`{Model}Repository`
- Service：`{Model}Service`
- API 路由：复数（`/users`, `/warehouses`）

## 代码质量要求

生成的代码必须符合：

1. **RUFF 规范** - 代码格式化和 lint 检查
2. **CLAUDE.md 架构规则** - 分层架构、DRY、SOLID 原则
3. **类型注解** - 所有函数参数和返回值
4. **Pydantic 验证** - 字段长度、格式验证
5. **文档字符串** - 类和关键方法的中文文档

## 详细参考

- **最佳实践**：查看 `references/best-practices.md`
- **Mixin 选择指南**：查看 `references/mixin-guide.md`
- **Hook 系统**：查看 `references/hook-system.md`
- **状态验证**：查看 `references/status-validation.md`

## 生成后的验证

```bash
# 1. 代码格式检查
ruff format src/app/{module}/
ruff check src/app/{module}/

# 2. 类型检查
mypy src/app/{module}/

# 3. 运行测试
pytest tests/test_{module}.py

# 4. 数据库迁移
./scripts/generate_migration.sh "Add {module} module"
./scripts/migrate.sh upgrade
```

## 常见问题

### 何时使用树形结构？

- ✅ 需要父子关系（菜单、分类、组织）
- ✅ 需要层级查询（获取所有子节点）
- ✅ 需要路径查询（获取祖先路径）
- ❌ 简单的一对多关系（使用外键）

### 如何选择 Mixin？

- `DataTableMixin` - 所有表必需
- `EnterpriseMixin` - 需要记录创建人/更新人
- `SoftDeleteMixin` - 需要软删除和回收站
- `TreeMixin` - 需要树形结构
- `OptimisticLockMixin` - 需要并发控制

### 如何添加自定义业务逻辑？

1. **Hook 系统** - 在 Repository 中添加 Hook
2. **Service 方法** - 在 Service 中添加业务方法
3. **自定义路由** - 通过 `custom_routes` 参数扩展 API

## 常见陷阱（Common Pitfalls）

### 🔴 CRITICAL：Repository 初始化错误

**错误**：
```python
class WarehouseRepository(BaseRepository[Warehouse]):
    pass  # ❌ 缺少 __init__ 方法
```

**症状**：
```
TypeError: BaseRepository.__init__() missing 1 required positional argument: 'model'
```

**正确**：
```python
class WarehouseRepository(BaseRepository[Warehouse]):
    def __init__(self):
        super().__init__(Warehouse)  # ✅ 必须调用父类构造函数
```

### 🔴 CRITICAL：Relationship 类型注解错误

**错误**：
```python
class Device(DataTableMixin, table=True):
    work_line: "WorkLine | None" = Relationship(...)  # ❌ Union 类型
```

**症状**：
```
sqlalchemy.exc.InvalidRequestError: When initializing mapper Mapper[Device(devices)],
expression 'WorkLine | None' failed to locate a name ('None').
```

**正确**：
```python
class Device(DataTableMixin, table=True):
    work_line: "WorkLine" = Relationship(...)  # ✅ 不使用 Union 类型
```

### 🟡 IMPORTANT：Service 方法缺少 cache 参数

**错误**：
```python
# 调用 Service 方法时缺少 cache 参数
warehouse = await warehouse_service.get_by_id(db, warehouse_id)  # ❌
```

**症状**：
```
TypeError: BaseService.get_by_id() missing 1 required positional argument: 'cache'
```

**正确**：
```python
# 传递 cache 参数（即使为 None）
warehouse = await warehouse_service.get_by_id(db, None, warehouse_id)  # ✅
# 或使用依赖注入
warehouse = await warehouse_service.get_by_id(db, cache, warehouse_id)  # ✅
```

### 🟡 IMPORTANT：Alembic 未检测到新模型

**错误**：
```python
# migrations/env.py 中未导入新模型
target_metadata = Base.metadata  # ❌ 模型未导入
```

**症状**：
```bash
alembic revision --autogenerate -m "Add new tables"
# 输出：No changes detected
```

**正确**：
```python
# migrations/env.py 中添加导入
from src.app.workline.models import WorkLine  # noqa: F401
from src.app.device.models import Device  # noqa: F401

target_metadata = Base.metadata  # ✅ 模型已导入
```

### 🟡 IMPORTANT：乐观锁更新缺少 version 字段

**错误**：
```python
# 更新时未包含 version 字段
await service.update(db, cache, id, {"name": "New Name"})  # ❌
```

**症状**：
```
OptimisticLockException: 更新失败，数据可能已被其他用户修改
```

**正确**：
```python
# 更新时包含 version 字段
obj = await service.get_by_id(db, cache, id)
await service.update(db, cache, id, {
    "name": "New Name",
    "version": obj.version  # ✅ 包含 version 字段
})
```

### 🟢 RECOMMENDED：模块导出不完整

**错误**：
```python
# services/__init__.py 中未导出新 Service
from .warehouse_service import WarehouseService

__all__ = ["WarehouseService"]  # ❌ 未导出实例
```

**症状**：
```
ImportError: cannot import name 'warehouse_service' from 'src.app.biz.warehouse.services'
```

**正确**：
```python
# 同时导出类和实例
from .warehouse_service import WarehouseService, warehouse_service

__all__ = ["WarehouseService", "warehouse_service"]  # ✅ 导出类和实例
```

### 🟢 RECOMMENDED：路由未注册

**错误**：
```python
# src/register.py 中未注册新路由
def register_routes(app: FastAPI) -> None:
    # ... 其他路由 ...
    pass  # ❌ 未注册 warehouse_router
```

**症状**：
```bash
curl http://localhost:8000/api/v1/warehouses
# 404 Not Found
```

**正确**：
```python
from src.app.biz.warehouse.v1.warehouse import router as warehouse_router

def register_routes(app: FastAPI) -> None:
    # ... 其他路由 ...
    app.include_router(warehouse_router, prefix="/api/v1")  # ✅ 注册路由
```

## 故障排查指南

遇到问题时，请查看 `references/troubleshooting.md` 获取详细的错误诊断和解决方案。

## 验证清单

创建模块后，请使用 `references/checklist.md` 中的清单验证所有功能是否正常。

## 示例：完整的模块创建

查看 `references/complete-example.md` 了解从零开始创建一个完整模块的详细步骤。
