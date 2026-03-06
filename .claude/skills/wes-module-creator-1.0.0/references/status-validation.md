# 状态验证系统详解

WES Backend 提供完整的状态验证系统，专为 WMS/WES 业务场景设计，自动验证单据、货架、容器等状态。

## 状态验证架构

```
StatusMixin (状态 Mixin)
    ↓
validate_xxx_status() (验证方法)
    ↓
BaseRepository (自动检测并注册 Hook)
    ↓
BEFORE_UPDATE / BEFORE_DELETE Hook
```

## 可用的状态 Mixin

### 1. DocumentStatusMixin（单据状态）

**适用场景**：入库单、出库单、盘点单、调拨单等业务单据

**状态定义**：

```python
from src.database.document_status import DocStatus

DocStatus.DRAFT       # 草稿：允许编辑和删除
DocStatus.CONFIRMED   # 已确认：不允许编辑和删除
DocStatus.COMPLETED   # 已完成：只读
DocStatus.CANCELLED   # 已取消：只读
DocStatus.REJECTED    # 已拒绝：允许编辑（重新提交）
```

**状态流转规则**：

```python
from src.database.document_status import DocumentStateMachine

# 允许的状态转换
DRAFT → CONFIRMED, CANCELLED, REJECTED
CONFIRMED → COMPLETED, CANCELLED
REJECTED → CONFIRMED, CANCELLED
COMPLETED → 终态（不可转换）
CANCELLED → 终态（不可转换）
```

**使用示例**：

```python
from src.database.status_mixins import DocumentStatusMixin
from src.database.document_status import DocStatus

class Inbound(DocumentStatusMixin, DataTableMixin, table=True):
    """入库单"""
    __tablename__ = "inbounds"

    doc_status: str = Field(default=DocStatus.DRAFT)
    # 自动获得 validate_document_status(operation) 方法
```

**验证规则**：

| 状态 | 允许编辑 | 允许删除 | 说明 |
|------|---------|---------|------|
| DRAFT | ✅ | ✅ | 草稿状态，可以自由修改 |
| CONFIRMED | ❌ | ❌ | 已确认，不允许修改和删除 |
| COMPLETED | ❌ | ❌ | 已完成，只读 |
| CANCELLED | ❌ | ❌ | 已取消，只读 |
| REJECTED | ✅ | ❌ | 已拒绝，允许编辑重新提交 |

### 2. ShelfStatusMixin（货架状态）

**适用场景**：货架、库位等存储位置

**状态定义**：

```python
from src.database.shelf_status import ShelfStatus

ShelfStatus.AVAILABLE   # 可用：可以存放货物
ShelfStatus.OCCUPIED    # 占用：已存放货物
ShelfStatus.LOCKED      # 锁定：不允许操作
ShelfStatus.MAINTENANCE # 维护：维护中
```

**使用示例**：

```python
from src.database.status_mixins import ShelfStatusMixin

class Shelf(ShelfStatusMixin, DataTableMixin, table=True):
    """货架"""
    __tablename__ = "shelves"

    shelf_status: str = Field(default=ShelfStatus.AVAILABLE)
    # 自动获得 validate_shelf_status(operation) 方法
```

**验证规则**：

| 状态 | 允许编辑 | 允许删除 | 说明 |
|------|---------|---------|------|
| AVAILABLE | ✅ | ✅ | 可用状态 |
| OCCUPIED | ✅ | ❌ | 占用状态，不允许删除 |
| LOCKED | ❌ | ❌ | 锁定状态，不允许操作 |
| MAINTENANCE | ✅ | ❌ | 维护状态，允许编辑 |

### 3. ContainerStatusMixin（容器状态）

**适用场景**：托盘、周转箱等容器

**状态定义**：

```python
from src.database.container_status import ContainerStatus

ContainerStatus.EMPTY      # 空闲：可以使用
ContainerStatus.IN_USE     # 使用中：已分配
ContainerStatus.DAMAGED    # 损坏：需要维修
ContainerStatus.SCRAPPED   # 报废：不可使用
```

**使用示例**：

```python
from src.database.status_mixins import ContainerStatusMixin

class Container(ContainerStatusMixin, DataTableMixin, table=True):
    """容器"""
    __tablename__ = "containers"

    container_status: str = Field(default=ContainerStatus.EMPTY)
    # 自动获得 validate_container_status(operation) 方法
```

**验证规则**：

