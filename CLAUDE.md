# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

P9 WES Backend 是一个基于 **FastAPI + SQLModel + SQLAlchemy 2.0 + Pydantic** 的快速开发框架，专为 WMS/WES 系统设计。项目采用**分层架构**和**零代码开发模式**，通过 BaseAPI/BaseService/BaseRepository 三层基类实现快速 CRUD 开发。

**核心特性**：

- **容器化基础设施**：Postgres(TimescaleDB) [container_name: wes_postgres]+ Redis [container_name: wes_redis] 由 Docker 容器化管理
- **零代码 CRUD**：继承 BaseAPI 自动生成标准 REST API
- **ModelFactory 动态 Schema**：自动生成 Create/Update Schema，避免重复代码
- **Hook 系统**：在 Repository 层实现业务逻辑扩展（状态验证、审计字段、乐观锁）
- **自动关系加载**：根据 Response Schema 自动推断并加载 SQLAlchemy 关联对象
- **Mixin 组合模式**：通过 Mixin 复用模型字段和行为（DRY 原则）
- **软删除支持**：SoftDeleteMixin 提供完整的软删除和回收站功能
- **RBAC 权限控制**：基于角色的访问控制，支持权限缓存
- **PostgreSQL Schema 支持**：多 Schema 隔离（wes_sys、wes_biz）

## 开发命令

### 环境管理

```bash
# 启动基础设施 (TimescaleDB + Redis)
docker-compose up -d

# 安装依赖
uv sync

# 运行数据库迁移
./scripts/migrate.sh upgrade

# 启动开发服务器
uvicorn main:app --reload
```

### 代码质量

```bash
# 代码格式化和检查
ruff format .
ruff check .

# 运行所有测试
pytest

# 运行单个测试文件
pytest tests/test_relation_metadata.py

# 运行单个测试方法
pytest tests/test_relation_metadata.py::TestRelationMetadata::test_get_relation_info_one_to_many

# 生成覆盖率报告
pytest --cov=src --cov-report=html:reports/coverage --cov-report=term-missing
```

### 性能测试

```bash
# 运行 Locust Web UI (http://localhost:8089)
./scripts/run_performance_test.sh locust-ui

# 运行压力测试
./scripts/run_performance_test.sh ab 1000 10

# 查看性能指标
./scripts/run_performance_test.sh metrics
```

## 核心架构

### 分层架构

```
API 层 (BaseAPI)
    ↓ 依赖注入
Service 层 (BaseService)
    ↓ 协调
Repository 层 (BaseRepository)
    ↓ 操作
数据库 (SQLModel/SQLAlchemy)
```

**每层职责**：

- **API 层**：路由定义、请求验证、响应转换（零代码生成）
- **Service 层**：业务逻辑协调、缓存管理、事务控制
- **Repository 层**：数据访问、CRUD 操作、关系加载、Hook 执行

### 零代码开发模式

通过继承基类自动获得完整 CRUD 能力：

```python
# 1. 定义基础字段（UserBase - 业务字段）
from src.core.mixins import BaseMixin

class UserBase(BaseMixin):
    """用户基础字段 - 用于 Schema 复用"""
    username: str
    email: str
    full_name: str | None = None

# 2. 定义数据库表模型（User - 继承 UserBase + Mixins）
class User(UserBase, DataTableMixin, EnterpriseMixin, SoftDeleteMixin, table=True):
    """用户数据库表模型"""
    __tablename__ = "users"
    # 表特有字段（不在 Base 中）
    hashed_password: str
    is_superuser: bool = False

# 3. 使用 ModelFactory 自动生成 Create/Update Schema
from src.database.model_factory import ModelFactory

class UserCreate(ModelFactory(UserBase).for_create()):
    """创建 Schema（基于 UserBase，所有字段必需）"""
    password: str  # 可以添加额外字段（如密码）

class UserUpdate(ModelFactory(UserBase).for_update()):
    """更新 Schema（基于 UserBase，所有字段可选）"""
    pass  # 自动生成，无需手动定义

# 4. 定义 Response Schema
class UserResponse(UserBase):
    """响应 Schema（基于 UserBase，添加系统字段）"""
    id: int
    is_superuser: bool
    created_at: datetime
    roles: list[RoleResponse] = []  # 关联对象

# 5. 定义 Repository（继承 BaseRepository）
class UserRepository(BaseRepository[User]):
    pass  # 自动获得完整 CRUD 能力

# 6. 定义 Service（继承 BaseService）
class UserService(BaseService[User, UserRepository]):
    def __init__(self):
        super().__init__(UserRepository(), enable_cache=True)

# 7. 定义 API（继承 BaseAPI，零代码）
from src.core.base_api import BaseAPI

user_api = BaseAPI(
    module_name="admin",
    model=User,
    service=UserService(),
    create_schema=UserCreate,
    update_schema=UserUpdate,
    response_schema=UserResponse,
    prefix="/users",
    tags=["用户管理"],
)
```

