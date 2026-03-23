# Mixin 选择指南

本文档帮助你选择合适的 Mixin 组合来构建数据模型。

## 可用的 Mixin

### 核心 Mixin

#### BaseMixin
**用途**：所有模型的基础，提供 SQLModel 配置

**包含**：
- `model_config` - Pydantic 配置

**使用场景**：所有 Base 模型必须继承

```python
class UserBase(BaseMixin):
    username: str
```

#### DataTableMixin
**用途**：标准数据表字段

**包含**：
- `id: int` - 主键（自增）
- `created_at: datetime` - 创建时间
- `updated_at: datetime` - 更新时间

**使用场景**：所有数据库表必须继承

```python
class User(UserBase, DataTableMixin, table=True):
    __tablename__ = "users"
```

### 企业功能 Mixin

#### EnterpriseMixin
**用途**：企业级审计字段

**包含**：
- `created_by: int | None` - 创建人 ID
- `updated_by: int | None` - 更新人 ID
- `remark: str | None` - 备注

**使用场景**：
- ✅ 需要记录操作人
- ✅ 需要审计追踪
- ✅ 企业级应用

```python
class Order(OrderBase, DataTableMixin, EnterpriseMixin, table=True):
    __tablename__ = "orders"
```

**自动填充**：
- BaseRepository 自动检测并填充 `created_by` / `updated_by`
- 从 `request.state.user_id` 获取当前用户 ID

#### SoftDeleteMixin
**用途**：软删除功能

**包含**：
- `is_deleted: bool` - 删除标记
- `deleted_at: datetime | None` - 删除时间
- `deleted_by: int | None` - 删除人 ID

**使用场景**：
- ✅ 需要回收站功能
- ✅ 需要恢复已删除数据
- ✅ 需要保留删除历史

```python
class Product(ProductBase, DataTableMixin, SoftDeleteMixin, table=True):
    __tablename__ = "products"
```

**自动功能**：
- `DELETE /products/{id}` - 软删除（设置 `is_deleted=True`）
- `DELETE /products/{id}?permanent=true` - 永久删除
- `POST /products/{id}/restore` - 恢复
- `GET /products/trash` - 回收站
- 所有查询自动过滤 `is_deleted=False`

### 树形结构 Mixin

#### TreeMixin
**用途**：树形结构（物化路径模式）

**包含**：
- `parent_id: int | None` - 父节点 ID
- `tree_path: str` - 树形路径（如：`/1/2/3/`）
- `level: int` - 层级（从 0 开始）
- `sort_order: int` - 排序

**使用场景**：
- ✅ 菜单、分类、组织架构
- ✅ 需要层级查询
- ✅ 需要祖先/后代查询

```python
class Category(TreeMixin, DataTableMixin, table=True):
    __tablename__ = "categories"
    name: str
```

**自动功能**：
- TreeRepository 自动维护 `tree_path`
- 提供 `get_children`, `get_descendants`, `get_ancestors` 方法
- TreeAPI 提供树形路由（`/tree`, `/siblings/{id}`, `/ancestors/{id}`）

### 并发控制 Mixin

#### OptimisticLockMixin
**用途**：乐观锁（防止并发冲突）

**包含**：
- `version: int` - 版本号

**使用场景**：
- ✅ 高并发更新
- ✅ 需要防止覆盖
- ✅ 库存、余额等关键数据

```python
class Inventory(InventoryBase, DataTableMixin, OptimisticLockMixin, table=True):
    __tablename__ = "inventories"
```

**自动功能**：
- BaseRepository 自动检测并验证版本号
- 更新时自动递增版本号
- 版本不匹配时抛出 `OptimisticLockException`

### 审计日志 Mixin

#### AuditableMixin
**用途**：审计日志能力（不是字段，是行为）

**使用场景**：
- ✅ 需要记录操作历史
- ✅ 合规要求
- ✅ 关键业务数据

```python
class Order(OrderBase, DataTableMixin, AuditableMixin, table=True):
    __tablename__ = "orders"
```

**自动功能**：
- BaseRepository 自动检测并记录操作历史
- 记录到 `audit_logs` 表
- 包含操作类型、操作人、操作时间、变更内容

## Mixin 组合模式