| 状态 | 允许编辑 | 允许删除 | 说明 |
|------|---------|---------|------|
| EMPTY | ✅ | ✅ | 空闲状态 |
| IN_USE | ✅ | ❌ | 使用中，不允许删除 |
| DAMAGED | ✅ | ❌ | 损坏状态，允许编辑 |
| SCRAPPED | ❌ | ✅ | 报废状态，允许删除 |

### 4. MaterialStatusMixin（物料状态）

**适用场景**：物料、产品等库存物品

**状态定义**：

```python
from src.database.material_status import MaterialStatus

MaterialStatus.NORMAL      # 正常：可以使用
MaterialStatus.QUARANTINE  # 隔离：待检验
MaterialStatus.EXPIRED     # 过期：不可使用
MaterialStatus.DAMAGED     # 损坏：不可使用
```

**使用示例**：

```python
from src.database.status_mixins import MaterialStatusMixin

class Material(MaterialStatusMixin, DataTableMixin, table=True):
    """物料"""
    __tablename__ = "materials"

    material_status: str = Field(default=MaterialStatus.NORMAL)
    # 自动获得 validate_material_status(operation) 方法
```

**验证规则**：

| 状态 | 允许编辑 | 允许删除 | 说明 |
|------|---------|---------|------|
| NORMAL | ✅ | ✅ | 正常状态 |
| QUARANTINE | ✅ | ❌ | 隔离状态，不允许删除 |
| EXPIRED | ✅ | ✅ | 过期状态，允许删除 |
| DAMAGED | ✅ | ✅ | 损坏状态，允许删除 |

## 自动注册机制

BaseRepository 在初始化时会自动检测模型中的 `validate_xxx_status()` 方法并注册为 Hook：

```python
# BaseRepository.__init__() 中的逻辑
def _register_status_validation_hooks(self):
    """自动注册状态验证 Hook"""
    # 检测 validate_document_status
    if hasattr(self.model, "validate_document_status"):
        self.add_hook(
            HookType.BEFORE_UPDATE,
            self._validate_status_before_update,
            priority=0,
        )
        self.add_hook(
            HookType.BEFORE_DELETE,
            self._validate_status_before_delete,
            priority=0,
        )
```

## 状态验证工作流程

### 更新操作

```
更新请求
    ↓
BEFORE_UPDATE Hook
    ↓
validate_xxx_status("edit")
    ↓
状态允许？
    ├─ 是 → 继续更新
    └─ 否 → 抛出 ValueError("当前状态 [xxx] 不允许修改")
```

### 删除操作

```
删除请求
    ↓
BEFORE_DELETE Hook
    ↓
validate_xxx_status("delete")
    ↓
状态允许？
    ├─ 是 → 继续删除
    └─ 否 → 抛出 ValueError("当前状态 [xxx] 不允许删除")
```

## 使用示例

### 单据状态验证

```python
from src.database.status_mixins import DocumentStatusMixin
from src.database.document_status import DocStatus

class Inbound(DocumentStatusMixin, DataTableMixin, table=True):
    """入库单"""
    __tablename__ = "inbounds"

    doc_status: str = Field(default=DocStatus.DRAFT)
    inbound_no: str = Field(max_length=50)
    quantity: int = Field(ge=0)

# 使用示例
from src.app.warehousing.services import inbound_service

# 1. 创建草稿单据
inbound = await inbound_service.create(db, cache, {
    "doc_status": DocStatus.DRAFT,
    "inbound_no": "IN001",
    "quantity": 100,
})

# 2. 更新草稿单据 - 成功 ✅
await inbound_service.update(db, cache, inbound.id, {
    "quantity": 200,
})

# 3. 确认单据
await inbound_service.update(db, cache, inbound.id, {
    "doc_status": DocStatus.CONFIRMED,
})

# 4. 尝试更新已确认单据 - 失败 ❌
try:
    await inbound_service.update(db, cache, inbound.id, {
        "quantity": 300,
    })
except ValueError as e:
    print(e)  # 当前状态 [confirmed] 不允许修改

# 5. 尝试删除已确认单据 - 失败 ❌
try:
    await inbound_service.delete(db, cache, inbound.id)
except ValueError as e:
    print(e)  # 当前状态 [confirmed] 不允许删除
```

### 货架状态验证