**模型设计模式**：
- **UserBase**：纯业务字段，用于 Schema 复用（不含 Mixins 字段）
- **User**：继承 UserBase + Mixins，添加表特有字段
- **UserCreate**：`ModelFactory(UserBase).for_create()` 自动生成
- **UserUpdate**：`ModelFactory(UserBase).for_update()` 自动生成
- **UserResponse**：继承 UserBase，添加系统字段和关联对象

**ModelFactory 的优势**：
- **自动排除系统字段**：id, created_at, updated_at 自动排除
- **智能处理可选性**：Create 时字段必需，Update 时字段可选
- **保留验证器**：继承基础模型的所有 Pydantic 验证器
- **单例缓存**：相同的 Schema 只生成一次，提高性能
- **DRY 原则**：避免为每个模型手动编写重复的 Schema

**自动生成的路由**：

- `POST /users` - 创建（自动权限：`admin:user:create`）
- `PUT /users/{id}` - 更新（`admin:user:update`）
- `DELETE /users/{id}` - 删除（`admin:user:delete`）
- `GET /users/{id}` - 获取单个（`admin:user:detail`）
- `POST /users/query` - 列表查询（`admin:user:list`）
- `POST /users/{id}/restore` - 恢复（软删除专用）
- `GET /users/trash` - 回收站（软删除专用）

### Hook 系统

Hook 系统允许在 Repository 的 CRUD 操作前后插入自定义逻辑，无需修改基类代码。

**Hook 类型**：

```python
from src.database.base_repository import HookType

HookType.BEFORE_CREATE  # 创建前
HookType.AFTER_CREATE   # 创建后
HookType.BEFORE_UPDATE  # 更新前
HookType.AFTER_UPDATE   # 更新后
HookType.BEFORE_DELETE  # 删除前
HookType.AFTER_DELETE   # 删除后
```

**自动注册的 Hook**（无需手动添加）：

1. **状态验证 Hook**：检测 `validate_xxx_status()` 方法并自动注册
2. **审计字段 Hook**：检测 `created_by/updated_by` 字段并自动填充
3. **乐观锁 Hook**：检测 `version` 字段并自动验证和递增
4. **审计日志 Hook**：检测 `AuditableMixin` 并自动记录操作历史

**自定义 Hook 示例**：

```python
class UserService(BaseService[User, UserRepository]):
    def __init__(self):
        super().__init__(UserRepository())
        # 添加自定义 Hook
        self.add_hook(
            HookType.BEFORE_CREATE,
            self._hash_password,
            priority=0,  # 优先级（数字越小越先执行）
        )

    async def _hash_password(self, context: HookContext) -> None:
        """创建前自动哈希密码"""
        data = context.params.get("data", {})
        if "password" in data:
            data["password"] = hash_password(data["password"])
```

### Mixin 系统

Mixin 提供可复用的模型字段和行为，遵循**组合优于继承**原则。

**常用 Mixin**：

```python
from src.core.mixins import (
    DataTableMixin,        # 标准表字段（id, created_at, updated_at）
    EnterpriseMixin,       # 企业字段（created_by, updated_by, remark）
    SoftDeleteMixin,       # 软删除字段（is_deleted, deleted_at, deleted_by）
    TreeMixin,            # 树形结构字段（parent_id, tree_path, level, sort_order）
    OptimisticLockMixin,  # 乐观锁字段（version）
    AuditableMixin,       # 审计日志能力（不是字段，是行为）
)
```

**组合模式**：

```python
# 标准业务表
class Warehouse(DataTableMixin, EnterpriseMixin, SoftDeleteMixin, table=True):
    name: str

# 树形结构表
class Category(TreeMixin, DataTableMixin, SoftDeleteMixin, table=True):
    name: str

# 需要审计的表
class Order(DataTableMixin, EnterpriseMixin, AuditableMixin, table=True):
    order_no: str
```

### 状态验证系统

项目提供完整的**状态验证 Mixin**，专为 WMS/WES 业务场景设计，自动验证单据、货架、容器等状态。

**可用的状态 Mixin**：

```python
from src.database.status_mixins import (
    DocumentStatusMixin,   # 单据状态（入库单、出库单、盘点单等）
    ShelfStatusMixin,      # 货架状态
    ContainerStatusMixin,  # 容器状态
    MaterialStatusMixin,   # 物料状态
)
```

