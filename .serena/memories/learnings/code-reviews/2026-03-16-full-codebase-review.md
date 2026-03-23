# WES Backend 代码库评审报告

## 审查日期
2026-03-16

## 审查范围
- 核心框架：`base_repository.py`, `base_service.py`, `base_api.py`
- 业务模块：`admin`, `auth`, `api_auth`, `device`, `workline`, `callback`, `sys`
- Mixin 系统：`src/core/mixins/`

---

## 发现的问题

### 🔴 严重问题：分层架构违规 (SRP 违反)

**问题描述**：Service 层直接使用 `select()` 和 `db.execute()` 查询数据库，违反了分层架构原则。

| 文件 | 方法 | 问题 |
|------|------|------|
| [auth_service.py](src/app/auth/services/auth_service.py) | `verify_user` | 直接 `select(User)` 查询用户 |
| [role_service.py](src/app/admin/services/role_service.py) | `_query_user_ids_by_role_id` | 直接查询 `user_role` 关联表 |
| [perm_service.py](src/app/admin/services/perm_service.py) | `_query_user_ids_by_permission_id` | 直接查询关联表 |
| [app_service.py](src/app/api_auth/services/app_service.py) | `_query_by_app_id` | 直接 `select(APIApplication)` |
| [app_service.py](src/app/api_auth/services/app_service.py) | `assign_permissions` | 直接操作 `api_app_permissions` 表 |
| [device_command_service.py](src/app/device/services/device_command_service.py) | `update_event_log` | 直接 `db.execute(update(DeviceEventLog))` |

**影响**：
- 违反单一职责原则 (SRP)
- Service 层承担了 Repository 层的职责
- 降低代码可测试性
- 增加维护成本

**重构建议**：
1. 将 `AuthService.verify_user` 中的用户查询逻辑迁移到 `UserRepository.get_by_username_with_roles()`
2. 将 `RoleService._query_user_ids_by_role_id` 迁移到 `RoleRepository.get_user_ids_by_role_id()`
3. 将 `PermissionService._query_user_ids_by_permission_id` 迁移到 `PermRepository.get_user_ids_by_permission_id()`
4. 将 `APIAppService._query_by_app_id` 迁移到 `APIAppRepository.get_by_app_id()`
5. 将 `APIAppService.assign_permissions` 中的关联表操作迁移到 `APIAppRepository`
6. 将 `DeviceCommandService.update_event_log` 迁移到 `DeviceEventLogRepository`

---

### 🟡 轻微问题：权限缓存失效代码重复 (DRY 轻微违反)

**问题描述**：`_invalidate_permissions_for_users` 方法在 `RoleService` 和 `PermissionService` 中重复。

```python
# RoleService 和 PermissionService 中相同代码
async def _invalidate_permissions_for_users(self, user_ids: set[int]) -> None:
    if not user_ids:
        return
    cache = get_cache()
    await invalidate_users_permissions(cache, user_ids)
```

**评估**：
- 每个 Service 只有 1 个调用点
- 提取到基类会增加间接层级
- 方法简单，重复成本低

**结论**：**保持现状** (YAGNI 原则 - 避免过度抽象)

---

## ✅ 正确的设计模式

### 1. API 层无直接数据库访问
经检查，所有 `v1/*.py` API 层文件均无 `select()` 或 `db.execute()` 直接调用，符合分层规范。

### 2. Mixin 组合正确
- `EnterpriseMixin` = `AuditableMixin` + `OptimisticLockMixin`
- 没有发现重复继承 `AuditMixin` 的问题
- 模型定义遵循正确的 Mixin 组合模式

### 3. 模型定义示例
```python
# ✅ 正确模式
class User(UserBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):
    __tablename__: Literal["users"] = "users"
    # ... 特有字段
```

### 4. Repository 层正常使用 SQLAlchemy
- `UserRepository`、`DeviceRepository`、`CommandRepository` 等均正确在 Repository 层使用 `select()` 和 `db.execute()`

