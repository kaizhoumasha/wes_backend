# Hook 系统详解

Hook 系统是 WES Backend 的核心扩展机制，允许在 Repository 的 CRUD 操作前后插入自定义逻辑，无需修改基类代码。

## Hook 系统架构

### 核心组件

```
HookSystem (Hook 管理器)
    ↓
HookType (Hook 类型枚举)
    ↓
HookContext (Hook 上下文)
    ↓
Hook Function (Hook 函数)
```

### Hook 类型

```python
from src.database.base_repository import HookType

# 创建操作
HookType.BEFORE_CREATE  # 创建前
HookType.AFTER_CREATE   # 创建后

# 更新操作
HookType.BEFORE_UPDATE  # 更新前
HookType.AFTER_UPDATE   # 更新后

# 删除操作
HookType.BEFORE_DELETE  # 删除前
HookType.AFTER_DELETE   # 删除后
```

### Hook 上下文

```python
@dataclass
class HookContext:
    """Hook 执行上下文"""
    hook_type: HookType          # Hook 类型
    instance: Any | None         # 模型实例
    params: dict[str, Any]       # 操作参数
    db: AsyncSession            # 数据库会话
```

## 自动注册的 Hook

BaseRepository 在初始化时会自动检测并注册以下 Hook：

### 1. 状态验证 Hook

**检测规则**：检测模型中的 `validate_xxx_status()` 方法

**自动注册**：
- `BEFORE_UPDATE` - 更新前验证状态
- `BEFORE_DELETE` - 删除前验证状态

**示例**：

```python
from src.database.status_mixins import DocumentStatusMixin

class Inbound(DocumentStatusMixin, DataTableMixin, table=True):
    doc_status: str = Field(default="draft")
    # 自动获得 validate_document_status(operation) 方法

# BaseRepository 自动注册：
# - BEFORE_UPDATE: validate_document_status("edit")
# - BEFORE_DELETE: validate_document_status("delete")
```

**工作流程**：

```
更新操作 → BEFORE_UPDATE Hook → validate_document_status("edit")
                                    ↓
                            状态允许 → 继续更新
                            状态不允许 → 抛出 ValueError
```

### 2. 审计字段 Hook

**检测规则**：检测模型中的 `created_by` / `updated_by` 字段

**自动注册**：
- `BEFORE_CREATE` - 创建前填充 `created_by`
- `BEFORE_UPDATE` - 更新前填充 `updated_by`

**示例**：

```python
class User(UserBase, DataTableMixin, EnterpriseMixin, table=True):
    # EnterpriseMixin 提供：
    # - created_by: int | None
    # - updated_by: int | None
    pass

# BaseRepository 自动注册：
# - BEFORE_CREATE: 填充 created_by
# - BEFORE_UPDATE: 填充 updated_by
```

**数据来源**：

```python
# 从 request.state 获取当前用户 ID
user_id = getattr(request.state, "user_id", None)
```

### 3. 乐观锁 Hook

**检测规则**：检测模型中的 `version` 字段

**自动注册**：
- `BEFORE_UPDATE` - 更新前验证版本号
- `AFTER_UPDATE` - 更新后递增版本号

**示例**：

```python
from src.core.mixins import OptimisticLockMixin

class Inventory(InventoryBase, DataTableMixin, OptimisticLockMixin, table=True):
    # OptimisticLockMixin 提供：
    # - version: int
    pass

# BaseRepository 自动注册：
# - BEFORE_UPDATE: 验证 version 是否匹配
# - AFTER_UPDATE: version += 1
```

**工作流程**：

```
更新操作 → BEFORE_UPDATE Hook → 验证 version
                                    ↓
                            版本匹配 → 继续更新 → AFTER_UPDATE Hook → version += 1
                            版本不匹配 → 抛出 OptimisticLockException
```

### 4. 审计日志 Hook

**检测规则**：检测模型是否继承 `AuditableMixin`

**自动注册**：
- `AFTER_CREATE` - 创建后记录日志
- `AFTER_UPDATE` - 更新后记录日志
- `AFTER_DELETE` - 删除后记录日志

**示例**：

```python
from src.core.mixins import AuditableMixin

class Order(OrderBase, DataTableMixin, AuditableMixin, table=True):
    # AuditableMixin 不是字段，是行为
    pass

# BaseRepository 自动注册：
# - AFTER_CREATE: 记录创建日志
# - AFTER_UPDATE: 记录更新日志
# - AFTER_DELETE: 记录删除日志
```