**使用示例**：

```python
# 单据状态验证
class Inbound(DocumentStatusMixin, DataTableMixin, table=True):
    doc_status: str = Field(default="draft")
    # 自动获得 validate_document_status() 方法
```

**自动注册机制**：

BaseRepository 自动检测 `validate_xxx_status()` 方法并注册为 Hook：

1. 检测模型中的 `validate_xxx_status()` 方法
2. 自动注册为 BEFORE_UPDATE 和 BEFORE_DELETE Hook
3. 状态不允许时自动抛出 ValueError

**状态定义（DocumentStatusMixin）**：

```python
from src.database.document_status import DocStatus

# 状态枚举
DocStatus.DRAFT       # 草稿：允许编辑和删除
DocStatus.CONFIRMED   # 已确认：不允许编辑和删除
DocStatus.COMPLETED   # 已完成：只读
DocStatus.CANCELLED   # 已取消：只读
DocStatus.REJECTED    # 已拒绝：允许编辑（重新提交）
```

**状态流转规则**：

```python
# 状态机管理状态转换
from src.database.document_status import DocumentStateMachine

# 允许的状态转换
DRAFT → CONFIRMED, CANCELLED, REJECTED
CONFIRMED → COMPLETED, CANCELLED
REJECTED → CONFIRMED, CANCELLED
COMPLETED → 终态（不可转换）
CANCELLED → 终态（不可转换）
```

**状态验证示例**：

```python
# 创建草稿单据
inbound = await service.create(db, {"doc_status": "draft", ...})

# 更新草稿单据 - 成功
await service.update(db, inbound.id, {"quantity": 100})

# 确认单据
await service.update(db, inbound.id, {"doc_status": "confirmed"})

# 尝试更新已确认单据 - 失败
await service.update(db, inbound.id, {"quantity": 200})
# 抛出 ValueError: 当前状态 [confirmed] 不允许修改

# 尝试删除已确认单据 - 失败
await service.delete(db, inbound.id)
# 抛出 ValueError: 当前状态 [confirmed] 不允许删除
```

**自定义状态 Mixin**：

```python
# 创建自定义状态 Mixin
class CustomStatusMixin:
    status: str

    def validate_custom_status(self, operation: str) -> None:
        if operation == "edit" and self.status == "locked":
            raise ValueError("锁定状态不允许编辑")
        if operation == "delete" and self.status != "draft":
            raise ValueError("只有草稿状态可以删除")

# BaseRepository 自动检测并注册
```

### 关系加载系统

项目实现了**自动关系加载**，根据 Response Schema 自动推断并加载关联对象。

**Schema 驱动加载**：

```python
class UserResponse(BaseModel):
    id: int
    username: str
    roles: list[RoleResponse]  # 自动加载 roles 关系

# Repository 自动识别并加载
user = await repo.get_by_id(db, id=1, schema=UserResponse)
# 不会产生 N+1 查询
```

**手动控制加载**：

```python
# 只加载特定关系
user = await repo.get_by_id(
    db,
    id=1,
    include_relations=True,
    relation_names=["roles"]  # 只加载 roles
)

# 控制加载深度
user = await repo.get_by_id(
    db,
    id=1,
    schema=UserResponse,
    max_depth=2,  # 最多加载 2 层关系
)
```

### 查询构建系统

使用 `FilterGroup` 和 `SortField` 构建复杂查询，无需编写 SQL。

**基础查询**：

```python
from src.core.query_models import FilterGroup, FilterCondition, FilterOperator

# 等于查询
filters = FilterGroup(conditions=[
    FilterCondition(field="username", operator=FilterOperator.EQ, value="admin")
])
total, users = await repo.get_list(db, limit=10, offset=0, filters=filters)

# 模糊查询
filters = FilterGroup(conditions=[
    FilterCondition(field="email", operator=FilterOperator.ILIKE, value="example.com")
])

# 组合查询（OR 逻辑）
filters = FilterGroup(
    logic="OR",
    conditions=[
        FilterCondition(field="username", operator=FilterOperator.EQ, value="admin"),
        FilterCondition(field="email", operator=FilterOperator.EQ, value="admin@example.com"),
    ]
)
```

**查询接口**：

```python
@router.post("/query")
async def query_users(options: QueryOptions):
    total, users = await service.get_list(
        db,
        cache,
        options.limit,
        options.offset,
        options.filters,  # FilterGroup
        options.sort,     # list[SortField]
        options.max_depth,
    )
    return {"total": total, "items": users}
```

### RBAC 权限系统