```python
from src.database.status_mixins import ShelfStatusMixin
from src.database.shelf_status import ShelfStatus

class Shelf(ShelfStatusMixin, DataTableMixin, table=True):
    """货架"""
    __tablename__ = "shelves"

    shelf_status: str = Field(default=ShelfStatus.AVAILABLE)
    shelf_code: str = Field(max_length=20)

# 使用示例
# 1. 创建货架
shelf = await shelf_service.create(db, cache, {
    "shelf_status": ShelfStatus.AVAILABLE,
    "shelf_code": "A-01-01",
})

# 2. 占用货架
await shelf_service.update(db, cache, shelf.id, {
    "shelf_status": ShelfStatus.OCCUPIED,
})

# 3. 尝试删除占用的货架 - 失败 ❌
try:
    await shelf_service.delete(db, cache, shelf.id)
except ValueError as e:
    print(e)  # 当前状态 [occupied] 不允许删除

# 4. 锁定货架
await shelf_service.update(db, cache, shelf.id, {
    "shelf_status": ShelfStatus.LOCKED,
})

# 5. 尝试更新锁定的货架 - 失败 ❌
try:
    await shelf_service.update(db, cache, shelf.id, {
        "shelf_code": "A-01-02",
    })
except ValueError as e:
    print(e)  # 当前状态 [locked] 不允许修改
```

## 自定义状态 Mixin

### 创建自定义状态 Mixin

```python
class CustomStatusMixin:
    """自定义状态 Mixin"""

    status: str  # 状态字段

    def validate_custom_status(self, operation: str) -> None:
        """
        验证自定义状态

        Args:
            operation: 操作类型（"edit" 或 "delete"）

        Raises:
            ValueError: 状态不允许操作
        """
        if operation == "edit":
            # 编辑验证规则
            if self.status == "locked":
                raise ValueError(f"当前状态 [{self.status}] 不允许修改")

        elif operation == "delete":
            # 删除验证规则
            if self.status != "draft":
                raise ValueError(f"当前状态 [{self.status}] 不允许删除")
```

### 使用自定义状态 Mixin

```python
class CustomModel(CustomStatusMixin, DataTableMixin, table=True):
    """自定义模型"""
    __tablename__ = "custom_models"

    status: str = Field(default="draft")
    # BaseRepository 自动检测并注册 validate_custom_status
```

## 状态机管理

### DocumentStateMachine（单据状态机）

```python
from src.database.document_status import DocumentStateMachine, DocStatus

# 检查状态转换是否允许
is_allowed = DocumentStateMachine.can_transition(
    from_status=DocStatus.DRAFT,
    to_status=DocStatus.CONFIRMED,
)  # True

# 获取允许的下一状态
next_statuses = DocumentStateMachine.get_allowed_transitions(
    DocStatus.DRAFT
)  # [DocStatus.CONFIRMED, DocStatus.CANCELLED, DocStatus.REJECTED]

# 验证状态转换
try:
    DocumentStateMachine.validate_transition(
        from_status=DocStatus.COMPLETED,
        to_status=DocStatus.DRAFT,
    )
except ValueError as e:
    print(e)  # 不允许从 completed 转换到 draft
```

### 状态转换图

```
DRAFT (草稿)
    ├─→ CONFIRMED (已确认)
    ├─→ CANCELLED (已取消) [终态]
    └─→ REJECTED (已拒绝)

CONFIRMED (已确认)
    ├─→ COMPLETED (已完成) [终态]
    └─→ CANCELLED (已取消) [终态]

REJECTED (已拒绝)
    ├─→ CONFIRMED (已确认)
    └─→ CANCELLED (已取消) [终态]

COMPLETED (已完成) [终态]
    └─ 不可转换

CANCELLED (已取消) [终态]
    └─ 不可转换
```

## API 响应

### 状态验证失败

```json
{
  "code": 400,
  "message": "当前状态 [confirmed] 不允许修改",
  "data": null
}
```

### 状态转换失败

```json
{
  "code": 400,
  "message": "不允许从 completed 转换到 draft",
  "data": null
}
```

## 最佳实践

### 1. 选择合适的状态 Mixin

```python
# 单据类业务 → DocumentStatusMixin
class Inbound(DocumentStatusMixin, DataTableMixin, table=True):
    doc_status: str = Field(default=DocStatus.DRAFT)

# 资源类业务 → ShelfStatusMixin / ContainerStatusMixin
class Shelf(ShelfStatusMixin, DataTableMixin, table=True):
    shelf_status: str = Field(default=ShelfStatus.AVAILABLE)

# 物料类业务 → MaterialStatusMixin
class Material(MaterialStatusMixin, DataTableMixin, table=True):
    material_status: str = Field(default=MaterialStatus.NORMAL)
```