---

## 代码质量评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构合规 | ⭐⭐⭐⭐☆ | Service 层有 6 处分层违规 |
| 代码复用 | ⭐⭐⭐⭐⭐ | BaseRepository/BaseService 避免大量 CRUD 重复 |
| 可读性 | ⭐⭐⭐⭐⭐ | 代码结构清晰，注释完善 |
| 可维护性 | ⭐⭐⭐⭐☆ | 分层违规会增加维护成本 |
| SOLID 原则 | ⭐⭐⭐⭐☆ | SRP 违反需要修复 |

---

## 重构优先级

| 优先级 | 模块 | 问题 | 影响 |
|--------|------|------|------|
| 🔴 P0 | `auth_service.py` | `verify_user` 直接查询数据库 | 认证核心逻辑，影响安全性和可测试性 |
| 🔴 P0 | `app_service.py` | `_query_by_app_id`, `assign_permissions` | API 认证核心逻辑 |
| 🟡 P1 | `role_service.py` | `_query_user_ids_by_role_id` | 角色变更时的权限缓存失效 |
| 🟡 P1 | `perm_service.py` | `_query_user_ids_by_permission_id` | 权限变更时的缓存失效 |
| 🟢 P2 | `device_command_service.py` | `update_event_log` 直接更新 | 设备事件日志更新 |

---

## TDD 重构策略

### 阶段 1：编写表征测试
1. 为每个需要重构的方法编写测试，锁定当前行为
2. 使用 Mock 隔离数据库依赖
3. 确保测试覆盖核心逻辑和边界情况

### 阶段 2：迁移到 Repository 层
1. 在对应 Repository 中添加新方法
2. 修改 Service 调用新 Repository 方法
3. 每次修改后运行测试确保行为不变

### 阶段 3：验证与清理
1. 确保所有测试通过
2. 删除 Service 层的数据库直接访问代码
3. 检查代码是否更符合 SOLID 原则

---

## 总结

**整体评价**：代码质量良好，框架设计合理，主要问题集中在 Service 层的分层违规。

**建议**：
1. 优先修复 `auth_service.py` 和 `app_service.py` 的分层违规（P0）
2. 后续修复 `role_service.py` 和 `perm_service.py`（P1）
3. 最后修复 `device_command_service.py`（P2）

**预期收益**：
- 提升代码可测试性
- 符合单一职责原则 (SRP)
- 降低维护成本

---

## 🎓 学习经验（2026-03-16 更新）

### 经验 1：`get_by_field` 不需要与 `get_by_id` 完全对齐

**问题**：`BaseRepository.get_by_field()` 是否应该支持 `get_by_id()` 的所有参数？

**分析**：
- `get_by_id()` 有 6 个参数：`id`, `schema`, `max_depth`, `include_relations`, `relation_names`, `include_deleted`
- `get_by_field()` 有 4 个参数：`field_name`, `value`, `relationships`, `include_deleted`

**结论**：**不需要完全对齐**，遵循 **KISS 原则**

| 功能 | `get_by_id` | `get_by_field` | 理由 |
|------|-------------|----------------|------|
| `include_deleted` | ✅ | ✅ | **必需** - 软删除表查询 |
| 关联加载 | `include_relations` + `relation_names` | `relationships: list[str]` | **设计差异** - `get_by_field` 更简洁 |
| 自动加载所有关联 | ✅ | ❌ | **不需要** - 通过 `relationships=[]` 可实现 |
| Schema 驱动加载 | ✅ (`schema` + `max_depth`) | ❌ | **不需要** - 违背简洁设计初衷 |

**设计理念**：
- `get_by_id` - 主键查询，支持复杂关联加载策略
- `get_by_field` - 通用字段查询，显式声明更符合 KISS 原则