基于角色的访问控制，权限码格式：`模块:资源:操作`

**权限码示例**：

```
admin:user:create    # 创建用户
admin:user:update    # 更新用户
admin:user:delete    # 删除用户
admin:user:list      # 查询用户列表
admin:user:detail    # 查看用户详情
```

**使用权限**：

```python
from src.core.rbac import RequirePermission

@router.post(
    "/users",
    dependencies=[Depends(RequirePermission("admin:user:create"))]
)
async def create_user(obj_in: UserCreate):
    pass
```

**权限注入方式**：
- 在路由装饰器中使用 `dependencies` 参数
- 不在函数参数中注入（避免污染函数签名）
- 权限验证在请求处理前自动执行
- 验证失败自动返回 403 错误

**超级用户**：

```python
from src.core.rbac import SUPERUSER_PERMISSION

# 超级用户拥有所有权限
if user.is_superuser:
    user.permissions = {SUPERUSER_PERMISSION}
```

### 缓存策略

**缓存层级**：

1. **Service 层缓存**： BaseService 自动缓存 `get_by_id` 和 `get_list` 结果
2. **权限缓存**： RBAC 系统缓存用户权限集合（5 分钟）
3. **分布式锁**：防止缓存击穿（查询热点数据时）

**缓存失效**：

```python
# 创建/更新/删除时自动失效缓存
await service.create(db, data, cache)  # 自动失效列表缓存
await service.update(db, id, data, cache)  # 自动失效详情缓存
await service.delete(db, id, cache)  # 自动失效详情和列表缓存
```

### 软删除系统

**SoftDeleteMixin 提供的能力**：

1. **软删除**：`DELETE /users/{id}` 自动设置 `is_deleted=True`
2. **永久删除**：`DELETE /users/{id}?permanent=true` 物理删除
3. **恢复**：`POST /users/{id}/restore` 恢复已删除记录
4. **回收站**：`GET /users/trash` 查看已删除记录
5. **批量恢复**：`POST /users/trash/restore` 批量恢复
6. **批量永久删除**：`DELETE /users/trash/permanent` 批量永久删除

**自动过滤**：

- 所有查询自动过滤 `is_deleted=False` 的记录
- 使用 `include_deleted=True` 查询已删除记录

### Schema 配置

**PostgreSQL Schema 隔离**：

```python
from src.database.schema_conf import SchemaType

class User(table=True):
    __schema__ = SchemaType.SYS.value  # wes_sys.users
    __tablename__ = "users"
```

**可用的 Schema**：

- `wes_sys`：系统管理（users, roles, permissions, configurations）
- `wes_biz`：业务数据（warehouses, containers, inventories）

## 开发规范

### 模块结构

每个业务模块遵循标准结构：

```
src/app/{module}/
├── models/           # 数据模型和 Pydantic Schema
│   ├── __init__.py
│   ├── user.py
│   └── role.py
├── repositories/     # 数据访问层
│   └── user_repository.py
├── services/         # 业务逻辑层
│   └── user_service.py
└── v1/              # API 路由层
    └── user.py
```

### 命名约定

**数据库表**：复数形式（`users`, `roles`, `warehouses`）
**模型类**：单数形式（`User`, `Role`, `Warehouse`）
**基础模型**：`{Model}Base`（`UserBase`）- 用于 Schema 复用
**Repository**：`{Model}Repository`（`UserRepository`）
**Service**：`{Model}Service`（`UserService`）
**API 路由**：复数形式（`/users`, `/roles`）

**模型定义模式**：
```python
# 正确模式：Base → Model
class UserBase(BaseMixin):
    """业务字段（用于 Schema 复用）"""
    username: str
    email: str

class User(UserBase, DataTableMixin, EnterpriseMixin, table=True):
    """数据库表（Base + Mixins + 表特有字段）"""
    __tablename__ = "users"
    hashed_password: str

# Schema 基于 UserBase 生成
class UserCreate(ModelFactory(UserBase).for_create()):
    password: str
```

**为什么使用 UserBase？**
- ✅ UserBase 只包含业务字段，适合在 Create/Update/Response 中复用
- ✅ User 添加 Mixins 和表特有字段（如 hashed_password）
- ✅ ModelFactory 基于 UserBase 自动生成 Schema
- ❌ 避免 Schema 包含不必要的字段（如 hashed_password 不应该在 Update 中）

### 依赖注入

**单例模式**（推荐）：

```python
# 在 repositories/__init__.py 中创建单例
user_repository = UserRepository()

# 在 services/__init__.py 中创建单例
user_service = UserService(user_repository)

# 在 API 中使用
from src.app.admin.services import user_service

user_api = BaseAPI(service=user_service, ...)
```