**日志内容**：

```python
{
    "table_name": "orders",
    "record_id": 1,
    "operation": "create",  # create/update/delete
    "user_id": 123,
    "changes": {"order_no": "ORD001", "total_amount": 100.0},
    "created_at": "2024-01-01T12:00:00"
}
```

## 自定义 Hook

### 在 Repository 中添加 Hook

```python
from src.database.base_repository import BaseRepository, HookType, HookContext

class UserRepository(BaseRepository[User]):
    def __init__(self):
        super().__init__(User)
        # 添加自定义 Hook
        self.add_hook(
            HookType.BEFORE_CREATE,
            self._validate_username,
            priority=0,  # 优先级（数字越小越先执行）
        )

    async def _validate_username(self, context: HookContext) -> None:
        """创建前验证用户名"""
        data = context.params.get("data", {})
        username = data.get("username")

        if username and len(username) < 3:
            raise ValueError("用户名至少 3 个字符")

        # 检查用户名是否已存在
        existing = await self.get_by_field(context.db, "username", username)
        if existing:
            raise ValueError(f"用户名 {username} 已存在")
```

### 在 Service 中添加 Hook

```python
from src.core.base_service import BaseService
from src.database.base_repository import HookType, HookContext

class UserService(BaseService[User, UserRepository]):
    def __init__(self):
        super().__init__(user_repository)
        # 添加自定义 Hook
        self.add_hook(
            HookType.BEFORE_CREATE,
            self._hash_password,
            priority=0,
        )

    async def _hash_password(self, context: HookContext) -> None:
        """创建前自动哈希密码"""
        data = context.params.get("data", {})
        if "password" in data:
            from src.core.security import hash_password
            data["hashed_password"] = hash_password(data.pop("password"))
```

## Hook 执行顺序

### 优先级规则

Hook 按优先级（priority）从小到大执行：

```python
# priority=0 的 Hook 先执行
self.add_hook(HookType.BEFORE_CREATE, hook1, priority=0)
self.add_hook(HookType.BEFORE_CREATE, hook2, priority=10)
self.add_hook(HookType.BEFORE_CREATE, hook3, priority=20)

# 执行顺序：hook1 → hook2 → hook3
```

### 自动注册的 Hook 优先级

```python
# 状态验证 Hook: priority=0
# 审计字段 Hook: priority=10
# 乐观锁 Hook: priority=20
# 审计日志 Hook: priority=100
```

### 完整执行流程

```
创建操作：
1. BEFORE_CREATE Hook (priority 从小到大)
   - 状态验证 (priority=0)
   - 审计字段填充 (priority=10)
   - 自定义 Hook (priority=自定义)
2. 执行数据库插入
3. AFTER_CREATE Hook (priority 从小到大)
   - 审计日志记录 (priority=100)
   - 自定义 Hook (priority=自定义)

更新操作：
1. BEFORE_UPDATE Hook (priority 从小到大)
   - 状态验证 (priority=0)
   - 审计字段填充 (priority=10)
   - 乐观锁验证 (priority=20)
   - 自定义 Hook (priority=自定义)
2. 执行数据库更新
3. AFTER_UPDATE Hook (priority 从小到大)
   - 乐观锁递增 (priority=20)
   - 审计日志记录 (priority=100)
   - 自定义 Hook (priority=自定义)

删除操作：
1. BEFORE_DELETE Hook (priority 从小到大)
   - 状态验证 (priority=0)
   - 自定义 Hook (priority=自定义)
2. 执行数据库删除
3. AFTER_DELETE Hook (priority 从小到大)
   - 审计日志记录 (priority=100)
   - 自定义 Hook (priority=自定义)
```

## Hook 最佳实践

### 1. Hook 函数签名

```python
async def hook_function(context: HookContext) -> None:
    """
    Hook 函数必须：
    - 是异步函数（async def）
    - 接受 HookContext 参数
    - 返回 None
    """
    pass
```

### 2. 访问上下文数据

```python
async def my_hook(context: HookContext) -> None:
    # 访问 Hook 类型
    hook_type = context.hook_type

    # 访问模型实例（AFTER_* Hook 中可用）
    instance = context.instance

    # 访问操作参数
    data = context.params.get("data", {})
    id = context.params.get("id")

    # 访问数据库会话
    db = context.db
```

### 3. 修改数据

