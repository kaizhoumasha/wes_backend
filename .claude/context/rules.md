---
purpose: 开发规则详解
indexed_by: CLAUDE.md
tags: [rules, architecture, timezone, performance]
---

# 开发规则详解

> 详见 CLAUDE.md 的规则索引，本文档提供详细的规则说明。

## 分层架构规则（CRITICAL）

### 🚨 严格禁止的架构违规

| 违规行为 | 问题描述 | 后果 |
|----------|----------|------|
| **API → Repository** | 路由层直接访问 Repository | 跳过业务逻辑层、无缓存 |
| **API → Database** | 路由层直接执行 SQL | 无法复用、难测试、职责混乱 |
| **跨层访问** | 任何跳过中间层的直接调用 | 破坏分层、耦合度过高 |

### ✅ 正确的依赖方向

```
API 层 → Service 层 → Repository 层 → 数据库
```

### 📋 架构合规检查清单

- [ ] API 层**没有** `from sqlalchemy import select`
- [ ] API 层**没有** `db.execute()` 或 `db.scalar()`
- [ ] API 层**所有**数据操作都通过 `xxx_service.xxx()` 完成

### 🔴 典型违规案例

```python
# ❌ 错误：API 层直接访问数据库
from sqlalchemy import select

@router.get("/permissions")
async def get_permissions(db: AsyncSessionDep):
    result = await db.execute(select(Permission))
    return result.scalars().all()

# ✅ 正确：API 层调用 Service
@router.get("/permissions")
async def get_permissions(db: AsyncSessionDep):
    return await permission_service.get_api_permissions(db)
```

## Service 相互调用规则

### ✅ 允许的调用模式

| 场景 | 示例 | 条件 |
|------|------|------|
| **API → 同模块 Service** | `api_application.py` → `api_app_service` | ✅ 正常 |
| **API → 跨模块 Service** | `api_application.py` → `permission_service` | ✅ 允许 |
| **Service → Service** | `InboundService` → `InventoryService` | ⚠️ 谨慎 |

### 🚨 禁止的调用模式

| 违规行为 | 原因 |
|----------|------|
| **循环依赖** | 启动时导入错误 |
| **直接初始化注入** | 可能循环导入 |
| **频繁跨模块调用** | 应用领域事件 |

### 📖 最佳实践

```python
# ✅ 推荐：方法内部懒加载导入（避免循环依赖）
class InboundService(BaseService):
    async def confirm_inbound(self, db, inbound_id: int):
        await self.update(db, inbound_id, {"status": "confirmed"})

        # 延迟导入（避免启动时循环依赖）
        from src.app.warehousing.services import inventory_service
        await inventory_service.increase_stock(db, inbound_id)
```

## 模块导出原则（CRITICAL）

### 🚨 常见错误：ImportError

**原因**：模块的 `__init__.py` 没有导出新添加的类/函数。

### ✅ 正确的模块导出模式

```python
# ✅ 正确：同时导入和导出
from .user_service import UserService, user_service
from .role_service import RoleService, role_service

__all__ = [
    "UserService",
    "RoleService",
    "user_service",
    "role_service",
]
```

### 📋 检查清单

1. **导入语句**：添加 `from .xxx_service import XxxService, xxx_service`
2. **导出列表**：在 `__all__` 中添加类名和实例名
3. **一致性**：类名用 PascalCase，实例名用 snake_case

## 时区使用规则（CRITICAL）

### 🚨 Naive vs Aware Datetime

| 类型 | 时区信息 | 使用场景 |
|------|----------|----------|
| **Naive** | ❌ 无 | 数据库存储 |
| **Aware** | ✅ 有 | API 响应、时间戳计算 |

### 📦 时区工具方法

| 方法 | 返回类型 | 用途 |
|------|----------|------|
| `timezone.now_for_db()` | naive UTC datetime | 数据库存储 |
| `timezone.now_utc()` | aware UTC datetime | API 响应、时间戳计算 |
| `timezone.to_utc(timestamp)` | aware UTC datetime | Unix 时间戳转换 |

### ⚠️ 危险模式

```python
# ❌ 危险：对 naive datetime 调用 .timestamp()
dt_naive = datetime(2024, 1, 1, 12, 0, 0)
timestamp = dt_naive.timestamp()  # 🚨 假设系统本地时区！

# ✅ 正确：对 aware datetime 调用 .timestamp()
dt_aware = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
timestamp = dt_aware.timestamp()
```

### 📋 合规检查清单

- [ ] 数据库操作使用 `timezone.now_for_db()`
- [ ] API 响应使用 `timezone.now_utc().isoformat()`
- [ ] 时间戳计算使用 `timezone.now_utc().timestamp()`
- [ ] ❌ **从不**对 naive datetime 调用 `.timestamp()`
- [ ] 时间比较保持类型一致

## 性能优化规则

### Token vs 缓存策略

| 数据类型 | 存储位置 | TTL | 适用场景 |
|----------|----------|-----|----------|
| `is_superuser` | JWT Token | Token 期间 | 高频访问、状态稳定 |
| 用户权限集合 | Redis 缓存 | 5 分钟 | 低频访问、动态变化 |
| 业务数据 | Service 缓存 | 可配置 | 通用查询优化 |

**设计原则**：
1. **Token 存储**：高频使用、Token 期间稳定的状态
2. **缓存存储**：低频使用、动态变化的数据
3. **撤销策略**：Token 期间状态变化需强制重新登录