**FastAPI 依赖注入**：

```python
from src.database.dependencies import AsyncSessionDep, CacheDep

@router.get("/users/{id}")
async def get_user(
    id: int,
    db: AsyncSessionDep,  # 自动注入数据库会话
    cache: CacheDep,      # 自动注入缓存服务
):
    return await user_service.get_by_id(db, cache, id)
```

### 错误处理

**使用自定义异常**：

```python
from src.core.exceptions import AppException, ResourceNotFoundException

if not user:
    raise ResourceNotFoundException(f"用户 {id} 不存在")
```

**全局异常处理器**（已配置）：

- `AppException`：统一响应格式
- `ResourceNotFoundException`：404 错误
- `OptimisticLockException`：乐观锁冲突
- `PermissionException`：权限不足

**ErrorTranslator 错误翻译器**：

项目使用 **ErrorTranslator** 自动将数据库 IntegrityError 转换为友好的中文提示，极大改善用户体验。

**支持的错误类型**：
```python
# 1. 外键约束错误
# 数据库错误：violates foreign key constraint
# 友好提示：当前删除的数据与[用户]中的数据有关联，请先删除[用户]关联的数据

# 2. 唯一约束错误
# 数据库错误：duplicate key value violates unique constraint
# 友好提示：用户名 'admin' 已被使用，请使用其他值

# 3. 非空约束错误
# 数据库错误：null value in column "xxx" violates not-null constraint
# 友好提示：[用户名]不能为空
```

**自动配置**：
```python
# BaseRepository 自动配置 ErrorTranslator
class BaseRepository[T]:
    def __init__(self, model: type[T]):
        self.error_translator = ErrorTranslator(model)

    async def create(self, db, data):
        try:
            instance = self.model(**data)
            db.add(instance)
            await db.flush()
        except IntegrityError as e:
            # 自动转换为友好提示
            self.error_translator.handle_integrity_error(e)
```

**字段名称映射**：
```python
# ErrorTranslator 自动从模型中提取中文名称
class User(DataTableMixin, table=True):
    username: str = Field(description="用户名")  # 自动识别
    email: str = Field(description="邮箱")
```

### 测试规范

**测试文件结构**：

```
tests/
├── test_base_repository.py
├── test_base_service.py
├── test_query_builder.py
└── test_relation_metadata.py
```

**pytest 配置**：

- `asyncio_mode = "auto"`：自动异步测试
- `--cov=src`：生成覆盖率报告
- `--durations=10`：显示最慢的 10 个测试

## 关键文件说明

### 核心框架

- `src/database/base_repository.py`：通用 Repository 基类（CRUD + Hook + 关系加载）
- `src/core/base_service.py`：通用 Service 基类（业务协调 + 缓存）
- `src/core/base_api.py`：通用 API 基类（零代码 CRUD 生成）
- `src/database/model_factory.py`：动态 Schema 工厂（自动生成 Create/Update Schema）
- `src/database/tree_repository.py`：树形结构 Repository
- `src/core/tree_service.py`：树形结构 Service
- `src/core/tree_api.py`：树形结构 API（继承 BaseAPI）

### Mixin 系统

- `src/core/mixins/__init__.py`：Mixin 导入（查看所有可用 Mixin）
- `src/core/mixins/datatable.py`：DataTableMixin（标准表字段）
- `src/core/mixins/audit.py`：AuditMixin（审计字段）
- `src/core/mixins/soft_delete.py`：SoftDeleteMixin（软删除）
- `src/core/mixins/tree.py`：TreeMixin（树形结构）
- `src/core/mixins/optimistic_lock.py`：OptimisticLockMixin（乐观锁）
- `src/core/mixins/composite.py`：组合 Mixin（StandardMixin, AuditableMixin）

### 查询和关系

- `src/core/query_builder.py`：查询构建器（FilterGroup → SQLAlchemy 表达式）
- `src/core/schema_loader.py`：Schema 加载器（自动关系加载）
- `src/core/query_models.py`：查询模型（FilterGroup, SortField, QueryOptions）
- `src/database/relation_metadata.py`：关系元数据（自动发现关联关系）
- `src/database/relations/`：关系处理（加载、更新、删除）

### Hook 系统

- `src/database/hooks/hook_system.py`：Hook 基础设施
- `src/database/audit/hook_registrar.py`：审计日志 Hook 注册器

### 其他核心组件

