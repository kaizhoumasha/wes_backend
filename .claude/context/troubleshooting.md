---
purpose: 故障排查指南
indexed_by: CLAUDE.md
tags: [troubleshooting, cache, n+1, architecture, import]
---

# 故障排查指南

> 详见 CLAUDE.md 的故障排查索引，本文档提供详细的排查步骤。

## 缓存问题

### 缓存未生效

**检查步骤**：
1. Redis 是否运行：`redis-cli ping`
2. 查看缓存键：`redis-cli KEYS "app:*"`
3. 查看 TTL：`redis-cli TTL app:user:detail:1`

### 权限缓存未更新

权限变更后手动清除缓存：

```python
from src.core.rbac import invalidate_user_permissions

await invalidate_user_permissions(cache, user_id)
```

## N+1 查询问题

**症状**：列表查询响应慢，数据库连接池耗尽。

**错误示例**：
```python
users = await repo.get_list(db)
for user in users:
    print(user.roles)  # 每次都查询数据库
```

**正确做法**：
```python
# 使用 Schema 自动加载
users = await repo.get_list(db, schema=UserResponse)
```

## 架构违规检测

### 检测命令

```bash
# 检查 API 层是否违规访问数据库
grep -r "from sqlalchemy import select" src/app/*/v1/
grep -r "db.execute(" src/app/*/v1/
grep -r "db.scalar(" src/app/*/v1/

# 检查是否有跨层访问
grep -r "from.*repositories import" src/app/*/v1/
```

### 修复方法

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

## ImportError 问题

### 症状

```
ImportError: cannot import name 'xxx_service' from 'src.app.xxx.services'
```

### 原因

模块的 `__init__.py` 没有导出新添加的类/函数。

### 解决方案

```python
# 在 services/__init__.py 中添加
from .xxx_service import XxxService, xxx_service

__all__ = [
    # ... 其他导出
    "XxxService",
    "xxx_service",
]
```

## Mixin 继承错误

### 症状

```
TypeError: Cannot create a consistent method resolution order
```

### 原因

重复继承导致 MRO 冲突。

### 解决方案

```python
# ❌ 错误：重复继承
class User(UserBase, AuditMixin, EnterpriseMixin, SoftDeleteMixin, table=True):

# ✅ 正确：EnterpriseMixin 已包含 AuditMixin
class User(UserBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):
```

## 时区相关问题

### 症状

- 时间戳计算结果与预期相差 8 小时
- API 响应时间缺少时区信息

### 检测命令

```bash
# 检查是否有 naive datetime 的 .timestamp() 调用
grep -r "\.timestamp()" src/ --include="*.py" | grep -v "now_utc" | grep -v "to_utc"

# 检查是否有直接使用 datetime.now(UTC)
grep -r "datetime.now(UTC)" src/ --include="*.py"
```

### 解决方案

```python
# ❌ 错误
cutoff_time = timezone.now_for_db().timestamp()

# ✅ 正确
cutoff_time = timezone.now_utc().timestamp()
```

## 状态验证失败

### 症状

```
ValueError: 当前状态 [confirmed] 不允许修改
```

### 原因

状态验证 Mixin 阻止了不允许的操作。

### 解决方案

检查状态流转规则：
- DRAFT → CONFIRMED, CANCELLED, REJECTED
- CONFIRMED → COMPLETED, CANCELLED
- REJECTED → CONFIRMED, CANCELLED
- COMPLETED → 终态
- CANCELLED → 终态

## 关键文件路径

### 核心框架

- `src/database/base_repository.py`：Repository 基类
- `src/core/base_service.py`：Service 基类
- `src/core/base_api.py`：API 基类
- `src/database/model_factory.py`：Schema 工厂

### Mixin 系统

- `src/core/mixins/__init__.py`：Mixin 导入
- `src/core/mixins/datatable.py`：DataTableMixin
- `src/core/mixins/soft_delete.py`：SoftDeleteMixin

### 查询和关系

- `src/core/query_builder.py`：查询构建器
- `src/core/schema_loader.py`：Schema 加载器

### 权限和认证

- `src/core/security.py`：JWT 认证
- `src/core/rbac.py`：RBAC 权限系统