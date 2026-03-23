# WES Backend 故障排查指南

本文档提供创建模块时常见问题的解决方案。

## 常见错误

### 1. TypeError: BaseRepository.__init__() missing 1 required positional argument: 'model'

**错误信息**：
```
TypeError: BaseRepository.__init__() missing 1 required positional argument: 'model'
```

**原因**：Repository 类缺少 `__init__` 方法，无法正确初始化。

**错误代码**：
```python
class DeviceRepository(BaseRepository[Device]):
    """设备数据访问层"""
    pass

# 创建单例时报错
device_repository = DeviceRepository()  # ❌ TypeError!
```

**正确代码**：
```python
class DeviceRepository(BaseRepository[Device]):
    """设备数据访问层"""

    def __init__(self) -> None:
        """初始化设备仓库"""
        super().__init__(Device)

# 创建单例
device_repository = DeviceRepository()  # ✅ 正确
```

**解决方案**：
1. 在 Repository 类中添加 `__init__` 方法
2. 调用 `super().__init__(Model)` 传递模型类
3. 确保类型注解正确（`BaseRepository[Device]`）

---

### 2. SQLAlchemy InvalidRequestError: failed to locate a name

**错误信息**：
```
sqlalchemy.exc.InvalidRequestError: When initializing mapper Mapper[Device(devices)],
expression 'WorkLine | None' failed to locate a name ('WorkLine | None')
```

**原因**：Relationship 的类型注解使用了 Union 类型（`WorkLine | None`），SQLAlchemy 无法解析字符串形式的 Union 类型。

**错误代码**：
```python
class Device(DeviceBase, DataTableMixin, table=True):
    # ❌ 错误：Union 类型注解
    work_line: "WorkLine | None" = Relationship(
        sa_relationship_kwargs={"lazy": "selectin"}
    )
```

**正确代码**：
```python
class Device(DeviceBase, DataTableMixin, table=True):
    # ✅ 正确：不使用 Union 类型
    work_line: "WorkLine" = Relationship(
        sa_relationship_kwargs={"lazy": "selectin"}
    )
```

**解决方案**：
1. 在 Relationship 类型注解中不要使用 Union 类型（`| None`）
2. 使用简单的字符串类型注解（`"WorkLine"`）
3. 可空性由数据库外键字段的 `nullable=True` 控制

**注意**：
- 外键字段可以是可选的：`work_line_id: int | None = None`
- 但 Relationship 类型注解不应该包含 `| None`

---

### 3. Alembic: Can't locate revision / No changes detected

**错误信息**：
```
INFO  [alembic.autogenerate] No changes detected
```
或
```
ERROR [alembic.util.messaging] Can't locate revision identified by 'xxx'
```

**原因**：新创建的模型没有在 `migrations/env.py` 中导入，Alembic 无法检测到新表。

**解决方案**：

1. 打开 `migrations/env.py` 文件
2. 在模型导入区域添加新模型的导入：

```python
# 导入所有模型以确保它们被 SQLModel.metadata 识别
from src.app.admin.models import Permission, Role, User  # noqa: F401
from src.app.workline.models import WorkLine  # noqa: F401  # ✅ 添加这行
from src.app.device.models import Device  # noqa: F401      # ✅ 添加这行
```

3. 重新生成迁移脚本：
```bash
uv run alembic revision --autogenerate -m "create_xxx_table"
```

**检查清单**：
- [ ] 在 `migrations/env.py` 中添加了模型导入
- [ ] 导入语句包含 `# noqa: F401` 注释
- [ ] 模型类名正确（不是 Base 类）
- [ ] 重新运行 alembic revision 命令

---

### 4. BaseService.get_by_id() missing 1 required positional argument: 'id'

**错误信息**：
```
TypeError: BaseService.get_by_id() missing 1 required positional argument: 'id'
```

**原因**：BaseService 的方法需要 `cache` 参数，但调用时没有传递。

**错误代码**：
```python
# ❌ 错误：缺少 cache 参数
user = await user_service.get_by_id(db, user_id)
```

**正确代码**：
```python
# ✅ 正确：传递 cache 参数（可以是 None）
user = await user_service.get_by_id(db, cache, user_id)

# 或者在没有缓存的情况下
user = await user_service.get_by_id(db, None, user_id)
```

**BaseService 方法签名**：
```python
# get_by_id
async def get_by_id(
    self, db: AsyncSession, cache: object, id: int,
    max_depth: int = 2, include_deleted: bool = False
) -> M | None

# get_list
async def get_list(
    self, db: AsyncSession, cache: object | None,
    limit: int = 10, offset: int = 0, ...
) -> tuple[int, list[M]]

# update
async def update(
    self, db: AsyncSession, id: int, data: dict[str, Any],
    cache: object | None = None
) -> M | None

# delete
async def delete(
    self, db: AsyncSession, id: int,
    cache: object | None = None
) -> bool
```