- `src/core/rbac.py`：RBAC 权限系统
- `src/core/conf.py`：应用配置（Pydantic Settings）
- `src/database/db.py`：数据库连接和会话管理
- `src/database/dependencies.py`：FastAPI 依赖注入（AsyncSessionDep, CacheDep）
- `src/database/handlers/error_translator.py`：错误翻译器（IntegrityError → 友好提示）
- `src/database/status_mixins.py`：状态验证 Mixin（DocumentStatusMixin 等）
- `src/database/document_status.py`：单据状态机（DocStatus, DocumentStateMachine）
- `src/core/response/`：统一响应格式
- `src/core/exceptions.py`：自定义异常

## 设计原则

项目严格遵循以下原则，编码时请务必遵守：

### DRY 原则（Don't Repeat Yourself）

- **使用 Mixin**：字段和行为通过 Mixin 复用，不要重复定义
- **使用 ModelFactory**：Create/Update Schema 通过工厂模式自动生成，避免手写重复代码
- **继承基类**：CRUD 操作通过 BaseAPI/BaseService/BaseRepository 复用
- **Hook 扩展**：业务逻辑通过 Hook 扩展，不要修改基类代码

### KISS 原则（Keep It Simple, Stupid）

- **优先简单**：使用基类提供的默认实现，不要过度抽象
- **避免复杂**：不需要的功能不要添加（YAGNI）
- **明确命名**：变量、函数、类的命名要清晰表达意图

### SOLID 原则

- **单一职责**：Repository 只负责数据访问，Service 只负责业务逻辑，API 只负责路由
- **开闭原则**：通过 Hook 和 Mixin 扩展功能，而不是修改基类
- **依赖倒置**：依赖抽象（BaseService），而不是具体实现

### YAGNI 原则（You Aren't Gonna Need It）

- **只实现当前需求**：不要为"将来可能需要"的功能编写代码
- **避免过度设计**：如果继承 BaseRepository 足够，就不要创建抽象层
- **渐进式优化**：先让代码工作，再优化性能

---

## 分层架构规则（CRITICAL）

### 🚨 严格禁止的架构违规

| 违规行为 | 问题描述 | 后果 |
|----------|----------|------|
| **API → Repository** | 路由层直接访问 Repository | 跳过业务逻辑层、无缓存 |
| **API → Database** | 路由层直接执行 SQL | 无法复用、难测试、职责混乱 |
| **跨层访问** | 任何跳过中间层的直接调用 | 破坏分层、耦合度过高 |

### ✅ 正确的依赖方向

```
┌─────────────────────────────────────────────┐
│           API 层（路由、验证、响应）          │
│  职责：路由定义、请求验证、响应转换              │
│  禁止：❌ 直接访问数据库/Repository           │
└──────────────────┬──────────────────────────┘
                   │ 只能调用 Service
                   ↓
┌─────────────────────────────────────────────┐
│       Service 层（业务逻辑、缓存、事务）        │
│  职责：业务协调、缓存管理、事务控制              │
│  允许：✅ 调用其他 Service（需单向依赖）        │
└──────────────────┬──────────────────────────┘
                   │ 只能调用 Repository
                   ↓
┌─────────────────────────────────────────────┐
│      Repository 层（数据访问、CRUD）           │
│  职责：数据访问、关系加载、Hook 执行            │
│  禁止：❌ 直接调用 Service（向上依赖）          │
└──────────────────┬──────────────────────────┘
                   │
                   ↓
              数据库 (PostgreSQL/Redis)
```

### 📋 架构合规检查清单

在提交代码前，请确认：

- [ ] API 层**没有** `from sqlalchemy import select`
- [ ] API 层**没有** `db.execute()` 或 `db.scalar()`
- [ ] API 层**所有**数据操作都通过 `xxx_service.xxx()` 完成
- [ ] Service 层方法都在 `services/` 目录中
- [ ] Repository 层方法都在 `repositories/` 目录中

### 🔴 典型违规案例（DO NOT DO THIS）

```python
# ❌ 错误：API 层直接访问数据库
from sqlalchemy import select
from src.app.admin.models import Permission

@router.get("/permissions")
async def get_permissions(db: AsyncSessionDep):
    result = await db.execute(
        select(Permission)
        .where(Permission.type == "api")
    )
    return result.scalars().all()
```

### 🟢 正确实现（DO THIS）

```python
# ✅ 正确：API 层调用 Service
from src.app.admin.services import permission_service

@router.get("/permissions")
async def get_permissions(db: AsyncSessionDep):
    # 通过 Service 层获取数据（符合分层架构）
    return await permission_service.get_api_permissions(db)
```

---

## Service 相互调用规则

### ✅ 允许的调用模式

