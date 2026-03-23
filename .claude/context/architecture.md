---
purpose: 核心架构详情
indexed_by: CLAUDE.md
tags: [architecture, hooks, mixins, jwt, rbac]
---

# 核心架构详情

> 详见 CLAUDE.md 的架构索引，本文档提供详细的架构实现说明。

## 分层架构

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

## 零代码开发模式

通过继承基类自动获得完整 CRUD 能力：

```python
# 1. 定义基础字段（UserBase - 业务字段）
class UserBase(BaseMixin):
    username: str
    email: str

# 2. 定义数据库表模型（User - 继承 UserBase + Mixins）
class User(UserBase, DataTableMixin, EnterpriseMixin, SoftDeleteMixin, table=True):
    __tablename__ = "users"
    hashed_password: str

# 3. 使用 ModelFactory 自动生成 Schema
class UserCreate(ModelFactory(UserBase).for_create()):
    password: str

class UserUpdate(ModelFactory(UserBase).for_update()):
    pass

# 4. 定义 Response Schema
class UserResponse(UserBase):
    id: int
    created_at: datetime
    roles: list[RoleResponse] = []

# 5. 定义 Repository/Service/API（零代码）
class UserRepository(BaseRepository[User]):
    pass

class UserService(BaseService[User, UserRepository]):
    def __init__(self):
        super().__init__(UserRepository(), enable_cache=True)

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

**自动生成的路由**：
- `POST /users` - 创建（权限：`admin:user:create`）
- `PUT /users/{id}` - 更新（`admin:user:update`）
- `DELETE /users/{id}` - 删除（`admin:user:delete`）
- `GET /users/{id}` - 获取单个（`admin:user:detail`）
- `POST /users/query` - 列表查询（`admin:user:list`）
- `POST /users/{id}/restore` - 恢复（软删除）
- `GET /users/trash` - 回收站（软删除）

## Hook 系统

Hook 系统允许在 Repository 的 CRUD 操作前后插入自定义逻辑。

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

**自动注册的 Hook**：
1. **状态验证 Hook**：检测 `validate_xxx_status()` 方法并自动注册
2. **审计字段 Hook**：检测 `created_by/updated_by` 字段并自动填充
3. **乐观锁 Hook**：检测 `version` 字段并自动验证和递增
4. **审计日志 Hook**：检测 `AuditableMixin` 并自动记录操作历史

**自定义 Hook 示例**：
```python
class UserService(BaseService[User, UserRepository]):
    def __init__(self):
        super().__init__(UserRepository())
        self.add_hook(HookType.BEFORE_CREATE, self._hash_password)

    async def _hash_password(self, context: HookContext) -> None:
        data = context.params.get("data", {})
        if "password" in data:
            data["password"] = hash_password(data["password"])
```

## Mixin 系统

Mixin 提供可复用的模型字段和行为，遵循**组合优于继承**原则。

**常用 Mixin**：
```python
from src.core.mixins import (
    DataTableMixin,        # 标准表字段（id, created_at, updated_at）
    EnterpriseMixin,       # 企业字段（created_by, updated_by, remark）
    SoftDeleteMixin,       # 软删除字段（is_deleted, deleted_at, deleted_by）
    TreeMixin,            # 树形结构字段（parent_id, tree_path, level, sort_order）
    OptimisticLockMixin,  # 乐观锁字段（version）
    AuditableMixin,       # 审计日志能力
)
```

**⚠️ Mixin 继承规范**：
- `EnterpriseMixin` **已包含** `AuditMixin` 和 `OptimisticLockMixin`，不要重复继承
- ❌ 错误：`class User(UserBase, AuditMixin, EnterpriseMixin, ...)`
- ✅ 正确：`class User(UserBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, ...)`

**Mixin 继承层次**：
```
EnterpriseMixin = AuditableMixin + OptimisticLockMixin
AuditableMixin = AuditMixin + StandardMixin
AuditMixin → TimestampMixin → BaseMixin
```

## 状态验证系统

项目提供**状态验证 Mixin**，自动验证单据、货架、容器等状态。

**可用的状态 Mixin**：
```python
from src.database.status_mixins import (
    DocumentStatusMixin,   # 单据状态
    ShelfStatusMixin,      # 货架状态
    ContainerStatusMixin,  # 容器状态
    MaterialStatusMixin,   # 物料状态
)
```

**使用示例**：
```python
class Inbound(DocumentStatusMixin, DataTableMixin, table=True):
    doc_status: str = Field(default="draft")
    # 自动获得 validate_document_status() 方法
```

**状态定义**：
```python
from src.database.document_status import DocStatus

DocStatus.DRAFT       # 草稿：允许编辑和删除
DocStatus.CONFIRMED   # 已确认：不允许编辑和删除
DocStatus.COMPLETED   # 已完成：只读
DocStatus.CANCELLED   # 已取消：只读
DocStatus.REJECTED    # 已拒绝：允许编辑（重新提交）
```

## 关系加载系统

项目实现了**自动关系加载**，根据 Response Schema 自动推断并加载关联对象。

```python
class UserResponse(BaseModel):
    id: int
    username: str
    roles: list[RoleResponse]  # 自动加载 roles 关系

user = await repo.get_by_id(db, id=1, schema=UserResponse)
# 不会产生 N+1 查询
```

## JWT 认证系统

**TokenPayload 结构**：
```python
@dataclass(frozen=True)
class TokenPayload:
    sub: str                  # 用户 ID
    jti: str                  # 唯一标识符
    exp: int                  # 过期时间
    token_type: TokenType     # ACCESS 或 REFRESH
    session_uuid: str         # 会话 UUID
    is_superuser: bool        # 超级用户标识（性能优化）
```

**性能优化设计**：
- `is_superuser` 编码在 Token 中，避免查询数据库
- `require_auth` 验证时自动填充 `request.state.is_superuser`

## RBAC 权限系统

权限码格式：`模块:资源:操作`

```python
from src.core.rbac import RequirePermission

@router.post(
    "/users",
    dependencies=[Depends(RequirePermission("admin:user:create"))]
)
async def create_user(obj_in: UserCreate):
    pass
```

## 缓存策略

1. **Service 层缓存**：BaseService 自动缓存 `get_by_id` 和 `get_list` 结果
2. **权限缓存**：RBAC 系统缓存用户权限集合（5 分钟）
3. **JWT Token 缓存**：`is_superuser` 编码在 Token 中

## 软删除系统

**SoftDeleteMixin 提供的能力**：
- 软删除：`DELETE /users/{id}` 自动设置 `is_deleted=True`
- 永久删除：`DELETE /users/{id}?permanent=true`
- 恢复：`POST /users/{id}/restore`
- 回收站：`GET /users/trash`