**解决方案**：
1. 在调用 Service 方法时，始终传递 `cache` 参数
2. 如果没有缓存实例，传递 `None`
3. 在 API 路由中，使用 `CacheDep` 依赖注入获取缓存实例

---

### 5. OptimisticLockException: 缺少 version 字段

**错误信息**：
```
OptimisticLockException: WorkLine 更新失败：缺少 version 字段，请刷新数据后重试
```

**原因**：模型使用了 `OptimisticLockMixin`（通过 DataTableMixin 继承），更新时必须包含 `version` 字段。

**错误代码**：
```python
# ❌ 错误：缺少 version 字段
await service.update(db, workline.id, {
    "capacity": 150,
    "description": "更新后的描述"
}, None)
```

**正确代码**：
```python
# ✅ 正确：包含 version 字段
await service.update(db, workline.id, {
    "capacity": 150,
    "description": "更新后的描述",
    "version": workline.version  # 必须包含当前版本号
}, None)
```

**解决方案**：
1. 在更新前先查询对象获取当前 `version`
2. 在更新数据中包含 `version` 字段
3. 如果版本不匹配，会抛出 `OptimisticLockException`

**最佳实践**：
```python
# 1. 查询对象
obj = await service.get_by_id(db, None, obj_id)

# 2. 更新时包含 version
await service.update(db, obj_id, {
    "field1": "new_value",
    "version": obj.version  # 使用查询到的版本号
}, None)
```

---

### 6. IntegrityError: duplicate key value violates unique constraint

**错误信息**：
```
IntegrityError: duplicate key value violates unique constraint "ix_wes_biz_devices_device_code"
```

**原因**：尝试插入重复的唯一字段值（如 `device_code`）。

**解决方案**：
1. 检查数据库中是否已存在相同的记录
2. 使用 `get_by_field` 方法先查询是否存在
3. 考虑使用软删除，避免硬删除后无法重用编码

**示例**：
```python
# 检查是否存在
existing = await repo.get_by_field(db, "device_code", "PDA_001")
if existing:
    raise ValueError(f"设备编码 {device_code} 已存在")

# 创建新记录
device = await repo.create(db, data)
```

---

### 7. 路由未注册：404 Not Found

**错误信息**：
```
404 Not Found: /api/devices
```

**原因**：新创建的模块路由没有在 `src/register.py` 中注册。

**解决方案**：

1. 打开 `src/register.py` 文件
2. 在 `register_routers` 函数中添加路由导入和注册：

```python
def register_routers(app: FastAPI) -> None:
    """注册路由"""
    from src.app.admin import router_v1 as admin_router
    from src.app.device import router_v1 as device_router  # ✅ 添加导入

    app.include_router(admin_router, prefix=settings.API_PATH)
    app.include_router(device_router, prefix=settings.API_PATH)  # ✅ 添加注册
```

3. 重启开发服务器

---

## 调试技巧

### 1. 检查模型是否正确注册

```python
# 在 Python REPL 中
from sqlmodel import SQLModel
print(SQLModel.metadata.tables.keys())
# 应该看到你的表名
```

### 2. 检查 Alembic 是否能检测到模型

```bash
# 生成迁移脚本（dry-run）
uv run alembic revision --autogenerate -m "test"
# 查看生成的脚本内容
```

### 3. 检查路由是否注册

```python
# 在 Python REPL 中
from src.register import app
routes = [route.path for route in app.routes]
print([r for r in routes if 'device' in r])
```

### 4. 检查数据库表结构

```bash
# 使用 psql 查看表结构
docker exec -i wes_postgres_dev psql -U wes_user -d wes_db -c "\d wes_biz.devices"
```

---

## 预防措施

### 创建模块后的检查清单

- [ ] Repository 包含 `__init__` 方法并调用 `super().__init__(Model)`
- [ ] Relationship 类型注解不使用 Union 类型（`| None`）
- [ ] 在 `migrations/env.py` 中添加了模型导入
- [ ] 在 `src/register.py` 中注册了路由
- [ ] 运行 `ruff format` 和 `ruff check` 检查代码
- [ ] 生成并执行数据库迁移
- [ ] 测试 CRUD 操作是否正常
- [ ] 更新操作包含 `version` 字段

---

## 获取帮助

如果遇到其他问题：

1. 查看项目的 `CLAUDE.md` 文档
2. 参考 `src/app/admin/` 中的示例代码
3. 检查 `src/database/base_repository.py` 的实现
4. 查看测试用例了解正确的使用方式
