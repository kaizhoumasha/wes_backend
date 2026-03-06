# SQLModel 关系定义指南

本文档提供 SQLModel 关系定义的最佳实践和常见陷阱。

## 关系类型

### 一对多关系（One-to-Many）

**场景**：一个作业线（WorkLine）有多个设备（Device）

**定义方式**：

```python
from sqlmodel import Field, Relationship

# 父表（One 端）
class WorkLine(DataTableMixin, table=True):
    __tablename__ = "work_lines"

    line_code: str = Field(unique=True)
    line_name: str

    # 关系定义（返回列表）
    devices: list["Device"] = Relationship(
        back_populates="work_line",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )

# 子表（Many 端）
class Device(DataTableMixin, table=True):
    __tablename__ = "devices"

    device_code: str = Field(unique=True)
    device_name: str

    # 外键字段
    work_line_id: int | None = Field(
        default=None,
        foreign_key="wes_biz.work_lines.id"
    )

    # 关系定义（返回单个对象）
    # ✅ 正确：不使用 Union 类型
    work_line: "WorkLine" = Relationship(back_populates="devices")
```

### 多对多关系（Many-to-Many）

**场景**：用户（User）和角色（Role）的多对多关系

**定义方式**：

```python
# 关联表
class UserRole(SQLModel, table=True):
    __tablename__ = "user_roles"
    __schema__ = SchemaType.SYS.value

    user_id: int = Field(foreign_key="wes_sys.users.id", primary_key=True)
    role_id: int = Field(foreign_key="wes_sys.roles.id", primary_key=True)

# 用户表
class User(DataTableMixin, table=True):
    __tablename__ = "users"

    username: str = Field(unique=True)

    # 多对多关系
    roles: list["Role"] = Relationship(
        back_populates="users",
        link_model=UserRole
    )

# 角色表
class Role(DataTableMixin, table=True):
    __tablename__ = "roles"

    role_code: str = Field(unique=True)

    # 多对多关系
    users: list["User"] = Relationship(
        back_populates="roles",
        link_model=UserRole
    )
```

## 常见陷阱

### 🔴 CRITICAL：不要在 Relationship 中使用 Union 类型

**错误**：
```python
class Device(DataTableMixin, table=True):
    work_line: "WorkLine | None" = Relationship(...)  # ❌ 错误
```

**症状**：
```
sqlalchemy.exc.InvalidRequestError: When initializing mapper Mapper[Device(devices)],
expression 'WorkLine | None' failed to locate a name ('None').
```

**原因**：SQLAlchemy 无法解析 Union 类型注解（`WorkLine | None`）。

**正确**：
```python
class Device(DataTableMixin, table=True):
    work_line: "WorkLine" = Relationship(...)  # ✅ 正确
```

**说明**：
- Relationship 字段的可选性由外键字段（`work_line_id`）控制
- 如果 `work_line_id` 可以为 None，则关系也是可选的
- 不需要在类型注解中使用 `| None`

### 🟡 IMPORTANT：外键字段命名规范

**推荐命名**：
```python
# ✅ 推荐：{关系名}_id
work_line_id: int | None = Field(foreign_key="wes_biz.work_lines.id")
work_line: "WorkLine" = Relationship(...)

# ✅ 也可以：{表名}_id
workline_id: int | None = Field(foreign_key="wes_biz.work_lines.id")
workline: "WorkLine" = Relationship(...)
```

**避免混淆**：
```python
# ❌ 避免：外键字段和关系字段名称不一致
line_id: int | None = Field(foreign_key="wes_biz.work_lines.id")
work_line: "WorkLine" = Relationship(...)  # 容易混淆
```

### 🟡 IMPORTANT：back_populates 必须匹配

**错误**：
```python
class WorkLine(DataTableMixin, table=True):
    devices: list["Device"] = Relationship(back_populates="workline")  # ❌

class Device(DataTableMixin, table=True):
    work_line: "WorkLine" = Relationship(back_populates="devices")  # ❌
```

**症状**：
```
sqlalchemy.exc.InvalidRequestError: Could not determine relationship direction for
relationship 'Device.work_line' - foreign key columns are present in neither the
parent nor the child's mapped tables
```

**正确**：
```python
class WorkLine(DataTableMixin, table=True):
    devices: list["Device"] = Relationship(back_populates="work_line")  # ✅

class Device(DataTableMixin, table=True):
    work_line: "WorkLine" = Relationship(back_populates="devices")  # ✅
```

### 🟢 RECOMMENDED：使用字符串引用避免循环导入

**推荐**：
```python
# 使用字符串引用（Forward Reference）
class Device(DataTableMixin, table=True):
    work_line: "WorkLine" = Relationship(...)  # ✅ 字符串引用
```

**避免**：
```python
# 直接导入可能导致循环依赖
from src.app.workline.models import WorkLine

class Device(DataTableMixin, table=True):
    work_line: WorkLine = Relationship(...)  # ⚠️ 可能循环导入
```

## 级联删除策略

### cascade 参数