| 场景 | 示例 | 条件 |
|------|------|------|
| **API → 同模块 Service** | `api_application.py` → `api_app_service` | ✅ 正常 |
| **API → 跨模块 Service** | `api_application.py` → `permission_service` | ✅ 允许（单向依赖） |
| **Service → Service** | `InboundService` → `InventoryService` | ⚠️ 谨慎（需单向依赖） |

### 🚨 禁止的调用模式

| 违规行为 | 示例 | 原因 |
|----------|------|------|
| **循环依赖** | A → B → A | 启动时导入错误 |
| **直接初始化注入** | `__init__` 中 `self.service_b = ServiceB()` | 可能循环导入 |
| **频繁跨模块调用** | 一个方法调用 5+ 个其他 Service | 应用领域事件 |

### 📖 Service 调用最佳实践

```python
# ✅ 推荐：方法内部懒加载导入（避免循环依赖）
class InboundService(BaseService):
    async def confirm_inbound(self, db, inbound_id: int):
        # 1. 更新入库单
        await self.update(db, inbound_id, {"status": "confirmed"})

        # 2. 延迟导入（避免启动时循环依赖）
        from src.app.warehousing.services import inventory_service

        # 3. 调用其他 Service
        await inventory_service.increase_stock(db, inbound_id)
```

### 🎯 更好的替代方案（复杂场景）

**方案 1：领域事件（解耦）**
```python
# 发布事件，其他 Service 监听并处理
from src.core.events import EventBus

await EventBus.publish("inbound.confirmed", inbound_id=inbound_id)
```

**方案 2：Facade 模式（编排）**
```python
# 专门的编排层协调多个 Service
class InboundFacadeService:
    async def process_inbound(self, db, inbound_id: int):
        await self.inbound_service.confirm(db, inbound_id)
        await self.inventory_service.update_stock(db, inbound_id)
        await self.notification_service.notify(db, inbound_id)
```

### 🔍 判断标准

使用前请确认：

1. **依赖方向**：是否保持单向？（如：`api_auth` → `admin`，不应反向）
2. **职责边界**：Service 是否仍在做业务协调（而非变成"万能类"）？
3. **循环风险**：是否会产生 A → B → A 的依赖链？
4. **替代方案**：复杂场景是否应该用领域事件解耦？

---

## 常见任务

### 创建新模块

```bash
# 1. 创建模块目录
mkdir -p src/app/{module}/models
mkdir -p src/app/{module}/repositories
mkdir -p src/app/{module}/services
mkdir -p src/app/{module}/v1

# 2. 创建模型（使用 Mixin 组合）
# src/app/{module}/models/models.py

# 3. 创建 Repository（继承 BaseRepository）
# src/app/{module}/repositories/repository.py

# 4. 创建 Service（继承 BaseService）
# src/app/{module}/services/service.py

# 5. 创建 API（继承 BaseAPI，零代码）
# src/app/{module}/v1/router.py

# 6. 在 src/register.py 中注册路由
```

**完整的模块创建示例**：

```python
# ===== models/warehouse.py =====
from datetime import datetime
from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, SoftDeleteMixin
from src.database.model_factory import ModelFactory

# 1. 基础字段（WarehouseBase - 纯业务字段）
class WarehouseBase(BaseMixin):
    """仓库基础字段 - 用于 Schema 复用"""
    name: str
    code: str
    location: str | None = None

# 2. 数据库表模型（Warehouse - 继承 Base + Mixins）
class Warehouse(WarehouseBase, DataTableMixin, EnterpriseMixin, SoftDeleteMixin, table=True):
    """仓库数据库表模型"""
    __tablename__ = "warehouses"
    # 表特有字段（不在 Base 中）
    capacity: int | None = None

# 3. 使用 ModelFactory 自动生成 Schema
class WarehouseCreate(ModelFactory(WarehouseBase).for_create()):
    """创建 Schema（基于 WarehouseBase，所有字段必需）"""
    pass

class WarehouseUpdate(ModelFactory(WarehouseBase).for_update()):
    """更新 Schema（基于 WarehouseBase，所有字段可选）"""
    pass

class WarehouseResponse(WarehouseBase):
    """响应 Schema（基于 WarehouseBase，添加系统字段）"""
    id: int
    capacity: int | None
    created_at: datetime
    updated_at: datetime

# ===== repositories/warehouse_repository.py =====
class WarehouseRepository(BaseRepository[Warehouse]):
    pass

# 创建单例
warehouse_repository = WarehouseRepository()

# ===== services/warehouse_service.py =====
class WarehouseService(BaseService[Warehouse, WarehouseRepository]):
    def __init__(self):
        super().__init__(
            warehouse_repository,
            enable_cache=True,
            cache_prefix="app:warehouse:detail",
        )

# 创建单例
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

### 添加自定义业务逻辑

```python
# 方式1：在 Service 中添加新方法
class UserService(BaseService[User, UserRepository]):
    async def get_by_username(self, db, username: str):
        return await self.repo.get_by_field(db, "username", username)