### 标准业务表

**场景**：用户、角色、仓库、产品等独立实体

**推荐组合**：
```python
class User(UserBase, DataTableMixin, EnterpriseMixin, SoftDeleteMixin, table=True):
    __tablename__ = "users"
```

**包含功能**：
- ✅ 标准表字段（id, created_at, updated_at）
- ✅ 审计字段（created_by, updated_by, remark）
- ✅ 软删除（is_deleted, deleted_at, deleted_by）

### 树形结构表

**场景**：菜单、分类、组织架构等层级关系

**推荐组合**：
```python
class Menu(MenuBase, TreeMixin, DataTableMixin, SoftDeleteMixin, table=True):
    __tablename__ = "menus"
```

**包含功能**：
- ✅ 树形字段（parent_id, tree_path, level, sort_order）
- ✅ 标准表字段
- ✅ 软删除

**注意**：TreeMixin 必须在 DataTableMixin 之前

### 高并发表

**场景**：库存、余额、订单等需要并发控制的数据

**推荐组合**：
```python
class Inventory(
    InventoryBase,
    DataTableMixin,
    EnterpriseMixin,
    OptimisticLockMixin,
    table=True
):
    __tablename__ = "inventories"
```

**包含功能**：
- ✅ 标准表字段
- ✅ 审计字段
- ✅ 乐观锁（version）

### 审计表

**场景**：订单、入库单、出库单等需要审计的业务数据

**推荐组合**：
```python
class Order(
    OrderBase,
    DataTableMixin,
    EnterpriseMixin,
    SoftDeleteMixin,
    AuditableMixin,
    table=True
):
    __tablename__ = "orders"
```

**包含功能**：
- ✅ 标准表字段
- ✅ 审计字段
- ✅ 软删除
- ✅ 审计日志

### 只读表

**场景**：日志、审计记录等只读数据

**推荐组合**：
```python
class AuditLog(AuditLogBase, DataTableMixin, table=True):
    __tablename__ = "audit_logs"
```

**包含功能**：
- ✅ 标准表字段

**API 配置**：
```python
audit_log_api = BaseAPI(
    module_name="sys",
    model=AuditLog,
    service=audit_log_service,
    gen_create=False,  # 禁用创建
    gen_update=False,  # 禁用更新
    gen_delete=False,  # 禁用删除
)
```

## Mixin 顺序规则

**重要**：Mixin 的顺序会影响字段的继承和覆盖

### 推荐顺序

```python
class Model(
    TreeMixin,           # 1. 树形结构（如果需要）
    DataTableMixin,      # 2. 标准表字段
    EnterpriseMixin,     # 3. 企业字段
    SoftDeleteMixin,     # 4. 软删除
    OptimisticLockMixin, # 5. 乐观锁
    AuditableMixin,      # 6. 审计日志
    table=True
):
    pass
```

### 顺序原因

1. **TreeMixin 在前**：树形字段需要在标准字段之前定义
2. **DataTableMixin 必需**：所有表都需要标准字段
3. **功能 Mixin 在后**：按功能重要性排序

## 决策树

### 是否需要树形结构？

```
需要父子关系？
├─ 是 → 使用 TreeMixin
│   └─ 示例：菜单、分类、组织架构
└─ 否 → 不使用 TreeMixin
    └─ 示例：用户、角色、产品
```

### 是否需要软删除？

```
需要回收站功能？
├─ 是 → 使用 SoftDeleteMixin
│   └─ 示例：用户、产品、订单
└─ 否 → 不使用 SoftDeleteMixin
    └─ 示例：日志、审计记录
```

### 是否需要审计字段？

```
需要记录操作人？
├─ 是 → 使用 EnterpriseMixin
│   └─ 示例：订单、入库单、出库单
└─ 否 → 不使用 EnterpriseMixin
    └─ 示例：系统配置、字典数据
```

### 是否需要乐观锁？

```
高并发更新？
├─ 是 → 使用 OptimisticLockMixin
│   └─ 示例：库存、余额、订单状态
└─ 否 → 不使用 OptimisticLockMixin
    └─ 示例：用户信息、产品信息
```

### 是否需要审计日志？