**代码对比**：
```python
# 复杂场景：使用 get_by_id
user = await repo.get_by_id(
    db, user_id,
    schema=UserResponse,      # Schema 驱动
    include_relations=True,   # 自动加载所有
    max_depth=2
)

# 简单场景：使用 get_by_field
user = await repo.get_by_field(
    db, "username", "admin",
    relationships=["roles"]  # 显式声明
)
```

---

### 经验 2：Repository 单例模式的架构一致性

**问题发现**：`AuthService` 中多次 `UserRepository()` 破坏了单例模式设计。

**违规代码**：
```python
# ❌ auth_service.py (3 处违规)
user_repo = UserRepository()
user = await user_repo.get_by_username_with_roles(db, username)
```

**正确模式**（参考 `UserService`, `RoleService`）：
```python
# ✅ user_service.py
from src.app.admin.repositories.user_repository import user_repository

class UserService(BaseService[User, UserRepository]):
    def __init__(
        self,
        user_repo: UserRepository = user_repository,  # 依赖注入
    ):
        super().__init__(user_repo, ...)
```

**修复方案**：
```python
# ✅ auth_service.py (修复后)
from src.app.admin.repositories.user_repository import user_repository

class AuthService:
    @staticmethod
    async def verify_user(db: AsyncSession, username: str, password: str) -> User:
        # 直接使用单例
        user = await user_repository.get_by_username_with_roles(db, username)
```

**影响分析**：
- ❌ **破坏单例设计**：每次调用都创建新实例
- ❌ **资源浪费**：重复初始化 `BaseRepository` 内部状态（`RelationManager`）
- ❌ **架构不一致**：`UserService`、`RoleService` 正确使用单例，但 `AuthService` 没有

**架构一致性检查命令**：
```bash
# 检查违规实例化
grep -rn "Repository()" src/app/ --include="*.py"

# 验证单例使用
grep -rn "user_repository" src/app/auth/services/auth_service.py
```

**遵循的原则**：
- **DRY**：避免重复创建 `UserRepository` 实例
- **SOLID**：依赖注入模式，单例作为全局共享资源
- **一致性**：所有 Service 遵循相同的 Repository 使用模式

---

### 经验 3：架构一致性比局部优化更重要

**启示**：
- 单个文件的优化（如 `AuthService` 的静态方法）不能违背整体架构设计
- 单例模式的意义在于 **资源复用** 和 **行为一致性**
- 代码审查应关注 **跨模块的模式一致性**，而非单一文件的局部正确性

**检查清单**：
- [ ] Repository 实例化是否统一使用单例？
- [ ] Service 层是否都遵循依赖注入模式？
- [ ] 是否存在 `XxxRepository()` 直接实例化？
- [ ] 跨模块的设计模式是否一致？

---

## 修复记录（2026-03-16）

### 已完成

1. ✅ **`auth_service.py` 单例模式修复**
   - 修复 3 处 `UserRepository()` 违规（97、219、527 行）
   - 更新导入：`from ... import user_repository`
   - 更新测试：`test_verify_user_characterization.py`
   - 测试通过：6/6

2. ✅ **`role_service.py` 和 `perm_service.py` 重构**
   - 新增 `RoleRepository.get_user_ids_by_role_id()`
   - 新增 `PermRepository.get_user_ids_by_permission_id()`
   - Service 层委托给 Repository
   - 测试通过：8/8

3. ✅ **`BaseRepository.get_by_field` 增强**
   - 新增 `relationships` 参数支持关联预加载
   - 新增 `include_deleted` 参数支持软删除过滤
   - 简化 `UserRepository` 代码复用增强方法

### 待修复

4. ⏳ **`app_service.py` 重构** (P0)
   - `_query_by_app_id` → `APIAppRepository.get_by_app_id()`
   - `assign_permissions` → `APIAppRepository.assign_permissions()`

5. ⏳ **`device_command_service.py` 重构** (P2)
   - `update_event_log` → `DeviceEventLogRepository.update_event_log()`