# 方式2：使用 Hook 拦截 CRUD 操作
class UserService(BaseService[User, UserRepository]):
    def __init__(self):
        super().__init__(UserRepository())
        self.add_hook(HookType.BEFORE_CREATE, self._custom_logic)

    async def _custom_logic(self, context: HookContext) -> None:
        # 自定义逻辑
        pass
```

### 添加状态验证

```python
from src.core.mixins import DataTableMixin
from src.database.status_mixins import DocumentStatusMixin
from src.database.document_status import DocStatus

# 1. 定义带状态验证的模型
class Inbound(DocumentStatusMixin, DataTableMixin, table=True):
    doc_status: str = Field(default=DocStatus.DRAFT)
    # 自动获得 validate_document_status(operation) 方法

# 2. BaseRepository 自动注册状态验证 Hook
# - BEFORE_UPDATE: 验证是否允许编辑
# - BEFORE_DELETE: 验证是否允许删除

# 3. 使用示例
# 草稿状态 - 允许编辑和删除
await service.update(db, id, {"quantity": 100})  # ✅ 成功
await service.delete(db, id)  # ✅ 成功

# 已确认状态 - 不允许编辑和删除
await service.update(db, id, {"quantity": 100})  # ❌ ValueError
await service.delete(db, id)  # ❌ ValueError
```

### 树形结构处理

```python
from src.core.mixins import TreeMixin, DataTableMixin

class Category(TreeMixin, DataTableMixin, table=True):
    name: str

# 使用 TreeRepository 和 TreeService
from src.database.tree_repository import TreeRepository
from src.core.tree_service import TreeServiceMixin
from src.core.tree_api import TreeAPI

class CategoryRepository(TreeRepository[Category]):
    pass

class CategoryService(TreeServiceMixin, BaseService[Category, CategoryRepository]):
    pass

# 自动获得树形 API：
# - GET /categories/tree - 获取树形结构
# - GET /categories/siblings/{node_id} - 获取同级节点
# - GET /categories/ancestors/{node_id} - 获取祖先路径
# - PUT /categories/move - 移动节点
```

## 项目特定配置

### Ruff 配置

项目使用严格的 Ruff 规则，注意以下特殊情况：

- 测试文件规则宽松（允许魔法值、未使用变量等）
- FastAPI 依赖注入允许函数默认值（`B008` 规则忽略）
- SQLModel 允许大写变量名（`N806` 规则忽略）

### pytest 配置

- `asyncio_mode = "auto"`：自动异步测试
- `--cov-report=term-missing`：显示未覆盖的行号
- `--durations=10`：显示最慢的 10 个测试

### 数据库迁移

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

## 故障排查

### 缓存未生效

检查：

1. Redis 是否运行：`redis-cli ping`
2. 查看缓存键：`redis-cli KEYS "app:*"`
3. 查看 TTL：`redis-cli TTL app:user:detail:1`

### N+1 查询问题

使用自动关系加载：

```python
# 错误：会产生 N+1
users = await repo.get_list(db)
for user in users:
    print(user.roles)  # 每次都查询数据库

# 正确：使用 Schema 自动加载
users = await repo.get_list(db, schema=UserResponse)
```

### 权限缓存未更新

权限变更后手动清除缓存：

```python
from src.core.rbac import invalidate_user_permissions

await invalidate_user_permissions(cache, user_id)
```

### 架构违规检测（CRITICAL）

**症状**：
- 代码审查中发现 API 层包含数据库操作
- 业务逻辑分散在多个层级
- 单元测试困难，需要模拟数据库连接

**检测命令**：
```bash
# 检查 API 层是否违规访问数据库
grep -r "from sqlalchemy import select" src/app/*/v1/
grep -r "db.execute(" src/app/*/v1/
grep -r "db.scalar(" src/app/*/v1/

# 检查是否有跨层访问
grep -r "from.*repositories import" src/app/*/v1/
```

**修复方法**：
```python
# ❌ 错误：API 层直接访问数据库
from sqlalchemy import select

@router.get("/items")
async def get_items(db: AsyncSessionDep):
    result = await db.execute(select(Item))
    return result.scalars().all()

# ✅ 正确：通过 Service 层访问
@router.get("/items")
async def get_items(db: AsyncSessionDep):
    return await item_service.get_all(db)
```