### 2. 状态字段命名

```python
# ✅ 正确：使用 Mixin 约定的字段名
class Inbound(DocumentStatusMixin, DataTableMixin, table=True):
    doc_status: str  # DocumentStatusMixin 要求

# ❌ 错误：使用其他字段名
class Inbound(DocumentStatusMixin, DataTableMixin, table=True):
    status: str  # 不会被自动检测
```

### 3. 状态转换在 Service 层

```python
class InboundService(BaseService[Inbound, InboundRepository]):
    async def confirm_inbound(
        self,
        db: AsyncSession,
        cache: CacheService,
        inbound_id: int
    ) -> Inbound:
        """确认入库单"""
        # 1. 获取入库单
        inbound = await self.get_by_id(db, cache, inbound_id)

        # 2. 验证状态转换
        from src.database.document_status import DocumentStateMachine
        DocumentStateMachine.validate_transition(
            inbound.doc_status,
            DocStatus.CONFIRMED,
        )

        # 3. 更新状态
        return await self.update(
            db,
            cache,
            inbound_id,
            {"doc_status": DocStatus.CONFIRMED}
        )
```

### 4. 状态验证与业务逻辑分离

```python
# ✅ 正确：状态验证在 Mixin 中，业务逻辑在 Service 中
class InboundService(BaseService[Inbound, InboundRepository]):
    async def confirm_inbound(self, db, cache, inbound_id: int):
        # 业务逻辑
        inbound = await self.get_by_id(db, cache, inbound_id)

        # 检查库存
        await self._check_inventory(db, inbound)

        # 更新状态（状态验证自动执行）
        return await self.update(
            db,
            cache,
            inbound_id,
            {"doc_status": DocStatus.CONFIRMED}
        )

# ❌ 错误：状态验证和业务逻辑混在一起
class InboundService(BaseService[Inbound, InboundRepository]):
    async def confirm_inbound(self, db, cache, inbound_id: int):
        inbound = await self.get_by_id(db, cache, inbound_id)

        # 手动验证状态
        if inbound.doc_status != DocStatus.DRAFT:
            raise ValueError("只有草稿状态可以确认")

        # 业务逻辑
        await self._check_inventory(db, inbound)

        return await self.update(db, cache, inbound_id, {...})
```

## 常见错误

### 错误 1：字段名不匹配

```python
# ❌ 错误：字段名不匹配
class Inbound(DocumentStatusMixin, DataTableMixin, table=True):
    status: str  # 应该是 doc_status

# ✅ 正确：使用约定的字段名
class Inbound(DocumentStatusMixin, DataTableMixin, table=True):
    doc_status: str
```

### 错误 2：忘记设置默认值

```python
# ❌ 错误：没有默认值
class Inbound(DocumentStatusMixin, DataTableMixin, table=True):
    doc_status: str

# ✅ 正确：设置默认值
class Inbound(DocumentStatusMixin, DataTableMixin, table=True):
    doc_status: str = Field(default=DocStatus.DRAFT)
```

### 错误 3：在 API 层验证状态

```python
# ❌ 错误：在 API 层验证
@router.put("/inbounds/{id}")
async def update_inbound(id: int, data: InboundUpdate):
    inbound = await inbound_service.get_by_id(db, cache, id)
    if inbound.doc_status != DocStatus.DRAFT:
        raise ValueError("只有草稿状态可以修改")
    return await inbound_service.update(db, cache, id, data)

# ✅ 正确：状态验证自动执行
@router.put("/inbounds/{id}")
async def update_inbound(id: int, data: InboundUpdate):
    return await inbound_service.update(db, cache, id, data)
    # 状态验证在 Repository 的 Hook 中自动执行
```

## 总结

状态验证系统的优势：
- ✅ 自动检测和注册
- ✅ 统一的验证规则
- ✅ 业务逻辑分离
- ✅ 易于扩展和维护
- ✅ 符合 WMS/WES 业务场景

状态验证系统的最佳实践：
- 选择合适的状态 Mixin
- 使用约定的字段名
- 状态转换在 Service 层
- 状态验证与业务逻辑分离
- 使用状态机管理状态转换