```python
# 1. 级联删除（删除父对象时删除所有子对象）
devices: list["Device"] = Relationship(
    back_populates="work_line",
    sa_relationship_kwargs={"cascade": "all, delete-orphan"}
)

# 2. 设置为 NULL（删除父对象时子对象的外键设为 NULL）
devices: list["Device"] = Relationship(
    back_populates="work_line",
    sa_relationship_kwargs={"cascade": "save-update, merge"}
)

# 3. 阻止删除（删除父对象时如果有子对象则报错）
# 不设置 cascade，数据库外键约束会阻止删除
devices: list["Device"] = Relationship(back_populates="work_line")
```

### 选择策略

| 场景 | 策略 | 示例 |
|------|------|------|
| 强依赖关系 | `all, delete-orphan` | 订单 → 订单明细 |
| 弱依赖关系 | `save-update, merge` | 作业线 → 设备 |
| 独立实体 | 不设置 cascade | 部门 → 员工 |

## 关系加载策略

### 自动加载（Schema 驱动）

```python
# Response Schema 中定义关系
class DeviceResponse(DeviceBase):
    id: int
    work_line: WorkLineResponse | None  # 自动加载关系

# Repository 自动识别并加载
device = await repo.get_by_id(db, id=1, schema=DeviceResponse)
# device.work_line 已加载，不会产生 N+1 查询
```

### 手动控制加载

```python
# 只加载特定关系
device = await repo.get_by_id(
    db,
    id=1,
    include_relations=True,
    relation_names=["work_line"]  # 只加载 work_line
)

# 控制加载深度
device = await repo.get_by_id(
    db,
    id=1,
    schema=DeviceResponse,
    max_depth=2,  # 最多加载 2 层关系
)
```

## 外键约束

### Schema 前缀

```python
# ✅ 正确：包含 Schema 前缀
work_line_id: int | None = Field(
    foreign_key="wes_biz.work_lines.id"  # schema.table.column
)

# ❌ 错误：缺少 Schema 前缀
work_line_id: int | None = Field(
    foreign_key="work_lines.id"  # 可能找不到表
)
```

### 索引优化

```python
# 外键字段自动创建索引
work_line_id: int | None = Field(
    foreign_key="wes_biz.work_lines.id",
    index=True  # 显式创建索引（可选，外键会自动创建）
)
```

## 完整示例

### 一对多关系（作业线 → 设备）

```python
from sqlmodel import Field, Relationship
from src.core.mixins import DataTableMixin, EnterpriseMixin, SoftDeleteMixin
from src.database.schema_conf import SchemaType

# 父表（WorkLine）
class WorkLine(DataTableMixin, EnterpriseMixin, SoftDeleteMixin, table=True):
    __tablename__ = "work_lines"
    __schema__ = SchemaType.BIZ.value

    line_code: str = Field(unique=True, index=True)
    line_name: str
    line_type: str

    # 一对多关系（返回列表）
    devices: list["Device"] = Relationship(
        back_populates="work_line",
        sa_relationship_kwargs={"cascade": "save-update, merge"}
    )

# 子表（Device）
class Device(DataTableMixin, EnterpriseMixin, SoftDeleteMixin, table=True):
    __tablename__ = "devices"
    __schema__ = SchemaType.BIZ.value

    device_code: str = Field(unique=True, index=True)
    device_name: str
    device_type: str

    # 外键字段（可选）
    work_line_id: int | None = Field(
        default=None,
        foreign_key="wes_biz.work_lines.id",
        index=True
    )

    # 多对一关系（返回单个对象）
    work_line: "WorkLine" = Relationship(back_populates="devices")
```

### Response Schema

```python
from datetime import datetime

class WorkLineResponse(WorkLineBase):
    id: int
    created_at: datetime
    updated_at: datetime
    devices: list["DeviceResponse"] = []  # 关联的设备列表

class DeviceResponse(DeviceBase):
    id: int
    created_at: datetime
    updated_at: datetime
    work_line: WorkLineResponse | None = None  # 关联的作业线
```

## 测试关系

```python
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

@pytest.mark.asyncio
async def test_relationship(db: AsyncSession):
    # 创建作业线
    work_line = WorkLine(line_code="SMT_AUTO_1", line_name="SMT自动线1")
    db.add(work_line)
    await db.flush()

    # 创建设备（关联作业线）
    device = Device(
        device_code="PDA_001",
        device_name="PDA设备1",
        work_line_id=work_line.id
    )
    db.add(device)
    await db.flush()

    # 测试关系加载
    await db.refresh(device, ["work_line"])
    assert device.work_line.line_code == "SMT_AUTO_1"

    await db.refresh(work_line, ["devices"])
    assert len(work_line.devices) == 1
    assert work_line.devices[0].device_code == "PDA_001"
```

## 参考资料

- **SQLModel 官方文档**：https://sqlmodel.tiangolo.com/tutorial/relationship-attributes/
- **SQLAlchemy 关系文档**：https://docs.sqlalchemy.org/en/20/orm/relationships.html
- **项目架构规范**：`CLAUDE.md`
- **故障排查指南**：`troubleshooting.md`