```
需要记录操作历史？
├─ 是 → 使用 AuditableMixin
│   └─ 示例：订单、入库单、出库单
└─ 否 → 不使用 AuditableMixin
    └─ 示例：用户、产品、分类
```

## 常见组合速查表

| 场景 | Mixin 组合 | 示例 |
|------|-----------|------|
| 标准业务表 | DataTableMixin + EnterpriseMixin + SoftDeleteMixin | User, Role, Product |
| 树形结构表 | TreeMixin + DataTableMixin + SoftDeleteMixin | Menu, Category, Organization |
| 高并发表 | DataTableMixin + EnterpriseMixin + OptimisticLockMixin | Inventory, Balance |
| 审计表 | DataTableMixin + EnterpriseMixin + SoftDeleteMixin + AuditableMixin | Order, Inbound, Outbound |
| 只读表 | DataTableMixin | AuditLog, SystemLog |
| 配置表 | DataTableMixin + EnterpriseMixin | Config, Dictionary |

## 示例代码

### 标准业务表（用户）

```python
from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, SoftDeleteMixin

class UserBase(BaseMixin):
    username: str = Field(max_length=50)
    email: str = Field(max_length=100)

class User(UserBase, DataTableMixin, EnterpriseMixin, SoftDeleteMixin, table=True):
    __tablename__ = "users"
    hashed_password: str = Field(max_length=255)
```

### 树形结构表（菜单）

```python
from src.core.mixins import BaseMixin, TreeMixin, DataTableMixin, SoftDeleteMixin

class MenuBase(TreeMixin, BaseMixin):
    name: str = Field(max_length=50)
    title: str = Field(max_length=50)

class Menu(MenuBase, DataTableMixin, SoftDeleteMixin, table=True):
    __tablename__ = "menus"
```

### 高并发表（库存）

```python
from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, OptimisticLockMixin

class InventoryBase(BaseMixin):
    product_id: int = Field(foreign_key="products.id")
    warehouse_id: int = Field(foreign_key="warehouses.id")
    quantity: int = Field(ge=0)

class Inventory(
    InventoryBase,
    DataTableMixin,
    EnterpriseMixin,
    OptimisticLockMixin,
    table=True
):
    __tablename__ = "inventories"
```

### 审计表（订单）

```python
from src.core.mixins import (
    BaseMixin,
    DataTableMixin,
    EnterpriseMixin,
    SoftDeleteMixin,
    AuditableMixin,
)

class OrderBase(BaseMixin):
    order_no: str = Field(max_length=50, unique=True)
    customer_id: int = Field(foreign_key="customers.id")
    total_amount: float = Field(ge=0)

class Order(
    OrderBase,
    DataTableMixin,
    EnterpriseMixin,
    SoftDeleteMixin,
    AuditableMixin,
    table=True
):
    __tablename__ = "orders"
```

## 常见错误

### 错误 1：TreeMixin 顺序错误

```python
# ❌ 错误：TreeMixin 在 DataTableMixin 之后
class Menu(MenuBase, DataTableMixin, TreeMixin, table=True):
    pass

# ✅ 正确：TreeMixin 在 DataTableMixin 之前
class Menu(MenuBase, TreeMixin, DataTableMixin, table=True):
    pass
```

### 错误 2：Base 包含 Mixin

```python
# ❌ 错误：Base 包含 DataTableMixin
class UserBase(DataTableMixin, BaseMixin):
    username: str

# ✅ 正确：Base 只包含业务字段
class UserBase(BaseMixin):
    username: str

class User(UserBase, DataTableMixin, table=True):
    pass
```

### 错误 3：缺少 DataTableMixin

```python
# ❌ 错误：缺少 DataTableMixin
class User(UserBase, EnterpriseMixin, table=True):
    pass

# ✅ 正确：必须包含 DataTableMixin
class User(UserBase, DataTableMixin, EnterpriseMixin, table=True):
    pass
```

## 总结

1. **DataTableMixin 必需** - 所有表都需要
2. **TreeMixin 在前** - 树形结构必须在 DataTableMixin 之前
3. **功能 Mixin 按需** - 根据业务需求选择
4. **Base 纯业务** - Base 只包含业务字段，不包含 Mixin
5. **顺序很重要** - 遵循推荐的 Mixin 顺序