```python
async def modify_data_hook(context: HookContext) -> None:
    """BEFORE_CREATE/BEFORE_UPDATE Hook 可以修改数据"""
    data = context.params.get("data", {})

    # 修改数据
    data["modified_field"] = "new_value"

    # 添加新字段
    data["computed_field"] = calculate_value()
```

### 4. 抛出异常

```python
async def validation_hook(context: HookContext) -> None:
    """Hook 中抛出异常会中断操作"""
    data = context.params.get("data", {})

    if not is_valid(data):
        raise ValueError("数据验证失败")
        # 操作会被中断，数据库不会执行
```

### 5. 调用其他 Service

```python
async def call_other_service_hook(context: HookContext) -> None:
    """Hook 中可以调用其他 Service"""
    # 延迟导入避免循环依赖
    from src.app.other.services import other_service

    data = context.params.get("data", {})
    await other_service.do_something(context.db, data)
```

## 常见使用场景

### 1. 数据验证

```python
async def validate_email(context: HookContext) -> None:
    """验证邮箱格式"""
    data = context.params.get("data", {})
    email = data.get("email")

    if email and not is_valid_email(email):
        raise ValueError("邮箱格式不正确")
```

### 2. 数据转换

```python
async def normalize_phone(context: HookContext) -> None:
    """规范化手机号"""
    data = context.params.get("data", {})
    phone = data.get("phone")

    if phone:
        # 移除空格和短横线
        data["phone"] = phone.replace(" ", "").replace("-", "")
```

### 3. 计算字段

```python
async def calculate_total(context: HookContext) -> None:
    """计算订单总额"""
    data = context.params.get("data", {})
    quantity = data.get("quantity", 0)
    price = data.get("price", 0)

    data["total_amount"] = quantity * price
```

### 4. 关联数据更新

```python
async def update_inventory(context: HookContext) -> None:
    """出库后更新库存"""
    instance = context.instance  # 出库单

    # 延迟导入
    from src.app.warehousing.services import inventory_service

    await inventory_service.decrease_stock(
        context.db,
        instance.product_id,
        instance.quantity
    )
```

### 5. 发送通知

```python
async def send_notification(context: HookContext) -> None:
    """订单创建后发送通知"""
    instance = context.instance  # 订单

    # 延迟导入
    from src.app.notification.services import notification_service

    await notification_service.send_order_created(
        context.db,
        instance.id
    )
```

## Hook 调试

### 启用 Hook 日志

```python
import logging

# 在 Repository 中启用日志
class UserRepository(BaseRepository[User]):
    def __init__(self):
        super().__init__(User)
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG)
```

### Hook 执行追踪

```python
async def debug_hook(context: HookContext) -> None:
    """调试 Hook"""
    print(f"Hook Type: {context.hook_type}")
    print(f"Instance: {context.instance}")
    print(f"Params: {context.params}")
```

## 常见错误

### 错误 1：Hook 函数不是异步

```python
# ❌ 错误：同步函数
def my_hook(context: HookContext) -> None:
    pass

# ✅ 正确：异步函数
async def my_hook(context: HookContext) -> None:
    pass
```

### 错误 2：在 BEFORE Hook 中访问 instance

```python
# ❌ 错误：BEFORE Hook 中 instance 为 None
async def my_hook(context: HookContext) -> None:
    instance = context.instance  # None
    print(instance.id)  # AttributeError

# ✅ 正确：从 params 中获取数据
async def my_hook(context: HookContext) -> None:
    data = context.params.get("data", {})
    print(data.get("id"))
```

### 错误 3：忘记处理异常

```python
# ❌ 错误：异常未处理
async def my_hook(context: HookContext) -> None:
    result = await some_operation()
    # 如果 some_operation 抛出异常，会中断整个操作

# ✅ 正确：处理异常
async def my_hook(context: HookContext) -> None:
    try:
        result = await some_operation()
    except Exception as e:
        logger.error(f"Hook 执行失败: {e}")
        # 决定是否重新抛出异常
```

## 总结

Hook 系统的优势：
- ✅ 无需修改基类代码
- ✅ 自动检测和注册
- ✅ 灵活的优先级控制
- ✅ 支持异步操作
- ✅ 易于测试和维护

Hook 系统的最佳实践：
- 优先使用自动注册的 Hook（状态验证、审计字段等）
- 自定义 Hook 保持简单和专注
- 使用优先级控制执行顺序
- 在 Hook 中抛出异常来中断操作
- 延迟导入避免循环依赖
